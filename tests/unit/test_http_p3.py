"""HTTP P3: structured replies, the model hint, health per provider, the audit records tab."""

from pathlib import Path

import httpx
import pytest

from harnesswalk.control.http_api import create_app
from tests.unit.support import harness, run_turn, write_fixture


def client(h) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(h)),
        base_url="http://t",
        headers={"X-Harnesswalk-Tenant": "t1"},
    )


async def finish(c: httpx.AsyncClient, body: dict) -> list[dict]:
    import json

    r = await c.post("/v1/turns", json=body)
    assert r.status_code == 202, r.text
    text = (await c.get(f"/v1/turns/{r.json()['turn_id']}/events")).text
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


@pytest.mark.anyio
async def test_output_schema_is_enforced(tmp_path: Path) -> None:
    fx = tmp_path / "fx"
    write_fixture(fx, "json_reply", [[{"text": '{"status": "shipped"}'}], [{"text": "not json"}]])
    h = harness(tmp_path, "json_reply", fixtures=fx)
    schema = {"type": "object", "required": ["status"]}
    async with client(h) as c:
        good = await finish(c, {"message": "Status?", "output_schema": schema, "session_id": "s1"})
        guard = [
            e for e in good if e.get("node_id") == "output_guardrails" and e["type"] == "node_exit"
        ]
        assert guard[0]["summary"]["schema"] == "valid"
        bad = await finish(c, {"message": "Again?", "output_schema": schema, "session_id": "s1"})
        assert bad[-1]["summary"]["exit"] == "blocked"
        r = await c.post("/v1/turns", json={"message": "x", "output_schema": "nope"})
        assert r.status_code == 400
        r = await c.post("/v1/turns", json={"message": "x", "model": "no-such-alias"})
        assert r.status_code == 400


@pytest.mark.anyio
async def test_health_reports_providers_and_audit_inspector_has_records(tmp_path: Path) -> None:
    h = harness(tmp_path, "approval_approve")
    await run_turn(h, "Please cancel order A-1002.", approval="approve")
    async with client(h) as c:
        health = (await c.get("/healthz")).json()
        assert health["providers"] == {"fake": "ok (not probed)"}
        node = (await c.get("/v1/nodes/audit")).json()["inspector"]
        assert node["verify"]["ok"] is True and len(node["records"]) == 3
        assert node["records"][0]["entry"]["action"] == "tool_executed"  # newest first
        assert node["export"] == "/v1/audit"


def test_dataset_tool_by_entrypoint_and_audit_before(tmp_path: Path) -> None:
    from harnesswalk.execution.registry import ToolRegistry
    from harnesswalk.types import ConfigError

    module = tmp_path / "pack_tools.py"
    module.write_text("def greet(arguments):\n    return 'hi ' + arguments['name']\n")
    yaml_path = tmp_path / "tools.yaml"
    yaml_path.write_text(
        "- name: greet\n  description: Greet.\n  action_class: read\n  timeout_ms: 500\n"
        "  sandbox: subprocess\n  entrypoint: pack_tools.py:greet\n"
        "  parameters: { type: object, properties: { name: { type: string } } }\n"
    )
    reg = ToolRegistry()
    reg.load_yaml(yaml_path)
    spec = reg.get("greet")
    assert spec.entrypoint == f"{module.resolve()}:greet" and spec.sandbox == "subprocess"
    assert spec.impl({"name": "Ana"}) == "hi Ana"  # the in-process level imports it too
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "- name: w\n  description: W.\n  action_class: write\n  timeout_ms: 500\n"
        "  audit: { before: missing_reader }\n  table: {}\n"
        "  parameters: { type: object, required: [k], properties: { k: { type: string } } }\n"
    )
    with pytest.raises(ConfigError):
        ToolRegistry().load_yaml(bad)
