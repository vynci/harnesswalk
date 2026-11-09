"""The pack's golden set on the fake provider: every case passes (evals/README.md)."""

from evals.run_golden import run


def test_golden_set_passes(tmp_path) -> None:
    totals = run(data=str(tmp_path))
    assert totals["failed"] == 0, totals
