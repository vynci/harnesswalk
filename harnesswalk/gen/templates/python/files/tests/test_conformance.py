"""The conformance suite under this repo's profile, on the fake provider (CI runs it)."""

from tests.conformance import runner


def test_conformance_suite_passes() -> None:
    assert runner.main(["--profile", runner.MANIFEST["profile"]]) == 0
