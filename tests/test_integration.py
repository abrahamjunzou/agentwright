"""Cross-layer integration tests (Layer 0 <-> Layer 1 <-> Layer 2).

Where the per-layer suites test units in isolation, these drive realistic flows
end to end: a domain brief is composed into a definition (L2), validated against
the primitive contracts (L1), instantiated on the runtime, and then exercised
through the real Layer-0 services (system DB, scheduler, event bus, vault, KV,
vector/universal stores, LLM gateway).

Async services are driven via ``asyncio.run`` inside sync tests (no
pytest-asyncio dependency).
"""

import asyncio

import pytest

from agentwright import (
    BriefConstraints,
    DomainBrief,
    InstanceRegistry,
    compose,
)
from agentwright.orchestration.agent_definition import AgentDefinition
from agentwright.runtime import AgentRuntime, Vault, generate_master_key
from agentwright.runtime.runtime import RuntimeError_


def _lead_brief(**overrides) -> DomainBrief:
    base = dict(
        name="Daily Lead Enrichment Agent",
        goal="enrich inbound leads, update CRM, post a summary",
        domain="sales",
        constraints=BriefConstraints(
            cost_per_run_usd_max=2.0,
            requires_human_approval_for=["crm.delete_contact"],
            pii_handling="redact",
            data_retention_days=90,
        ),
        available_connections=["gmail", "hubspot", "slack", "clearbit_api"],
        triggers_needed=["schedule"],
        long_lived=True,
    )
    base.update(overrides)
    return DomainBrief(**base)


# --- 1. Full lifecycle: brief -> compose -> instantiate -> run -----------


def test_full_lead_enrichment_lifecycle(tmp_path):
    # L2: compose + validate
    d = compose(_lead_brief(), owner_id="usr_1", created_by="usr_1")
    assert d.status == "validated", d.validation_errors

    # L0: instantiate the definition's stores
    rt = AgentRuntime(tmp_path)
    agent_id = rt.instantiate(d)
    assert rt.scheduler.list_jobs(agent_id)  # schedule trigger -> scheduler job

    # A run exercises every store the agent uses.
    run_id = rt.system_db.start_run(agent_id, "schedule_trigger")

    # dedup via LMDB + structured state via universal store
    rt.kv_store.put(agent_id, "processed:leads:bob@acme.com", b"1")
    assert rt.kv_store.count_prefix(agent_id, "processed:") == 1
    rt.universal_store.create(agent_id, "processed_leads", {"email": "bob@acme.com"})
    assert rt.universal_store.select(agent_id, "processed_leads")[0]["email"] == "bob@acme.com"

    # semantic recall via vector store
    rt.vector_store.upsert(agent_id, "lead1", "bob smith vp sales at acme corp", {})
    rt.vector_store.upsert(agent_id, "lead2", "weather forecast sunny", {})
    assert rt.vector_store.query(agent_id, "acme sales executive", top_k=1)[0].id == "lead1"

    # costed LLM call + audited tool action
    res = rt.llm_gateway.complete(
        [{"role": "user", "content": "enrich bob@acme.com"}],
        d.primitives.reasoning_loop.model.model_id, 1024, 0.2, None,
        d.primitives.reasoning_loop.cost_controls.max_cost_per_run_usd, run_id,
    )
    rt.system_db.record_action(agent_id, run_id, "hubspot.update_contact", "{}", "success", 0.0)
    rt.system_db.end_run(run_id, "done", res.stop_reason, res.cost_usd, 0)

    run = rt.system_db.get_run(run_id)
    assert run["status"] == "done" and run["stop_reason"] == "done"
    assert rt.system_db.run_cost(run_id) == pytest.approx(res.cost_usd)
    assert len(rt.system_db.list_actions(run_id)) == 1
    rt.close()


# --- 2. L2 registry and L0 system DB share one system.db -----------------


def test_registry_and_runtime_share_system_db(tmp_path):
    db_path = tmp_path / "db" / "system.db"
    rt = AgentRuntime(tmp_path)  # opens db/system.db (creates agents table)

    d = compose(_lead_brief(), "usr_1", "usr_1")
    registry = InstanceRegistry(db_path)  # same file, agents table
    registry.create(d)
    registry.update_status(d.agent_id, "active")

    # L0 records runs for the same agent in the same file.
    run_id = rt.system_db.start_run(d.agent_id)
    rt.system_db.end_run(run_id, "done")

    # Both views coexist: registry sees the agent, system DB sees its run.
    assert registry.get(d.agent_id).status == "active"
    assert rt.system_db.list_runs(d.agent_id)[0]["status"] == "done"
    rt.close()


# --- 3. trigger (L1) -> scheduler -> event bus -> run dispatch (L0) -------


