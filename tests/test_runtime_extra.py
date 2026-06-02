"""Additional Layer-0 tests: edge cases and white-box behaviours not covered by
the main runtime suite (policy evaluation, scheduler cron re-queue, write-worker
error propagation, vault key mismatch, event-bus timeout, store idempotence)."""

import asyncio
import time

import pytest

from agentwright.primitives.permission import (
    ActionOverride,
    ActionPolicy,
    OwnerConfig,
    PermissionConfig,
)
from agentwright.runtime.document_store import WriteWorker
from agentwright.runtime.event_bus import EventBus
from agentwright.runtime.file_store import FileStore
from agentwright.runtime.kv_store import KVStore
from agentwright.runtime.memory_backends import (
    InMemoryPreferenceStore,
    InMemoryVectorStore,
)
from agentwright.runtime.policy import evaluate
from agentwright.runtime.scheduler import Scheduler
from agentwright.runtime.shell_runner import ShellRunner
from agentwright.runtime.vault import Vault, generate_master_key


# --- policy evaluation ---------------------------------------------------


def _perm(default="require_approval", overrides=None):
    return PermissionConfig(
        owner=OwnerConfig(user_id="u"),
        action_policy=ActionPolicy(default=default, overrides=overrides or []),
    )


def test_policy_default_allow():
    d = evaluate(_perm(default="allow"), "gmail.read")
    assert d.allowed and not d.requires_approval


def test_policy_default_require_approval():
    d = evaluate(_perm(default="require_approval"), "gmail.send")
    assert not d.allowed and d.requires_approval


def test_policy_override_deny_wins():
    perm = _perm(default="allow", overrides=[
        ActionOverride(action_pattern="crm.delete_*", policy="deny")
    ])
    assert not evaluate(perm, "crm.delete_contact").allowed
    assert evaluate(perm, "crm.update").allowed  # default applies otherwise


def test_policy_override_require_approval_carries_approvers():
    perm = _perm(default="allow", overrides=[
        ActionOverride(action_pattern="gmail.send", policy="require_approval",
                       approvers=["usr_mgr"])
    ])
    d = evaluate(perm, "gmail.send")
    assert d.requires_approval and d.approvers == ["usr_mgr"]


def test_policy_first_matching_override_wins():
    perm = _perm(default="deny", overrides=[
        ActionOverride(action_pattern="a.*", policy="allow"),
        ActionOverride(action_pattern="a.danger", policy="deny"),
    ])
    # First pattern matches first -> allow, even though a later one would deny.
    assert evaluate(perm, "a.danger").allowed


# --- scheduler cron re-queue (white-box) ---------------------------------


def test_scheduler_cron_requeues_after_firing():
    async def go():
        bus = EventBus()
        sched = Scheduler(bus)
        sched._enqueue(time.time() + 0.01,
                       {"job_id": "c", "agent_id": "a", "cron": "* * * * *", "timezone": "UTC"})
        job = await sched._fire_due()      # fires onto the bus
        fired = await bus.consume("trigger.fired", timeout=1)
        return job, fired, sched._queue.qsize()

    job, fired, qsize = asyncio.run(go())
    assert job["job_id"] == "c" and fired["job_id"] == "c"
    assert qsize == 1  # the cron job was re-queued for its next occurrence


def test_scheduler_cancelled_job_does_not_fire():
    async def go():
        bus = EventBus()
        sched = Scheduler(bus)
        sched._enqueue(time.time(), {"job_id": "x", "agent_id": "a"})
        sched.cancel("x")
        return await sched._fire_due()  # returns None for a cancelled job

    assert asyncio.run(go()) is None


# --- write worker error propagation --------------------------------------


def test_write_worker_propagates_exception():
    async def go():
        worker = WriteWorker()
        worker.start()

        def boom():
            raise ValueError("write failed")

        try:
            await worker.submit(boom)
            return "no-raise"
        except ValueError as exc:
            return str(exc)
        finally:
            await worker.stop()

    assert asyncio.run(go()) == "write failed"


# --- vault key mismatch --------------------------------------------------


def test_vault_wrong_master_key_cannot_decrypt(tmp_path):
    path = tmp_path / "v.bin"
    Vault(vault_path=path, master_key=generate_master_key()).set("a", "1")
    with pytest.raises(Exception):  # cryptography raises InvalidToken on bad key
        Vault(vault_path=path, master_key=generate_master_key())


