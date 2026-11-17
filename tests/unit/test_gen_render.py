"""The generator's template preprocessor and manifest checks (harnesswalk/gen)."""

from __future__ import annotations

import pytest

from harnesswalk.gen.manifest import GenManifest, ManifestError
from harnesswalk.gen.render import TemplateError, render

CTX = {
    "has": lambda n: n in {"policy"},
    "providers": ["fake", "ollama"],
    "release": "chain",
    "x": 7,
}


def test_directives_keep_and_drop_lines() -> None:
    text = (
        "a\n#% if has('policy')\nb\n#%   if release == 'file_door'\nc\n#% else\nd\n#% endif\n"
        "#% elif True\ne\n#% else\nf\n#% endif\ng @@x@@\n"
    )
    assert render(text, CTX) == "a\nb\nd\ng 7\n"


def test_elif_and_else_branches() -> None:
    text = "#% if has('approval')\nA\n#% elif 'ollama' in providers\nB\n#% else\nC\n#% endif\n"
    assert render(text, CTX) == "B\n"
    assert render(text, {**CTX, "providers": ["fake"]}) == "C\n"


def test_template_errors_are_loud() -> None:
    with pytest.raises(TemplateError, match="unclosed"):
        render("#% if True\nx\n", CTX)
    with pytest.raises(TemplateError, match="no value"):
        render("@@missing@@", CTX)
    with pytest.raises(TemplateError, match="bad directive"):
        render("#% endif\n", CTX)


def test_manifest_refuses_what_is_not_generated() -> None:
    ids = {"gateway"}
    with pytest.raises(ManifestError, match="operated"):
        GenManifest("operated", "p").check(6, ids)
    with pytest.raises(ManifestError, match="dev mode"):
        GenManifest("minimal", "p", modes=["dev", "public"]).check(1, ids)
    with pytest.raises(ManifestError, match="governed"):
        GenManifest("minimal", "p", release="chain").check(1, ids)
    with pytest.raises(ManifestError, match="tune node"):
        GenManifest("governed", "p", tune=True).check(5, ids)
    with pytest.raises(ManifestError, match="providers"):
        GenManifest("minimal", "p", providers=["ollama"]).check(1, ids)


def test_release_door_defaults_by_rung() -> None:
    low, high = GenManifest("minimal", "p"), GenManifest("governed", "p")
    low.check(1, set())
    high.check(5, set())
    assert (low.release, high.release) == ("file_door", "chain")