def test_trigger_fires_and_dispatches_run(tmp_path):
    async def go():
        d = compose(_lead_brief(), "usr_1", "usr_1")
        rt = AgentRuntime(tmp_path)
        agent_id = rt.instantiate(d)
        rt.scheduler.start()
        # The configured cron is far off; simulate an imminent firing of the
        # same agent's trigger to drive the dispatch path.
        rt.scheduler.add_delayed(f"{agent_id}:schedule_trigger", 0.05, agent_id, "Run it")

        fired = await rt.event_bus.consume("trigger.fired", timeout=2)
        run_id = rt.system_db.start_run(fired["agent_id"], fired["job_id"])
        rt.system_db.end_run(run_id, "done")

        run_status = rt.system_db.get_run(run_id)["status"]
        await rt.scheduler.stop()
        rt.close()
        return fired["agent_id"], run_status

    agent_id, run_status = asyncio.run(go())
    assert agent_id.startswith("agt_")
    assert run_status == "done"


# --- 4. permission policy (L1) -> approval flow over event bus (L0) -------


def test_permission_approval_flow_end_to_end(tmp_path):
    d = compose(_lead_brief(), "usr_1", "usr_1")
    # The brief's approval pattern became a require_approval override (L1).
    overrides = d.primitives.permission.action_policy.overrides
    assert any(o.action_pattern == "crm.delete_contact" and o.policy == "require_approval"
               for o in overrides)

    rt = AgentRuntime(tmp_path)
    agent_id = rt.instantiate(d)
    run_id = rt.system_db.start_run(agent_id)

    async def go():
        async def approver():
            req = await rt.event_bus.consume("approval.requested", timeout=1)
            await rt.event_bus.publish(
                "approval.resolved",
                {"approval_id": req["approval_id"], "decision": "approved",
                 "reviewer_id": "usr_manager"},
            )

        asyncio.create_task(approver())
        return await rt.event_bus.request_approval(
            {"approval_id": "ap_1", "agent_id": agent_id, "action": "crm.delete_contact"},
            timeout=1,
        )

    decision = asyncio.run(go())
    assert decision["decision"] == "approved"

    outcome = "success" if decision["decision"] == "approved" else "denied"
    rt.system_db.record_action(agent_id, run_id, "crm.delete_contact", "{}", outcome, 0.0)
    assert rt.system_db.list_actions(run_id)[0]["outcome"] == "success"
    rt.close()


# --- 5. tool_connection credential_ref (L1) -> vault (L0) -----------------


def test_vault_resolves_tool_connection_credential(tmp_path):
    vault = Vault(vault_path=tmp_path / "vault.bin", master_key=generate_master_key())
    rt = AgentRuntime(tmp_path, vault=vault)

    d = compose(_lead_brief(), "usr_1", "usr_1")
    # A human fills in the credential ref the configurator flagged, and stores
    # the secret in the vault.
    conn = d.primitives.tool_connection.connections[0]
    conn.auth.credential_ref = "cred_gmail_usr1"
    rt.vault.set("cred_gmail_usr1", "ya29.secret-oauth-token")

    rt.instantiate(d)
    # At runtime, tool_connection resolves the ref through the vault.
    assert rt.vault.get(conn.auth.credential_ref) == "ya29.secret-oauth-token"
    rt.close()


# --- 6. cost budget (invariant 8) enforced at runtime --------------------


def test_cost_budget_exceeded_end_to_end(tmp_path):
    # Tiny budget; invariant 8 keeps reasoning cap == permission cap so it still
    # validates, then the gateway trips the budget at runtime.
    d = compose(
        _lead_brief(constraints=BriefConstraints(cost_per_run_usd_max=0.0001)),
        "usr_1", "usr_1",
    )
    assert d.status == "validated", d.validation_errors

    rt = AgentRuntime(tmp_path)
    agent_id = rt.instantiate(d)
    run_id = rt.system_db.start_run(agent_id)

    res = rt.llm_gateway.complete(
        [{"role": "user", "content": "word " * 300}],  # enough tokens to exceed
        d.primitives.reasoning_loop.model.model_id, 1024, 0.2, None,
        d.primitives.reasoning_loop.cost_controls.max_cost_per_run_usd, run_id,
    )
    assert res.stop_reason == "budget_exceeded"
    rt.system_db.end_run(run_id, "failed", res.stop_reason)
    assert rt.system_db.get_run(run_id)["stop_reason"] == "budget_exceeded"
    assert rt.system_db.run_cost(run_id) == 0.0  # nothing billed
    rt.close()


# --- 7. multi-agent store isolation --------------------------------------


def test_multi_agent_store_isolation(tmp_path):
    rt = AgentRuntime(tmp_path)
    a = rt.instantiate(compose(_lead_brief(name="Agent A"), "usr_1", "usr_1"))
    b = rt.instantiate(compose(_lead_brief(name="Agent B"), "usr_1", "usr_1"))
    assert a != b

    rt.kv_store.put(a, "secret", b"a-only")
    rt.vector_store.upsert(a, "d1", "confidential alpha note", {})
    rt.file_store.write(a, "artifacts/x.txt", "A data")

    # B sees none of A's data.
    assert rt.kv_store.get(b, "secret") is None
    assert rt.vector_store.query(b, "confidential alpha", top_k=5) == []
    assert not rt.file_store.exists(b, "artifacts/x.txt")
    rt.close()


