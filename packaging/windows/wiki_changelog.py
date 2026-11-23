"""The wiki's Changelog page: the Windows builds (the windows-* releases) with their notes, newest first.

The release workflow regenerates it after each release (see docs/RELEASING.md). This module only
builds the Markdown; it takes the releases as JSON, each {"name", "tagName", "publishedAt", "body"}:

    python packaging/windows/wiki_changelog.py --releases releases.json --out DIR [--previous Changelog.md]

--previous is the page as it is in the wiki now. The workflow prunes the oldest builds, so a version
that is no longer a release keeps its section and index row from that page instead of disappearing.
Road releases (roads-*) and releases not named "0.<N>" or "0.<N>.<M>" are left out.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

REPO = "fernandoaugusto21/terrascope-br"
NAME = re.compile(r"^0\.(\d+)(?:\.(\d+))?(?:\s|$)")
HEADING = re.compile(r"^## (0\.\d+(?:\.\d+)?)$")
ROW = re.compile(r"^\| \[(0\.\d+(?:\.\d+)?)\]\(#\d+\) \|")


def version_of(name: str) -> tuple[int, int] | None:
    """(N, M) of "0.<N>" (M = 0) or "0.<N>.<M>"; None for any other name."""
    match = NAME.match((name or "").strip())
    return (int(match.group(1)), int(match.group(2) or 0)) if match else None


def _anchor(number: str) -> str:
    return number.replace(".", "")


def _entry(release: dict) -> tuple[str, str, list[str]]:
    """(version, index row, section lines) of one windows-* release."""
    name = release["name"]
    number = name.split(" ", 1)[0]
    tag = release["tagName"]
    date = release["publishedAt"][:16].replace("T", " ")
    url = f"https://github.com/{REPO}/releases/tag/{tag}"
    title = name[len(number):].strip().removeprefix("(").removesuffix(")")
    # The notes start with "## <version>: changes" and end with the install footer after "\n---\n".
    body = (release.get("body") or "").replace("\r\n", "\n")
    text = body.split("\n---\n")[0].strip()
    text = re.sub(r"^## .*?\n", "", text, count=1).strip()
    text = text.replace("### ", "#### ")
    row = f"| [{number}](#{_anchor(number)}) | {date} | [{tag}]({url}) |"
    section = [f"## {number}", "", f"{date} UTC · {title} · [release]({url})", "", text, ""]
    return number, row, section


def _previous(page: str) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Index rows and sections of a page, by version. Each section runs to the next version heading."""
    rows: dict[str, str] = {}
    sections: dict[str, list[str]] = {}
    current = None
    for line in page.replace("\r\n", "\n").split("\n"):
        heading = HEADING.match(line)
        if heading:
            current = heading.group(1)
            sections[current] = [line]
        elif current is not None:
            sections[current].append(line)
        elif (row := ROW.match(line)):
            rows[row.group(1)] = line
    for lines in sections.values():
        while lines and not lines[-1].strip():
            lines.pop()
    return rows, sections


def changelog_page(releases: list[dict], previous: str | None = None) -> str:
    """The Changelog page (Markdown) for the releases, in any order."""
    entries = []
    for release in releases:
        if not str(release.get("tagName") or "").startswith("windows-"):
            continue
        if version_of(release.get("name")) is None:
            continue
        number, row, section = _entry(release)
        entries.append((version_of(number), number, row, section))
    if previous:
        rows, sections = _previous(previous)
        published = {number for _, number, _, _ in entries}
        for number, section in sections.items():
            if number not in published and number in rows and version_of(number) is not None:
                entries.append((version_of(number), number, rows[number], section + [""]))
    entries.sort(key=lambda entry: entry[0], reverse=True)

    lines = [
        "# Changelog",
        "",
        "The Windows builds of terrascope-br (terrascope.exe with the plugin), newest first. Each version lists the pull",
        "requests merged since the previous one, in the core (fernandoaugusto21/Terrascope) and in the plugin. A version",
        f"`0.N.M` is a hotfix of `0.N`. The same notes are on each [release](https://github.com/{REPO}/releases).",
        "",
        "Install the newest build with Scoop:",
        "",
        f"    scoop install https://github.com/{REPO}/releases/latest/download/terrascope-br.json",
        "",
    ]
    index = ["| Version | Date (UTC) | Release |", "|---|---|---|"] + [row for _, _, row, _ in entries]
    sections = [line for _, _, _, section in entries for line in section]
    return "\n".join(lines + index + [""] + sections).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--releases", type=Path, required=True,
                        help="JSON list of {name, tagName, publishedAt, body} (any releases; windows-* are kept)")
    parser.add_argument("--out", type=Path, required=True, help="directory that gets Changelog.md")
    parser.add_argument("--previous", type=Path, help="the page as it is in the wiki now (keeps pruned versions)")
    args = parser.parse_args(argv)
    releases = json.loads(args.releases.read_text(encoding="utf-8") or "[]") or []
    previous = args.previous.read_text(encoding="utf-8") if args.previous and args.previous.exists() else None
    page = changelog_page(releases, previous)
    args.out.mkdir(parents=True, exist_ok=True)
    # LF on every platform: the page is committed to the wiki from a Linux runner.
    with (args.out / "Changelog.md").open("w", encoding="utf-8", newline="\n") as file:
        file.write(page)
    print(f"Changelog written to {args.out / 'Changelog.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
