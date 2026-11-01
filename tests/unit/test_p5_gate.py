"""P5 release gate: POST /v1/releases runs the golden set first; the release records the gate,
its hash covers it; force skips it in dev, audited; the activation log is chained. Fake
provider only, the starter pack as shipped (the gate is on by default)."""

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from harnesswalk.ops import release as rel
from harnesswalk.ops.artifacts import ArtifactStore
from harnesswalk.runtime import Harness
from tests.spec.support import ROOT, event_validator
from tests.unit.support import run_turn, settings
from tests.unit.test_http_api import client

WINDOW = "within 30 days of delivery"


def starter(tmp_path: Path, mode: str = "dev", **overrides) -> Harness:
    return Harness(settings(tmp_path, "plain_answer", profile=None, overrides=overrides, mode=mode))


def ops_types(h: Harness, tenant: str = "dev") -> list[str]:
    return [e["type"] for e in h.ops.list(tenant)]


async def break_returns(c) -> None:
    body = (await c.get("/v1/artifacts/skill/returns")).json()["body"]
    assert WINDOW in body
    r = await c.put(
        "/v1/artifacts/skill/returns", json={"body": body.replace("30 days", "14 days")}
    )
    assert r.status_code == 200, r.text


@pytest.mark.anyio
async def test_a_passing_gate_releases_and_records_the_run(tmp_path: Path) -> None:
    h = starter(tmp_path)
    async with client(h, "dev") as c:
        r = await c.post("/v1/releases", json={"note": "as shipped"})
        assert r.status_code == 201, r.text
        out = r.json()
        gate = out["gate"]
        assert gate["status"] == "passed" and gate["pass_rate"] == 1.0
        assert gate["suite"] == "golden" and gate["min_pass_rate"] == 1.0
        assert gate["failed_cases"] == [] and not gate["forced"] and len(gate["run_ids"]) == 1
        run = (await c.get(f"/v1/evals/runs/{gate['run_ids'][0]}")).json()
        assert run["purpose"] == "release_gate" and run["status"] == "done"
        assert run["totals"]["failed"] == 0
        listed = (await c.get("/v1/releases")).json()
        assert listed["releases"][0]["gate"]["status"] == "passed"
        assert listed["releases"][-1]["gate"]["reason"] == "startup"  # r1, seeded
        assert listed["verify"]["ok"] is True
    types = ops_types(h)
    assert types[-2:] == ["release_gated", "artifact_released"]
    gated, released = h.ops.list("dev")[-2:]
    assert gated["status"] == "done" and gated["summary"]["gate"] == "passed"
    assert released["summary"]["gate"] == "passed"
    started = h.ops.list("dev", f"eval:{gate['run_ids'][0]}", type_="eval_run_started")[0]
    assert started["summary"]["purpose"] == "release_gate"
    validator = event_validator()
    assert all(not list(validator.iter_errors(e)) for e in h.ops.list("dev"))
    await h.aclose()


@pytest.mark.anyio
async def test_a_failing_gate_is_409_with_the_cases_and_force_releases_anyway(
    tmp_path: Path,
) -> None:
    h = starter(tmp_path)
    async with client(h, "dev") as c:
        before = (await c.get("/v1/releases")).json()["active_release_id"]
        await break_returns(c)
        r = await c.post("/v1/releases", json={"note": "14 days"})
        assert r.status_code == 409, r.text
        body = r.json()
        assert body["error"]["code"] == "gate_failed"
        assert (
            body["gate"]["status"] == "failed" and body["gate"]["reason"] == "below_min_pass_rate"
        )
        assert body["gate"]["failed_cases"] == ["returns_window"]
        assert body["gate"]["pass_rate"] < 1.0
        [case] = body["cases"]
        assert case["case_id"] == "returns_window" and case["events_url"]
        assert (await c.get("/v1/releases")).json()["active_release_id"] == before
        assert ops_types(h)[-1] == "release_gated"
        assert h.ops.list("dev")[-1]["status"] == "error"

        r = await c.post("/v1/releases", json={"note": "anyway", "force": True})
        assert r.status_code == 201, r.text
        gate = r.json()["gate"]
        assert gate == {
            "status": "skipped", "reason": "forced", "suite": "golden", "min_pass_rate": 1.0,
            "pass_rate": None, "run_ids": [], "failed_cases": [], "forced": True,
        }  # fmt: skip
        assert (await c.get("/v1/releases")).json()["active_release_id"] == r.json()["release_id"]
        audit = (await c.get("/v1/audit")).json()["records"]
        forced = [a for a in audit if a["entry"]["action"] == "release_forced"]
        assert forced and forced[-1]["entry"]["extra"]["release_id"] == r.json()["release_id"]
        assert (await c.post("/v1/releases", json={"force": "yes"})).status_code == 400
    await h.aclose()


@pytest.mark.anyio
async def test_gate_switches(tmp_path: Path) -> None:
    off = starter(tmp_path / "off", **{"release.gate.enabled": False})
    out = await rel.apply_create_gated(off, "dev", note=None)
    assert out["gate"]["status"] == "skipped" and out["gate"]["reason"] == "disabled"
    assert "release_gated" not in ops_types(off)
    await off.aclose()
    other = starter(tmp_path / "other", **{"release.gate.packs": ["ecommerce"]})
    out = await rel.apply_create_gated(other, "dev", note=None)
    assert out["gate"]["reason"] == "pack_not_gated"
    await other.aclose()
    lax = starter(tmp_path / "lax", **{"release.gate.min_pass_rate": 0.5})
    async with client(lax, "dev") as c:
        await break_returns(c)
        r = await c.post("/v1/releases", json={})
        assert r.status_code == 201 and r.json()["gate"]["status"] == "passed"
        assert r.json()["gate"]["failed_cases"] == ["returns_window"]
    await lax.aclose()


