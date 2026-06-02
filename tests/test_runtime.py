"""Tests for Layer 0: the runtime infrastructure services.

Async services (EventBus, Scheduler, WriteWorker) are tested by driving them
through ``asyncio.run`` inside sync test functions, so no pytest-asyncio
dependency is needed.
"""

import asyncio

import pytest

from agentwright.runtime.document_store import DocumentStore, WriteWorker
from agentwright.runtime.event_bus import EventBus
from agentwright.runtime.file_store import FileStore
from agentwright.runtime.kv_store import KVStore
from agentwright.runtime.llm_gateway import FakeLLMGateway
from agentwright.runtime.memory_backends import (
    InMemoryGraphStore,
    InMemoryPreferenceStore,
    InMemoryUniversalStore,
    InMemoryVectorStore,
)
from agentwright.runtime.scheduler import Scheduler, next_cron
from agentwright.runtime.shell_runner import ShellRunner
from agentwright.runtime.system_db import SystemDB
from agentwright.runtime.vault import Vault, VaultError, generate_master_key


# --- SystemDB ------------------------------------------------------------


def test_system_db_run_lifecycle_and_cost():
    db = SystemDB(":memory:")
    run_id = db.start_run("agt_1", "trig_1")
    assert db.get_run(run_id)["status"] == "running"
    db.record_cost("agt_1", run_id, "anthropic", "claude-opus-4-8", 100, 50, 0.01)
    db.record_cost("agt_1", run_id, "anthropic", "claude-opus-4-8", 10, 5, 0.002)
    assert db.run_cost(run_id) == pytest.approx(0.012)
    db.end_run(run_id, "done", "done", 0.012, 165)
    assert db.get_run(run_id)["status"] == "done"
    assert len(db.list_runs("agt_1")) == 1


def test_system_db_action_audit():
    db = SystemDB(":memory:")
    run_id = db.start_run("agt_1")
    db.record_action("agt_1", run_id, "gmail.send", '{"to":"x"}', "success", 0.0)
    actions = db.list_actions(run_id)
    assert len(actions) == 1 and actions[0]["outcome"] == "success"


def test_system_db_end_unknown_run_raises():
    db = SystemDB(":memory:")
    with pytest.raises(KeyError):
        db.end_run("run_nope", "done")


# --- FileStore -----------------------------------------------------------


def test_file_store_write_read_list(tmp_path):
    fs = FileStore(tmp_path)
    fs.init_agent("agt_1")
    fs.write("agt_1", "artifacts/report.md", "hello")
    assert fs.read("agt_1", "artifacts/report.md") == "hello"
    assert "artifacts/report.md" in fs.list("agt_1")
    assert fs.exists("agt_1", "artifacts/report.md")
    fs.delete("agt_1", "artifacts/report.md")
    assert not fs.exists("agt_1", "artifacts/report.md")


def test_file_store_blocks_path_traversal(tmp_path):
    fs = FileStore(tmp_path)
    fs.init_agent("agt_1")
    with pytest.raises(ValueError):
        fs.write("agt_1", "../../escape.txt", "x")


# --- Vault ---------------------------------------------------------------


def test_vault_roundtrip_across_reopen(tmp_path):
    key = generate_master_key()
    path = tmp_path / "vault.bin"
    v = Vault(vault_path=path, master_key=key)
    v.set("cred_gmail", "ya29.tok")
    # Re-open with same key -> still readable; ciphertext on disk.
    v2 = Vault(vault_path=path, master_key=key)
    assert v2.get("cred_gmail") == "ya29.tok"
    assert b"ya29.tok" not in path.read_bytes()  # encrypted at rest


def test_vault_missing_key_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_RUNTIME_MASTER_KEY", raising=False)
    with pytest.raises(VaultError):
        Vault(vault_path=tmp_path / "v.bin", key_file=tmp_path / "absent.key")


def test_vault_delete_and_rotate(tmp_path):
    k1 = generate_master_key()
    path = tmp_path / "vault.bin"
    v = Vault(vault_path=path, master_key=k1)
    v.set("a", "1")
    v.set("b", "2")
    v.delete("a")
    assert not v.exists("a") and v.get("b") == "2"
    k2 = generate_master_key()
    v.rotate_key(k2)
    assert Vault(vault_path=path, master_key=k2).get("b") == "2"


