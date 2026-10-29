"""The CLI prints the walk and dumps stored events."""

import json

from harnesswalk.__main__ import main


def test_turn_prints_the_walk_and_events_dumps_it(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("HARNESSWALK_DATA_DIR", str(tmp_path))
    assert (
        main(
            [
                "turn",
                "What are your support hours?",
                "--provider",
                "fake",
                "--fixture",
                "plain_answer",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    walk = [
        line.split()[0]
        for line in out.split("walk (")[1].split("\nreply\n")[0].splitlines()[1:]
        if line.strip()
    ]
    assert walk[:3] == ["gateway", "input_guardrails", "context_builder"]
    assert walk[-2:] == ["trace", "cost"]
    assert "9am to 5pm" in out and "exit answered" in out
    turn_id = out.split("harnesswalk events ")[1].split()[0]
    assert main(["events", turn_id]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines[0]["type"] == "turn_start" and lines[-1]["type"] == "turn_end"
    assert main(["events", "turn_missing"]) == 1


def test_turn_with_approval_flag(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("HARNESSWALK_DATA_DIR", str(tmp_path))
    args = [
        "turn",
        "Cancel A-1002",
        "--provider",
        "fake",
        "--fixture",
        "cancel_order",
        "--approval",
        "deny",
    ]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "decision=deny" in out and "outcome=denied" in out


def test_turns_lists_a_session_and_cancel_after_stops_a_turn(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("HARNESSWALK_DATA_DIR", str(tmp_path))
    base = ["--provider", "fake", "--fixture", "two_turns"]
    assert main(["turn", "Hello", *base]) == 0
    session = capsys.readouterr().out.split("session ")[1].split()[0]
    args = ["turn", "Where is A-1002?", "--session", session, "--cancel-after", "loop", *base]
    assert main(args) == 0  # stopped at the loop's first step boundary, with its notice
    out = capsys.readouterr().out
    assert "exit cancelled" in out and "exit_reason=interrupted" in out

    assert main(["turns", session]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2 and lines[0].split()[0] == "1" and lines[1].split()[0] == "0"
    assert "cancelled" in lines[0] and "answered" in lines[1]
    assert main(["turns", session, "--json", "--limit", "1"]) == 0
    turns = json.loads(capsys.readouterr().out)
    assert [t["exit"] for t in turns] == ["cancelled"]
    assert main(["turns", session, "--tenant", "someone_else"]) == 1


def test_read_commands_never_create_or_write_stores(tmp_path, monkeypatch, capsys) -> None:
    data = tmp_path / "data"
    monkeypatch.setenv("HARNESSWALK_DATA_DIR", str(data))
    assert main(["turns", "sess_nope"]) == 1
    assert main(["events", "turn_nope"]) == 1
    assert not data.exists()  # reading opened nothing read/write, so created nothing
    assert "no session sess_nope" in capsys.readouterr().err

    assert main(["turn", "Hello", "--provider", "fake", "--fixture", "two_turns"]) == 0
    session = capsys.readouterr().out.split("session ")[1].split()[0]
    stores = sorted(p for p in data.iterdir() if p.suffix == ".db")
    before = {p.name: p.read_bytes() for p in stores}
    assert main(["turns", session]) == 0 and main(["turns", "sess_other"]) == 1
    assert {p.name: p.read_bytes() for p in stores} == before


def test_make_run_respects_a_web_dist_from_the_environment(tmp_path) -> None:
    import os
    import subprocess

    from tests.spec.support import ROOT

    env = {k: v for k, v in os.environ.items() if k != "HARNESSWALK_WEB_DIST"}
    default = subprocess.run(
        ["make", "-n", "run"], cwd=ROOT, env=env, capture_output=True, text=True, check=True
    ).stdout
    assert f'HARNESSWALK_WEB_DIST="{ROOT}/web/dist"' in default
    env["HARNESSWALK_WEB_DIST"] = str(tmp_path / "dist")
    custom = subprocess.run(
        ["make", "-n", "run"], cwd=ROOT, env=env, capture_output=True, text=True, check=True
    ).stdout
    assert f'HARNESSWALK_WEB_DIST="{tmp_path}/dist"' in custom


def test_turn_prints_the_sources_the_reply_cites(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("HARNESSWALK_DATA_DIR", str(tmp_path))
    question = "Can I return order A-1003, the Trailhead backpack?"
    assert main(["turn", question, "--provider", "fake", "--fixture", "trailhead_return"]) == 0
    sources = capsys.readouterr().out.split("\nsources\n")[1].split("\n\n")[0]
    assert "[6] Returns policy, Return window  (returns_policy#1)" in sources
    assert "[5] Returns policy, Return labels  (returns_policy#2)" in sources