# --- 8. vm rejected at BOTH layers (defense in depth) --------------------


def test_vm_rejected_at_compose_and_at_runtime(tmp_path):
    # L2: compose rejects a vm brief (invariant 12).
    d_vm = compose(
        DomainBrief(name="VM", goal="g", domain="d", available_compute=["vm"]),
        "usr_1", "usr_1",
    )
    assert d_vm.status == "draft"
    assert any("invariant 12" in e for e in d_vm.validation_errors)

    # L0: even if vm is force-enabled on an otherwise-valid definition, the
    # runtime refuses to instantiate it.
    d_ok = compose(
        DomainBrief(name="Sh", goal="g", domain="d", available_compute=["shell"]),
        "usr_1", "usr_1",
    )
    d_ok.primitives.compute.vm.enabled = True
    rt = AgentRuntime(tmp_path)
    with pytest.raises(RuntimeError_):
        rt.instantiate(d_ok)
    rt.close()


# --- 9. JSON handoff boundary then instantiate ---------------------------


def test_json_handoff_then_runtime_instantiate(tmp_path):
    d = compose(_lead_brief(), "usr_1", "usr_1")
    # Serialize as a runtime would persist it, reload in a "fresh process".
    reloaded = AgentDefinition.model_validate_json(d.model_dump_json())
    rt = AgentRuntime(tmp_path)
    agent_id = rt.instantiate(reloaded)
    assert agent_id == d.agent_id
    # The reloaded definition's config still drives provisioning correctly.
    rt.kv_store.put(agent_id, "k", b"v")
    assert rt.kv_store.exists(agent_id, "k")
    assert rt.file_store.exists(agent_id, "workspace")
    rt.close()


# --- 10. full agent run through the ReasoningEngine (fake gateway) --------


def test_reasoning_engine_full_run_through_runtime(tmp_path):
    import asyncio

    from agentwright.runtime import ReasoningEngine

    d = compose(_lead_brief(), "usr_1", "usr_1")
    rt = AgentRuntime(tmp_path)
    rt.instantiate(d)  # default FakeLLMGateway returns no tool calls -> one turn

    res = asyncio.run(ReasoningEngine(rt).run(d, "summarize today's leads"))
    assert res.stop_reason == "done"
    assert res.output  # the fake gateway produced an answer
    run = rt.system_db.get_run(res.run_id)
    assert run["status"] == "done"
    assert rt.system_db.run_cost(res.run_id) == pytest.approx(res.cost_usd)
    # Transcript persisted to the file store; interaction remembered (memory agent).
    assert rt.file_store.exists(d.agent_id, f"history/{res.run_id}.md")
    assert rt.preference_store.get_all(d.agent_id)
    rt.close()


# --- 11. the definition's provider selects the gateway (no network) -------


def test_provider_in_definition_routes_gateway(monkeypatch):
    from agentwright.runtime import real_backends as rb

    monkeypatch.setattr(rb, "AnthropicGateway", lambda db=None: ("anthropic", db))
    monkeypatch.setattr(rb, "OpenAIGateway", lambda db=None: ("openai", db))
    monkeypatch.setattr(rb, "GoogleGateway", lambda db=None: ("google", db))

    d = compose(_lead_brief(), "usr_1", "usr_1")
    provider = d.primitives.reasoning_loop.model.provider  # "anthropic" by default
    assert rb.make_gateway(provider)[0] == "anthropic"


# --- 12. LIVE three-layer run with a real provider (key-gated) -----------


def test_live_three_layer_run_anthropic(tmp_path):
    import asyncio
    import os

    pytest.importorskip("anthropic")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set — live three-layer run skipped")

    from agentwright.runtime import ReasoningEngine
    from agentwright.runtime.real_backends import AnthropicGateway

    d = compose(_lead_brief(), "usr_1", "usr_1")
    # Use a cheap model + small output for the live call.
    d.primitives.reasoning_loop.model.model_id = "claude-haiku-4-5-20251001"
    d.primitives.reasoning_loop.model.max_tokens = 32

    rt = AgentRuntime(tmp_path)
    rt.instantiate(d)
    rt.llm_gateway = AnthropicGateway(rt.system_db)  # real provider

    res = asyncio.run(ReasoningEngine(rt).run(d, "Reply with exactly: DONE"))
    assert res.stop_reason == "done"
    assert "DONE" in res.output.upper()
    assert res.cost_usd > 0  # billed to the ledger
    assert rt.system_db.get_run(res.run_id)["status"] == "done"
    rt.close()
