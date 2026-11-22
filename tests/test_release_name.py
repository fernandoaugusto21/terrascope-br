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
