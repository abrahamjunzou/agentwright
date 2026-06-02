"""Tests for the AgentRuntime facade and per-agent instantiation sequence."""

import pytest

from agentwright import DomainBrief, compose
from agentwright.runtime import AgentRuntime
from agentwright.runtime.runtime import RuntimeError_


def _runtime(tmp_path):
    return AgentRuntime(tmp_path)


def test_instantiate_provisions_stores_for_long_lived_agent(tmp_path):
    d = compose(
        DomainBrief(
            name="A", goal="g", domain="d", long_lived=True, available_connections=["gmail"]
        ),
        "usr_1",
        "usr_1",
    )
    rt = _runtime(tmp_path)
    agent_id = rt.instantiate(d)
    assert agent_id == d.agent_id
    # File store dirs + LMDB env exist; document store usable.
    assert rt.file_store.exists(agent_id, "workspace")
    rt.kv_store.put(agent_id, "k", b"v")
    assert rt.kv_store.get(agent_id, "k") == b"v"
    doc_id = rt.document_store.insert(agent_id, "states", {"x": 1})
    assert rt.document_store.get(agent_id, "states", doc_id)["x"] == 1
    rt.close()


def test_instantiate_schedules_trigger(tmp_path):
    d = compose(
        DomainBrief(name="A", goal="g", domain="d", triggers_needed=["schedule"]),
        "usr_1",
        "usr_1",
    )
    # configurator inserts a placeholder cron, so the schedule job is real.
    rt = _runtime(tmp_path)
    agent_id = rt.instantiate(d)
    jobs = rt.scheduler.list_jobs(agent_id)
    assert len(jobs) == 1
    assert jobs[0]["job_id"] == f"{agent_id}:schedule_trigger"
    rt.close()


def test_instantiate_rejects_vm(tmp_path):
    # Build a definition that slips past compose by mutating after the fact.
    d = compose(DomainBrief(name="A", goal="g", domain="d", available_compute=["shell"]),
                "usr_1", "usr_1")
    d.primitives.compute.vm.enabled = True  # force the infra gap
    rt = _runtime(tmp_path)
    with pytest.raises(RuntimeError_):
        rt.instantiate(d)
    rt.close()


def test_instantiate_minimal_agent_has_no_memory_stores(tmp_path):
    d = compose(DomainBrief(name="A", goal="g", domain="d"), "usr_1", "usr_1")
    rt = _runtime(tmp_path)
    agent_id = rt.instantiate(d)
    # No memory primitive -> no tinydb tables created for it, but LMDB (always
    # active) and the file store are provisioned.
    assert rt.file_store.exists(agent_id, "traces")
    rt.kv_store.put(agent_id, "k", b"v")
    assert rt.kv_store.exists(agent_id, "k")
    rt.close()


def test_full_run_lifecycle_through_runtime(tmp_path):
    d = compose(DomainBrief(name="A", goal="g", domain="d", long_lived=True), "usr_1", "usr_1")
    rt = _runtime(tmp_path)
    agent_id = rt.instantiate(d)

    run_id = rt.system_db.start_run(agent_id)
    res = rt.llm_gateway.complete(
        [{"role": "user", "content": "do the task"}],
        d.primitives.reasoning_loop.model.model_id, 1024, 0.2, None,
        d.primitives.reasoning_loop.cost_controls.max_cost_per_run_usd, run_id,
    )
    rt.system_db.record_action(agent_id, run_id, "tool.x", "{}", "success", 0.0)
    rt.system_db.end_run(run_id, "done", res.stop_reason, res.cost_usd, 0)

    assert rt.system_db.get_run(run_id)["status"] == "done"
    assert rt.system_db.run_cost(run_id) == pytest.approx(res.cost_usd)
    assert len(rt.system_db.list_actions(run_id)) == 1
    rt.close()
