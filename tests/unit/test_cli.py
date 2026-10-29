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
