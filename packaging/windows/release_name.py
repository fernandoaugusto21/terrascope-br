"""Name of the next Windows build release: "0.<N+1> (<summary>)".

N is the highest "0.<N>" among the names of the earlier releases (0 when there
is none); the summary joins the titles of the pull requests merged since the
last release, in the core and in this repository, with " + ", cut to MAX_SUMMARY
characters. Example: "0.3 (CGE regions: names, flood points + Side panel info box)".

Usage (the workflow passes JSON from `gh`):
    python packaging/windows/release_name.py --releases releases.json --pulls pulls.json
where releases.json is a list of {"name": ...} and pulls.json a list of {"title": ...}.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

MAX_SUMMARY = 120
FALLBACK_SUMMARY = "rebuild"
NUMBER = re.compile(r"^0\.(\d+)(?:\s|$)")


def next_number(names: list[str]) -> int:
    numbers = [int(match.group(1)) for name in names if (match := NUMBER.match(name.strip()))]
    return max(numbers, default=0) + 1


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
    parser.add_argument("--releases", required=True, type=Path)
    parser.add_argument("--pulls", required=True, type=Path)
    args = parser.parse_args(argv)
    releases = json.loads(args.releases.read_text(encoding="utf-8") or "[]")
    pulls = json.loads(args.pulls.read_text(encoding="utf-8") or "[]")
    print(release_name([release.get("name") or "" for release in releases],
                       [pull.get("title") or "" for pull in pulls]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