@pytest.mark.anyio
async def test_outside_dev_the_gate_tests_the_working_set_not_the_served_release(
    tmp_path: Path,
) -> None:
    h = starter(tmp_path, mode="private")
    assert h.release.use_working_set is False
    body = h.release.artifact("skill", "returns")["body"]
    rel.apply_edit(h, "ops", "skill", "returns", body.replace("30 days", "14 days"))
    assert WINDOW in h.procedural.skills["returns"].body  # still serving the active release
    with pytest.raises(rel.GateFailed) as exc:
        await rel.apply_create_gated(h, "ops", note="candidate")
    # pii_redacted fails in private too: the starter case expects dev's restored values.
    assert "returns_window" in exc.value.detail["gate"]["failed_cases"]
    run = h.evals.store.run("ops", exc.value.detail["gate"]["run_ids"][0])
    assert run["working_set"] is True  # the candidate served the working set
    assert WINDOW in h.procedural.skills["returns"].body
    assert h.release.status()["active_release_id"] == "r1"
    await h.aclose()


def test_the_release_hash_covers_the_gate(tmp_path: Path) -> None:
    store = ArtifactStore(str(tmp_path / "releases.db"))
    store.put("p", "prompt", "p", "one", by="t")
    gate = {"status": "passed", "run_ids": ["run_0001"], "pass_rate": 1.0}
    made = store.create_release("p", by="t", gate=gate)
    assert store.release(made.release_id).gate == gate
    assert store.verify("p")["ok"]
    store.db.execute(
        "UPDATE release_gates SET gate = ? WHERE release_id = ?",
        ('{"status": "passed", "run_ids": [], "pass_rate": 0.1}', made.release_id),
    )
    assert store.verify("p") == {"ok": False, "releases": 1, "broken_at": made.release_id}
    store.close()


def test_the_activation_log_is_chained_and_old_stores_are_chained_on_open(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(str(tmp_path / "releases.db"))
    store.put("p", "prompt", "p", "one", by="t")
    a = store.create_release("p", by="t")
    store.put("p", "prompt", "p", "two", by="t")
    store.create_release("p", by="t")
    store.activate("p", a.release_id, by="t", rollback_of="r2")
    assert store.verify("p") == {"ok": True, "releases": 2, "activations": 3}
    store.db.execute("UPDATE activations SET by = 'mallory' WHERE seq = 2")
    out = store.verify("p")
    assert out["ok"] is False and out["activation_broken_at"] == 2
    store.close()

    # A P4 store: activations without the chain columns get them, and are chained, on open.
    old = tmp_path / "old.db"
    conn = sqlite3.connect(old)
    conn.executescript(
        "CREATE TABLE activations (seq INTEGER PRIMARY KEY AUTOINCREMENT, pack TEXT NOT NULL,"
        " release_id TEXT NOT NULL, at TEXT NOT NULL, by TEXT NOT NULL, rollback_of TEXT);"
        "INSERT INTO activations (pack, release_id, at, by) VALUES"
        " ('p', 'r1', '2026-10-01T00:00:00.000Z', 'seed'),"
        " ('p', 'r2', '2026-10-02T00:00:00.000Z', 'user:dev');"
    )
    conn.commit()
    conn.close()
    reopened = ArtifactStore(str(old))
    assert reopened.verify_activations("p") == {"ok": True, "activations": 2}
    reopened.activate("p", "r1", by="t")
    assert reopened.verify_activations("p") == {"ok": True, "activations": 3}
    reopened.close()


@pytest.mark.anyio
async def test_rollback_is_not_gated(tmp_path: Path) -> None:
    h = starter(tmp_path)
    async with client(h, "dev") as c:
        await break_returns(c)
        assert (await c.post("/v1/releases", json={"force": True})).status_code == 201
        r = await c.post("/v1/releases/r1/rollback", json={})
        assert r.status_code == 200, r.text
        events = await run_turn(h, "What are your support hours?", tenant="dev")
        assert events[0]["summary"]["release"]["release_id"] == "r1"
        assert r.json()["verify"]["ok"] if "verify" in r.json() else True
        assert (await c.get("/v1/releases")).json()["verify"]["activations"] == 3
    await h.aclose()


def _cli(tmp_path: Path, *args: str, mode: str = "dev") -> subprocess.CompletedProcess:
    env = {
        "PATH": "/usr/bin:/bin",
        "HARNESSWALK_ROOT": str(ROOT),
        "HARNESSWALK_DATA_DIR": str(tmp_path / "data"),
        "HARNESSWALK_EMBEDDINGS": "fake",
        "HARNESSWALK_MODE": mode,
        "HARNESSWALK_OLLAMA_URL": "",
    }
    return subprocess.run(
        [sys.executable, "-m", "harnesswalk", "release", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(ROOT),
    )


def test_cli_release_create_runs_the_gate(tmp_path: Path) -> None:
    out = _cli(tmp_path, "create", "--note", "from the CLI")
    assert out.returncode == 0, out.stderr
    assert "created r2 with" in out.stdout and "gate passed" in out.stdout
    out = _cli(tmp_path, "create", "--no-gate")
    assert out.returncode == 0 and "gate skipped (forced" in out.stdout
    assert _cli(tmp_path, "create", "--no-gate", mode="private").returncode == 2
    assert _cli(tmp_path, "list", "--pack", "nope").returncode == 2
