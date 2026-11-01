"""tools/release.py: the changelog move and the tag check the release workflow relies on."""

import importlib.util

import pytest

from tests.spec.support import ROOT

spec = importlib.util.spec_from_file_location("release_tool", ROOT / "tools" / "release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

FIRST = """# Changelog

## [Unreleased]

### Added

- Everything.

[Unreleased]: https://github.com/vynci/harnesswalk/commits/main
"""


def test_first_release_moves_unreleased_and_links_the_tag() -> None:
    text = release.move_unreleased(FIRST, "0.1.0", "2026-11-01")
    assert "## [Unreleased]\n\nNothing yet.\n\n## [0.1.0] - 2026-11-01\n\n### Added" in text
    assert "[Unreleased]: https://github.com/vynci/harnesswalk/compare/v0.1.0...HEAD" in text
    assert "[0.1.0]: https://github.com/vynci/harnesswalk/releases/tag/v0.1.0" in text
    assert release.section(text, "0.1.0") == "### Added\n\n- Everything.\n"


def test_second_release_compares_against_the_first() -> None:
    once = release.move_unreleased(FIRST, "0.1.0", "2026-11-01")
    twice = once.replace("Nothing yet.", "### Fixed\n\n- A bug.")
    text = release.move_unreleased(twice, "0.2.0", "2026-12-01")
    assert text.index("## [0.2.0]") < text.index("## [0.1.0]")
    assert "[0.2.0]: https://github.com/vynci/harnesswalk/compare/v0.1.0...v0.2.0" in text
    assert release.section(text, "0.2.0") == "### Fixed\n\n- A bug.\n"
    assert release.section(text, "0.1.0") == "### Added\n\n- Everything.\n"


def test_an_empty_or_repeated_release_is_refused() -> None:
    once = release.move_unreleased(FIRST, "0.1.0", "2026-11-01")
    with pytest.raises(release.ReleaseError, match="empty"):
        release.move_unreleased(once, "0.2.0", "2026-12-01")
    with pytest.raises(release.ReleaseError, match="already"):
        release.move_unreleased(once.replace("Nothing yet.", "- x"), "0.1.0", "2026-12-01")


def test_the_repo_changelog_has_an_unreleased_section_to_release() -> None:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert release.section(text, "Unreleased").strip()
    release.move_unreleased(text, "9.9.9", "2026-11-01")  # would succeed


def test_check_tag_matches_the_project_version(capsys) -> None:
    version = release.project_version()
    assert release.main(["check-tag", f"v{version}"]) == 0
    assert release.main(["check-tag", "v0.0.0-not-it"]) == 1
