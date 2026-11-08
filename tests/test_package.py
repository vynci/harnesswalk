"""The package imports, its version is set, and the entry point prints help."""

import importlib

import harnesswalk
from harnesswalk.__main__ import main

SUBPACKAGES = ["control", "context", "execution", "ops", "adapters", "config"]


def test_subpackages_import() -> None:
    for name in SUBPACKAGES:
        importlib.import_module(f"harnesswalk.{name}")


def test_versions_are_set() -> None:
    assert harnesswalk.__version__
    assert harnesswalk.SPEC_VERSION == "0.2.0"


def test_main_without_a_command_prints_help(capsys) -> None:
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "serve" in out and "turn" in out and "events" in out