# --- KVStore (LMDB) ------------------------------------------------------


def test_kv_store_put_get_prefix(tmp_path):
    kv = KVStore(tmp_path)
    kv.open_agent("agt_1")
    kv.put("agt_1", "processed:leads:1", b"1")
    kv.put("agt_1", "processed:leads:2", b"1")
    kv.put("agt_1", "cursor:gmail", b"42")
    assert kv.get("agt_1", "processed:leads:1") == b"1"
    assert kv.exists("agt_1", "cursor:gmail")
    assert kv.count_prefix("agt_1", "processed:") == 2
    kv.delete("agt_1", "processed:leads:1")
    assert kv.count_prefix("agt_1", "processed:") == 1
    kv.close()


# --- DocumentStore (TinyDB) ----------------------------------------------


def test_document_store_crud(tmp_path):
    ds = DocumentStore(tmp_path)
    ds.init_agent("agt_1")
    doc_id = ds.insert("agt_1", "states", {"step": 1, "name": "a"})
    assert ds.get("agt_1", "states", doc_id)["step"] == 1
    ds.insert("agt_1", "states", {"step": 2, "name": "b"})
    assert len(ds.all("agt_1", "states")) == 2
    assert ds.search("agt_1", "states", "name", "b")[0]["step"] == 2
    ds.update("agt_1", "states", {"step": 99}, "name", "a")
    assert ds.search("agt_1", "states", "name", "a")[0]["step"] == 99
    ds.remove("agt_1", "states", "name", "a")
    assert len(ds.all("agt_1", "states")) == 1
    ds.close()


# --- ShellRunner ---------------------------------------------------------


def test_shell_runner_runs_command(tmp_path):
    r = ShellRunner().exec("echo hello", tmp_path)
    assert r.exit_code == 0 and r.stdout.strip() == "hello"
    assert not r.timed_out


def test_shell_runner_allowlist_blocks(tmp_path):
    r = ShellRunner().exec("rm -rf x", tmp_path, allowed_commands=["echo", "ls"])
    assert r.exit_code == 126 and "not allowed" in r.stderr


def test_shell_runner_timeout(tmp_path):
    r = ShellRunner().exec("sleep 5", tmp_path, timeout_seconds=1)
    assert r.timed_out and r.exit_code == 124


# --- EventBus (async) ----------------------------------------------------


def test_event_bus_publish_consume():
    async def go():
        bus = EventBus()
        await bus.publish("trigger.fired", {"job_id": "j1"})
        return await bus.consume("trigger.fired", timeout=1)

    assert asyncio.run(go())["job_id"] == "j1"


def test_event_bus_approval_roundtrip():
    async def go():
        bus = EventBus()

        async def approver():
            req = await bus.consume("approval.requested", timeout=1)
            await bus.publish(
                "approval.resolved",
                {"approval_id": req["approval_id"], "decision": "approved"},
            )

        asyncio.create_task(approver())
        return await bus.request_approval({"approval_id": "ap_1"}, timeout=1)

    assert asyncio.run(go())["decision"] == "approved"


# --- Scheduler (async) ---------------------------------------------------


def test_next_cron_is_in_future():
    import time

    assert next_cron("0 9 * * *") > time.time()


def test_scheduler_fires_delayed_job():
    async def go():
        bus = EventBus()
        sched = Scheduler(bus)
        sched.start()
        sched.add_delayed("j1", 0.05, "agt_1", "do it")
        ev = await bus.consume("trigger.fired", timeout=2)
        await sched.stop()
        return ev

    assert asyncio.run(go())["job_id"] == "j1"


def test_scheduler_list_and_cancel():
    bus = EventBus()
    sched = Scheduler(bus)
    sched.add_cron("j1", "0 9 * * *", "UTC", "agt_1", "t")
    sched.add_cron("j2", "0 10 * * *", "UTC", "agt_2", "t")
    assert {j["job_id"] for j in sched.list_jobs()} == {"j1", "j2"}
    assert [j["job_id"] for j in sched.list_jobs("agt_1")] == ["j1"]
    sched.cancel("j1")
    assert [j["job_id"] for j in sched.list_jobs()] == ["j2"]


