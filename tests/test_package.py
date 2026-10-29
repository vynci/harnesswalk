"""The package skeleton imports and its entry point runs."""

import importlib

import harness
from harness.__main__ import main

SUBPACKAGES = ["control", "context", "execution", "ops", "adapters", "config"]


def test_subpackages_import() -> None:
    for name in SUBPACKAGES:
        importlib.import_module(f"harness.{name}")


def test_versions_are_set() -> None:
    assert harness.__version__
    assert harness.SPEC_VERSION == "0.1.0"


def test_main_exits_zero(capsys) -> None:
    assert main() == 0
    assert "P0" in capsys.readouterr().out