# --- event bus timeout ---------------------------------------------------


def test_event_bus_consume_times_out():
    async def go():
        try:
            await EventBus().consume("never", timeout=0.05)
            return "no-timeout"
        except asyncio.TimeoutError:
            return "timeout"

    assert asyncio.run(go()) == "timeout"


# --- store idempotence / prefixes ----------------------------------------


def test_kv_open_agent_is_idempotent(tmp_path):
    kv = KVStore(tmp_path)
    kv.open_agent("a", map_size_mb=8)
    kv.open_agent("a")  # second call is a no-op, keeps the same env
    kv.put("a", "k", b"v")
    assert kv.get("a", "k") == b"v"
    kv.close()


def test_file_store_list_with_prefix(tmp_path):
    fs = FileStore(tmp_path)
    fs.init_agent("a")
    fs.write("a", "artifacts/sub/x.txt", "1")
    fs.write("a", "history/h.md", "2")
    assert fs.list("a", "artifacts") == ["artifacts/sub/x.txt"]
    assert set(fs.list("a")) == {"artifacts/sub/x.txt", "history/h.md"}


def test_shell_runner_reports_nonzero_exit(tmp_path):
    r = ShellRunner().exec("exit 3", tmp_path)
    assert r.exit_code == 3 and not r.timed_out and not r.oom_killed


# --- event bus concurrent approvals --------------------------------------


def test_event_bus_request_approval_requeues_foreign_resolution():
    # A resolution for a different approval_id must be re-queued, not consumed,
    # so request_approval only returns its own answer (the re-queue branch).
    async def go():
        bus = EventBus()
        # Foreign resolution is enqueued ahead of ours.
        await bus.publish("approval.resolved", {"approval_id": "other", "ok": "no"})
        await bus.publish("approval.resolved", {"approval_id": "mine", "ok": "yes"})
        return await bus.request_approval({"approval_id": "mine"}, timeout=1)

    assert asyncio.run(go())["ok"] == "yes"


# --- vault key resolution paths ------------------------------------------


def test_vault_master_key_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_RUNTIME_MASTER_KEY", generate_master_key())
    path = tmp_path / "v.bin"
    Vault(vault_path=path).set("s", "secret")  # key resolved from env var
    assert Vault(vault_path=path).get("s") == "secret"


def test_vault_master_key_from_file(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_RUNTIME_MASTER_KEY", raising=False)
    key_file = tmp_path / "master.key"
    key_file.write_text(generate_master_key())
    path = tmp_path / "v.bin"
    Vault(vault_path=path, key_file=key_file).set("s", "secret")  # key from file
    assert Vault(vault_path=path, key_file=key_file).get("s") == "secret"


# --- kv store lazy open / prefix boundary --------------------------------


def test_kv_lazy_opens_env_on_access(tmp_path):
    kv = KVStore(tmp_path)
    kv.put("fresh", "k", b"v")  # no explicit open_agent -> _env lazily opens
    assert kv.get("fresh", "k") == b"v"
    kv.close()


def test_kv_scan_prefix_stops_at_boundary(tmp_path):
    kv = KVStore(tmp_path)
    kv.put("a", "p:1", b"1")
    kv.put("a", "p:2", b"2")
    kv.put("a", "q:1", b"3")  # sorts after the "p:" range -> triggers the break
    out = kv.scan_prefix("a", "p:")
    assert [k for k, _ in out] == ["p:1", "p:2"]
    kv.close()


# --- file store missing prefix -------------------------------------------


def test_file_store_list_missing_prefix_is_empty(tmp_path):
    fs = FileStore(tmp_path)
    fs.init_agent("a")
    assert fs.list("a", "does/not/exist") == []


# --- in-memory store edge cases ------------------------------------------


def test_vector_store_delete_removes_doc():
    vs = InMemoryVectorStore()
    vs.upsert("a", "d1", "hello world", {})
    vs.delete("a", "d1")
    assert vs.query("a", "hello", top_k=5) == []
    vs.delete("a", "missing")  # deleting an absent doc is a no-op, not an error


def test_preference_store_skips_empty_messages():
    ps = InMemoryPreferenceStore()
    ps.add("a", [{"content": ""}, {"content": "remember this"}])
    mems = ps.get_all("a")
    assert len(mems) == 1 and mems[0].memory == "remember this"