# --- WriteWorker (async) -------------------------------------------------


def test_write_worker_serializes_writes(tmp_path):
    async def go():
        ds = DocumentStore(tmp_path)
        ds.init_agent("agt_1")
        worker = WriteWorker()
        worker.start()
        ids = []
        for i in range(5):
            doc_id = await worker.submit(lambda i=i: ds.insert("agt_1", "states", {"n": i}))
            ids.append(doc_id)
        await worker.stop()
        ds.close()
        return ids

    ids = asyncio.run(go())
    assert len(set(ids)) == 5  # all distinct -> no lost/clobbered writes


# --- Tier B in-memory backends ------------------------------------------


def test_vector_store_ranks_by_similarity():
    vs = InMemoryVectorStore()
    vs.upsert("a", "d1", "bob works at acme corporation", {})
    vs.upsert("a", "d2", "the weather is sunny and warm", {})
    hits = vs.query("a", "acme company employee", top_k=2)
    assert hits[0].id == "d1"
    assert hits[0].score >= hits[1].score


def test_vector_store_filters_metadata():
    vs = InMemoryVectorStore()
    vs.upsert("a", "d1", "alpha", {"kind": "x"})
    vs.upsert("a", "d2", "alpha", {"kind": "y"})
    hits = vs.query("a", "alpha", top_k=5, filters={"kind": "y"})
    assert [h.id for h in hits] == ["d2"]


def test_graph_store_bounded_traversal():
    gs = InMemoryGraphStore()
    gs.upsert_node("a", "Company", "acme", {})
    gs.upsert_node("a", "Person", "bob", {})
    gs.upsert_node("a", "Topic", "ml", {})
    gs.upsert_edge("a", "employs", "acme", "bob", {})
    gs.upsert_edge("a", "authored", "bob", "ml", {})
    assert set(gs.neighbors("a", "acme", None, depth=1)) == {"bob"}
    assert set(gs.neighbors("a", "acme", None, depth=2)) == {"bob", "ml"}
    assert set(gs.neighbors("a", "acme", "employs", depth=2)) == {"bob"}


def test_universal_store_tables_and_state():
    us = InMemoryUniversalStore()
    us.create("a", "processed_leads", {"email": "bob@acme.com"})
    assert us.select("a", "processed_leads")[0]["email"] == "bob@acme.com"
    us.write_state("a", "cursor", 42)
    assert us.read_state("a", "cursor") == 42
    assert us.read_state("a", "missing") is None


def test_preference_store_search_by_overlap():
    ps = InMemoryPreferenceStore()
    ps.add("a", [{"content": "user prefers weekly digest not daily"}])
    ps.add("a", [{"content": "user dislikes phone calls"}])
    hits = ps.search("a", "how often digest", limit=5)
    assert hits and "digest" in hits[0].memory
    assert len(ps.get_all("a")) == 2


# --- FakeLLMGateway ------------------------------------------------------


def test_fake_gateway_records_cost_to_ledger():
    db = SystemDB(":memory:")
    run_id = db.start_run("agt_1")
    gw = FakeLLMGateway(db)
    res = gw.complete(
        [{"role": "user", "content": "hello there friend"}],
        "claude-opus-4-8", 1024, 0.2, None, 2.0, run_id,
    )
    assert res.stop_reason == "done"
    assert db.run_cost(run_id) == pytest.approx(res.cost_usd)


def test_fake_gateway_enforces_budget():
    db = SystemDB(":memory:")
    run_id = db.start_run("agt_1")
    res = FakeLLMGateway(db).complete(
        [{"role": "user", "content": "x " * 5000}],
        "claude-opus-4-8", 1024, 0.2, None, 0.0001, run_id,
    )
    assert res.stop_reason == "budget_exceeded"
    assert db.run_cost(run_id) == 0.0  # nothing billed
