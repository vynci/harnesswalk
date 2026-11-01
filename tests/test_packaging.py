"""The wheel's metadata and the paths an installed harness reads from (tools/hatch_build.py)."""

import tomllib
from pathlib import Path

import harnesswalk
from harnesswalk import paths
from tests.spec.support import ROOT

PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_package_version_matches_the_project_version() -> None:
    assert PYPROJECT["project"]["version"] == harnesswalk.__version__


def test_project_metadata_is_ready_for_pypi() -> None:
    project = PYPROJECT["project"]
    assert project["name"] == "harnesswalk"
    assert project["license"] == "Apache-2.0"
    assert project["scripts"] == {"harnesswalk": "harnesswalk.__main__:main"}
    assert {"Homepage", "Source", "Issues", "Changelog"} <= set(project["urls"])


def test_the_bundle_hook_is_off_by_default_and_its_trees_exist() -> None:
    hook = PYPROJECT["tool"]["hatch"]["build"]["targets"]["wheel"]["hooks"]["custom"]
    assert hook["enable-by-default"] is False  # uv sync and the Docker image never load it
    assert (ROOT / hook["path"]).is_file()
    for tree in ("config", "spec", "datasets", "lessons", "harnesswalk"):
        assert (ROOT / tree).is_dir(), tree


def test_a_checkout_reads_its_own_root_and_keeps_data_in_it() -> None:
    assert paths.PACKAGE_ROOT == ROOT
    assert paths.default_data_dir(ROOT) == ROOT / "data"


def test_an_installed_wheel_never_writes_into_site_packages(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert paths.default_data_dir(paths.BUNDLE) == Path.cwd() / "harnesswalk-data"
