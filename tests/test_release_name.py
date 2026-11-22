"""The Windows build releases are named "0.<N> (<pull request titles>)"."""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "packaging" / "windows" / "release_name.py"
spec = importlib.util.spec_from_file_location("release_name", SCRIPT)
release_name = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_name)


def test_the_number_follows_the_highest_earlier_one():
    names = ["0.2 (Windows Support + .br plugin)", "Windows build windows-179e900-br930998f", "0.10 (x)"]
    assert release_name.next_number(names) == 11
    assert release_name.next_number(["Windows build windows-abc"]) == 1
    assert release_name.next_number(["0.2.1 (patch)"]) == 1   # only 0.<N>


def test_the_summary_joins_the_pull_request_titles():
    assert release_name.release_name(["0.2 (Windows Support + .br plugin)"],
                                     ["CGE regions: names, flood points, station and forecast",
                                      "Side panel: size the info box for the selected layer"]) == (
        "0.3 (CGE regions: names, flood points, station and forecast + "
        "Side panel: size the info box for the selected layer)")


def test_a_long_summary_is_cut_and_an_empty_one_says_rebuild():
    long = release_name.summary(["x" * 80, "y" * 80])
    assert len(long) == release_name.MAX_SUMMARY and long.endswith("…")
    assert release_name.release_name([], []) == "0.1 (rebuild)"
    assert release_name.summary(["A", "A", " "]) == "A"


def test_the_previous_release_says_what_it_was_built_from():
    releases = [{"name": "0.2 (Windows Support + .br plugin)", "tagName": "windows-cbe6c83-brf27ef28"},
                {"name": "0.3 (CGE regions)", "tagName": "windows-179e900-br930998f"},
                {"name": "Windows build windows-aaaaaaa-brbbbbbb", "tagName": "windows-aaaaaaa-brbbbbbb"}]
    assert release_name.previous_commits(releases) == ("179e900", "930998f")
    assert release_name.previous_commits([{"name": "Windows build x", "tagName": "windows-1234567-br89abcde"}]) is None


def test_only_merge_commits_of_pull_requests_count():
    messages = ["Merge pull request #22 from fernandoaugusto21/fb-cge-region-names\n\nCGE regions",
                "CGE regions: names", "Merge branch 'main' into fb-x", "Merge pull request #23 from x/y",
                "Merge pull request #22 from again"]
    assert release_name.merged_pulls(messages) == [22, 23]


ROADS = [{"name": "Road network roads-20261122-0605", "tagName": "roads-20261122-0605", "createdAt": "2026-11-22T06:05:00Z"},
         {"name": "Road network roads-20261115-0605", "tagName": "roads-20261115-0605", "createdAt": "2026-11-15T06:05:00Z"}]
WINDOWS = [{"name": "0.3 (CGE regions)", "tagName": "windows-179e900-br930998f", "createdAt": "2026-11-20T10:00:00Z"}]


def test_road_releases_do_not_change_the_windows_numbering_or_the_previous_build():
    releases = ROADS + WINDOWS
    assert release_name.next_number([r["name"] for r in releases]) == 4
    assert release_name.previous_commits(releases) == ("179e900", "930998f")
    assert release_name.previous_commits(ROADS) is None


def test_the_windows_prune_step_never_selects_a_road_release():
    """The workflow's prune keeps the newest 10 windows-* releases (its jq filter, in Python)."""
    releases = ROADS + WINDOWS + [
        {"name": f"0.{n}", "tagName": f"windows-{n:07x}-br{n:07x}", "createdAt": f"2026-10-{n:02d}T00:00:00Z"}
        for n in range(1, 13)]
    selected = sorted((r for r in releases if r["tagName"].startswith("windows-")),
                      key=lambda r: r["createdAt"], reverse=True)[10:]
    assert selected and all(r["tagName"].startswith("windows-") for r in selected)
    assert not any(r["tagName"].startswith("roads-") for r in selected)
