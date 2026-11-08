"""The runner against an implementation it did not start (P7): `--base-url` and `--command`.

The reference harness stands in for "any implementation": once behind a server the runner
did not start, once started from a command template the way an external one would be."""

from pathlib import Path

from tests.conformance.runner import (
    CONF,
    LiveHarness,
    Target,
    case_paths,
    load_yaml,
    needs_own_server,
    run_case,
)

SHARED_SERVER_CASES = {
    "approval_approve", "approval_deny", "approval_off", "audit_chain", "cancel_running",
    "pii_redacted", "plain_answer", "schema_extracted", "step_mode", "step_over", "tool_call",
}  # fmt: skip

REFERENCE_COMMAND = (
    "{python} -m harnesswalk serve --provider fake --fixture {fixture} --profile {profile}"
    " --mode {mode} --port {port} --log-level warning {set_args}"
)


def case(name: str) -> Path:
    return CONF / "cases" / f"{name}.yaml"


def test_which_cases_a_shared_server_can_run() -> None:
    runnable = {p.stem for p in case_paths() if needs_own_server(load_yaml(p)) is None}
    assert runnable == SHARED_SERVER_CASES


def test_base_url_drives_a_running_server_with_per_turn_fixtures(tmp_path) -> None:
    # One server on its default fixture; each turn names the case's fixture in the body.
    with LiveHarness({"fixture": "plain_answer"}, tmp_path) as live:
        target = Target(base_url=live.base_url, tenant_suffix="-t1")
        for name in ("tool_call", "approval_approve", "step_over"):
            result = run_case(case(name), target)
            assert result.passed, (name, result.problems)
        again = run_case(case("tool_call"), Target(base_url=live.base_url, tenant_suffix="-t2"))
        assert again.passed, again.problems
        skipped = run_case(case("loop_max_steps"), target)
        assert skipped.skipped and "loop.max_steps" in skipped.skipped
        assert not skipped.passed and not skipped.problems


def test_command_starts_an_implementation_per_case_with_overrides_and_restarts() -> None:
    target = Target(command=REFERENCE_COMMAND)
    for name in ("loop_max_steps", "approval_resume_after_restart", "injection_blocked_public"):
        result = run_case(case(name), target)
        assert result.passed, (name, result.problems)


def test_a_command_that_does_not_start_fails_the_case() -> None:
    result = run_case(case("plain_answer"), Target(command="{python} -c 'raise SystemExit(3)'"))
    assert not result.passed
    assert "exited 3" in result.problems[0]
