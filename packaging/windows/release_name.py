"""Name and notes of the next Windows build release: "0.<N> (<summary>)", or "0.<N>.<M> (...)"
for a hotfix.

The version follows the highest one among the names of the earlier releases ("0.<N>" or
"0.<N>.<M>"; none = 0.0). A build whose pull requests all come from hotfix branches
("hf-*") is the next patch of that version (0.9 -> 0.9.1 -> 0.9.2); any other build is the
next minor (0.9.2 -> 0.10). The pull requests are those whose merge commits are in this
build and not in the previous release's: the previous release's tag
("windows-<core commit>-br<plugin commit>") says what it was built from, and the workflow
compares that with this build in each repository.

The summary joins the pull request titles with " + ", cut to MAX_SUMMARY characters
("rebuild" when nothing was merged). The notes are the release's changelog: each pull
request with its link and the first paragraph of its description, core first.

Usage (the workflow passes JSON from `gh`):
    python release_name.py previous --releases releases.json
        prints "<core commit> <plugin commit>" of the previous release (nothing when none)
    python release_name.py pulls --messages messages.json
        prints the numbers of the pull requests merged by those commits
    python release_name.py name --releases releases.json --pulls pulls.json
        prints the name; pulls.json is a list of {"repo", "number", "title", "branch", "body", "url"}
    python release_name.py notes --pulls pulls.json --version 0.9.1 [--install URL]
        prints the release notes (Markdown)
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

MAX_SUMMARY = 120
MAX_DESCRIPTION = 600
FALLBACK_SUMMARY = "rebuild"
HOTFIX_PREFIX = "hf-"
CORE_REPO = "fernandoaugusto21/Terrascope"
NUMBER = re.compile(r"^0\.(\d+)(?:\.(\d+))?(?:\s|$)")
TAG = re.compile(r"^windows-([0-9a-f]{7,40})-br([0-9a-f]{7,40})$")
MERGE = re.compile(r"^Merge pull request #(\d+) ")


def version_of(name: str) -> tuple[int, int] | None:
    """(N, M) of "0.<N>" (M = 0) or "0.<N>.<M>"; None for any other name."""
    match = NUMBER.match((name or "").strip())
    return (int(match.group(1)), int(match.group(2) or 0)) if match else None


def version_text(version: tuple[int, int]) -> str:
    return f"0.{version[0]}" if version[1] == 0 else f"0.{version[0]}.{version[1]}"


def next_version(names: list[str], hotfix: bool = False) -> tuple[int, int]:
    highest = max((version for name in names if (version := version_of(name)) is not None), default=(0, 0))
    return (highest[0], highest[1] + 1) if hotfix and highest != (0, 0) else (highest[0] + 1, 0)


def next_number(names: list[str]) -> int:
    """N of the next minor version (kept for callers of the first version)."""
    return next_version(names)[0]


def is_hotfix(pulls: list[dict]) -> bool:
    """Every pull request of the build comes from a hotfix branch (and there is one)."""
    return bool(pulls) and all(str(pull.get("branch") or "").startswith(HOTFIX_PREFIX) for pull in pulls)


def previous_commits(releases: list[dict]) -> tuple[str, str] | None:
    """(core commit, plugin commit) of the release with the highest version, read from its tag."""
    numbered = [(version, release) for release in releases if (version := version_of(release.get("name"))) is not None]
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


def release_name(names: list[str], titles: list[str], hotfix: bool = False) -> str:
    return f"{version_text(next_version(names, hotfix))} ({summary(titles)})"


def first_paragraph(body: str, limit: int = MAX_DESCRIPTION) -> str:
    """The first paragraph of a pull request description, on one line."""
    for paragraph in re.split(r"\n\s*\n", (body or "").replace("\r\n", "\n")):
        text = " ".join(paragraph.split())
        if text:
            return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
    return ""


def notes(pulls: list[dict], version: str, install_url: str = "") -> str:
    """The release's changelog (Markdown): core pull requests, then the plugin's."""
    lines = [f"## {version}: changes", ""]
    if not pulls:
        lines += ["No pull request merged since the previous release (a rebuild).", ""]
    groups = (("Terrascope (core)", [p for p in pulls if p.get("repo") == CORE_REPO]),
              ("terrascope-br (plugin)", [p for p in pulls if p.get("repo") != CORE_REPO]))
    for heading, group in groups:
        if not group:
            continue
        lines += [f"### {heading}", ""]
        for pull in group:
            title = " ".join(str(pull.get("title") or "").split())
            link = f"[#{pull.get('number')}]({pull.get('url')})" if pull.get("url") else f"#{pull.get('number')}"
            lines.append(f"- **{title}** ({link})")
            description = first_paragraph(pull.get("body") or "")
            if description:
                lines.append(f"  {description}")
        lines.append("")
    lines += ["---", "", "terrascope.exe with this plugin; the tag names the core and plugin commits it was built from."]
    if install_url:
        lines.append(f"Install the newest build: `scoop install {install_url}`")
    return "\n".join(lines).rstrip() + "\n"


def _read(path: Path | None, default):
    return json.loads(path.read_text(encoding="utf-8") or "null") if path else default


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("previous", "pulls", "name", "notes"))
    parser.add_argument("--releases", type=Path)
    parser.add_argument("--pulls", type=Path, help="name / notes: JSON list of the build's pull requests")
    parser.add_argument("--titles", type=Path, help="name: JSON list of titles (first version; no hotfix detection)")
    parser.add_argument("--messages", type=Path, help="pulls: JSON list of commit messages")
    parser.add_argument("--version", help="notes: the version the notes are for")
    parser.add_argument("--install", default="", help="notes: the Scoop manifest URL")
    args = parser.parse_args(argv)
    if args.action == "pulls":
        print(" ".join(str(number) for number in merged_pulls(_read(args.messages, []) or [])))
        return 0
    if args.action == "notes":
        print(notes(_read(args.pulls, []) or [], args.version or "", args.install), end="")
        return 0
    releases = _read(args.releases, []) or []
    if args.action == "previous":
        commits = previous_commits(releases)
        if commits:
            print(*commits)
        return 0
    pulls = _read(args.pulls, []) or []
    titles = [pull.get("title") or "" for pull in pulls] if args.pulls else (_read(args.titles, []) or [])
    print(release_name([release.get("name") or "" for release in releases], titles, is_hotfix(pulls)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
