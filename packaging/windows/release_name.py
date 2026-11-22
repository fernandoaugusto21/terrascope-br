"""Name of the next Windows build release: "0.<N+1> (<summary>)".

N is the highest "0.<N>" among the names of the earlier releases (0 when there
is none). The summary joins, with " + ", the titles of the pull requests whose
merge commits are in this build and not in the previous release's: the
previous release's tag ("windows-<core commit>-br<plugin commit>") says what it
was built from, and the workflow compares that with this build in each
repository. Cut to MAX_SUMMARY characters; "rebuild" when nothing was merged.
Example: "0.3 (CGE regions: names, flood points + Side panel: size the info box)".

Usage (the workflow passes JSON from `gh`):
    python release_name.py previous --releases releases.json
        prints "<core commit> <plugin commit>" of the previous numbered release (nothing when none)
    python release_name.py name --releases releases.json --titles titles.json
        prints the name; titles.json is a list of pull request titles, oldest first
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

MAX_SUMMARY = 120
FALLBACK_SUMMARY = "rebuild"
NUMBER = re.compile(r"^0\.(\d+)(?:\s|$)")
TAG = re.compile(r"^windows-([0-9a-f]{7,40})-br([0-9a-f]{7,40})$")
MERGE = re.compile(r"^Merge pull request #(\d+) ")


def _number(name: str) -> int | None:
    match = NUMBER.match((name or "").strip())
    return int(match.group(1)) if match else None


def next_number(names: list[str]) -> int:
    return max((number for name in names if (number := _number(name)) is not None), default=0) + 1


def previous_commits(releases: list[dict]) -> tuple[str, str] | None:
    """(core commit, plugin commit) of the release with the highest 0.<N>, read from its tag."""
    numbered = [(number, release) for release in releases if (number := _number(release.get("name"))) is not None]
    if not numbered:
        return None
    match = TAG.match(max(numbered, key=lambda pair: pair[0])[1].get("tagName") or "")
    return (match.group(1), match.group(2)) if match else None


def merged_pulls(messages: list[str]) -> list[int]:
    """Pull request numbers of the "Merge pull request #N from ..." commits, in order."""
    numbers = []
    for message in messages:
        match = MERGE.match(message or "")
        if match and int(match.group(1)) not in numbers:
            numbers.append(int(match.group(1)))
    return numbers


def summary(titles: list[str], limit: int = MAX_SUMMARY) -> str:
    seen: list[str] = []
    for title in titles:
        title = " ".join(str(title).split())
        if title and title not in seen:
            seen.append(title)
    text = " + ".join(seen) or FALLBACK_SUMMARY
    return text if len(text) <= limit else text[: limit - 1].rstrip(" +") + "…"


def release_name(names: list[str], titles: list[str]) -> str:
    return f"0.{next_number(names)} ({summary(titles)})"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("previous", "pulls", "name"))
    parser.add_argument("--releases", type=Path)
    parser.add_argument("--titles", type=Path)
    parser.add_argument("--messages", type=Path, help="pulls: JSON list of commit messages")
    args = parser.parse_args(argv)
    if args.action == "pulls":
        print(" ".join(str(number) for number in merged_pulls(json.loads(args.messages.read_text(encoding="utf-8") or "[]"))))
        return 0
    releases = json.loads(args.releases.read_text(encoding="utf-8") or "[]")
    if args.action == "previous":
        commits = previous_commits(releases)
        if commits:
            print(*commits)
        return 0
    titles = json.loads(args.titles.read_text(encoding="utf-8") or "[]")
    print(release_name([release.get("name") or "" for release in releases], titles))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
