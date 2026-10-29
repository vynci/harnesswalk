"""Every spec/conformance/cases/*.yaml against the live reference harness (fake provider)."""

from pathlib import Path

import pytest

from tests.conformance.runner import case_paths, run_case


@pytest.mark.parametrize("path", case_paths(), ids=lambda p: p.stem)
def test_conformance_case(path: Path) -> None:
    result = run_case(path)
    assert result.passed, "\n".join(result.problems)
