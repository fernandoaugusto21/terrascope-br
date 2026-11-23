"""The wiki's Changelog page: the windows-* releases with their notes, newest first, one section each."""

import importlib.util
import json
import random
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "packaging" / "windows" / "wiki_changelog.py"
spec = importlib.util.spec_from_file_location("wiki_changelog", SCRIPT)
wiki = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wiki)

NOTES = ("## {n}: changes\n\n### Terrascope (core)\n\n- **Core change** ([#1](https://example.test/1))\n"
         "  First paragraph of the pull request.\n\n### terrascope-br (plugin)\n\n- **Plugin change** ([#2](https://example.test/2))\n\n"
         "---\n\nterrascope.exe with this plugin; the tag names the core and plugin commits it was built from.\n")


def release(name, tag, published="2026-11-22T10:20:30Z", body=None):
    number = name.split(" ", 1)[0]
    return {"name": name, "tagName": tag, "publishedAt": published,
            "body": NOTES.format(n=number) if body is None else body}


RELEASES = [
    release("0.9 (Radar)", "windows-aaaaaaa-br1111111", "2026-11-20T10:00:00Z"),
    release("0.9.2 (Radar fix)", "windows-bbbbbbb-br2222222", "2026-11-22T10:00:00Z"),
    release("0.9.1 (Hotfix)", "windows-ccccccc-br3333333", "2026-11-21T10:00:00Z"),
    release("0.10 (Next minor)", "windows-ddddddd-br4444444", "2026-11-23T10:00:00Z"),
    release("Road network roads-20261122-2247", "roads-20261122-2247", "2026-11-22T22:47:34Z", body="roads"),
    release("Windows build windows-eeeeeee-bre555555", "windows-eeeeeee-bre555555", body="no version"),
    release("0.8 (Before)", "windows-fffffff-br6666666", "2026-11-10T10:00:00Z"),
]


def headings(page):
    return [line[3:] for line in page.split("\n") if line.startswith("## ")]


def test_versions_are_newest_first_with_hotfixes_between_their_minor():
    expected = ["0.10", "0.9.2", "0.9.1", "0.9", "0.8"]
    for seed in range(5):   # the order of the releases does not matter
        shuffled = RELEASES[:]
        random.Random(seed).shuffle(shuffled)
        assert headings(wiki.changelog_page(shuffled)) == expected


def test_road_and_unnumbered_releases_are_left_out():
    page = wiki.changelog_page(RELEASES)
    assert "roads" not in page and "Road network" not in page
    assert "windows-eeeeeee" not in page and "no version" not in page


def test_the_version_comparison_is_numeric():
    assert wiki.version_of("0.9") < wiki.version_of("0.9.1") < wiki.version_of("0.9.2") < wiki.version_of("0.10")
    assert wiki.version_of("0.9.1 (x)") == (9, 1)
    assert wiki.version_of("roads-20261122") is None


def test_the_notes_drop_the_heading_and_the_install_footer_and_go_one_level_down():
    section = wiki.changelog_page([RELEASES[1]]).split("## 0.9.2\n", 1)[1]
    body = section.split("\n## ", 1)[0]
    assert "## 0.9.2: changes" not in body
    assert "terrascope.exe with this plugin" not in body and "\n---\n" not in body
    assert "#### Terrascope (core)" in body and "#### terrascope-br (plugin)" in body
    assert not any(line.startswith("### ") for line in body.split("\n"))
    assert "- **Core change** ([#1](https://example.test/1))" in body


def test_windows_line_endings_in_the_notes_are_trimmed_too():
    crlf = NOTES.format(n="0.9.2").replace("\n", "\r\n")
    page = wiki.changelog_page([release("0.9.2 (x)", "windows-bbbbbbb-br2222222", body=crlf)])
    assert "terrascope.exe with this plugin" not in page
    assert "\r" not in page


def test_the_anchors_drop_the_dots_and_the_meta_line_has_date_title_and_link():
    page = wiki.changelog_page(RELEASES)
    tag = "https://github.com/fernandoaugusto21/terrascope-br/releases/tag/windows-bbbbbbb-br2222222"
    assert "| [0.10](#010) | 2026-11-23 10:00 | [windows-ddddddd-br4444444](https://github.com/" in page
    assert "| [0.9.2](#092) | 2026-11-22 10:00 | [windows-bbbbbbb-br2222222](" in page
    assert "| [0.9](#09) | 2026-11-20 10:00 |" in page
    assert "## 0.9.2\n\n2026-11-22 10:00 UTC · Radar fix · [release](" + tag + ")\n" in page


def test_the_page_starts_with_the_fixed_header_and_the_index():
    page = wiki.changelog_page(RELEASES)
    assert page.startswith("# Changelog\n\nThe Windows builds of terrascope-br")
    assert "scoop install https://github.com/fernandoaugusto21/terrascope-br/releases/latest/download/terrascope-br.json" in page
    assert "| Version | Date (UTC) | Release |\n|---|---|---|\n| [0.10](#010)" in page
    assert page.endswith("\n") and not page.endswith("\n\n")


def test_a_pruned_version_keeps_its_section_from_the_page_in_the_wiki():
    page = wiki.changelog_page(RELEASES)
    rest = [r for r in RELEASES if not r["name"].startswith("0.8 ")]   # 0.8 is no longer a release
    assert "## 0.8" in wiki.changelog_page(rest, previous=page)
    assert wiki.changelog_page(rest, previous=page) == page


def test_a_release_still_listed_replaces_its_old_section():
    page = wiki.changelog_page(RELEASES)
    edited = [dict(r) for r in RELEASES]
    edited[1]["body"] = NOTES.format(n="0.9.2").replace("First paragraph", "Edited paragraph")
    result = wiki.changelog_page(edited, previous=page)
    section = result.split("## 0.9.2\n", 1)[1].split("\n## ", 1)[0]
    assert "Edited paragraph" in section and "First paragraph" not in section
    assert headings(result) == headings(page)


def test_regenerating_from_the_same_page_is_a_no_op():
    page = wiki.changelog_page(RELEASES)
    assert wiki.changelog_page(RELEASES, previous=page) == page


def test_the_cli_writes_changelog_md_with_lf_and_reads_the_previous_page(tmp_path):
    releases = tmp_path / "releases.json"
    releases.write_text(json.dumps(RELEASES[:4]), encoding="utf-8")
    first = tmp_path / "first"
    assert wiki.main(["--releases", str(releases), "--out", str(first)]) == 0
    page = (first / "Changelog.md").read_bytes()
    assert b"\r" not in page
    assert page.decode("utf-8") == wiki.changelog_page(RELEASES[:4])

    # The next release prunes the oldest build: the page keeps it.
    releases.write_text(json.dumps(RELEASES[:3]), encoding="utf-8")
    second = tmp_path / "second"
    wiki.main(["--releases", str(releases), "--out", str(second), "--previous", str(first / "Changelog.md")])
    assert (second / "Changelog.md").read_text(encoding="utf-8") == wiki.changelog_page(RELEASES[:4])
