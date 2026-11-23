# Releases

How a change reaches a release of terrascope-br, and the rule that protects `main`.

## The rule: no merge before the checks

`main` is protected. A pull request can be merged only when its checks have run and passed:

- `build`: the Windows build check (`.github/workflows/build-windows.yml`, job "build");
- `test (ubuntu-latest)` and `test (windows-latest)`: the tests (`.github/workflows/ci.yml`).

`publish` is skipped on pull requests and is not required.

Never merge a pull request on "no checks reported". That message means the runs have not been created yet, not that
they passed. Wait for the checks to exist, then for them to pass, then merge. Protection also refuses the merge while a
required check is missing, but the wait is what keeps a script from trying.

The owner can still merge in an emergency, because admins are not held to the rule. Such a merge should say why in the
pull request.

## How a change reaches a release

1. Cut a branch from `main`: `hf-<version>` for a hotfix (for example `hf-0.9.3`), or `fb-<topic>` for a feature. The
   branch name matters for the release name.
2. Open a pull request into `main`. Its first paragraph is the changelog line of the release: write it in plain words,
   because the release notes repeat it.
3. The checks above run on the pull request. They must pass before the merge.
4. Merge with a merge commit (`Merge pull request #n`). Squash and rebase are not used here.
5. The merge starts the Windows build on `main`: a standalone `terrascope.exe` (PyInstaller `--onedir`) with its Scoop
   manifest, published as a GitHub release. The release is named `0.<N> (<pull request titles>)`. When every pull
   request in the build comes from an `hf-*` branch, the release is the next patch instead, `0.<N>.<M>`. The notes list
   each pull request with its first paragraph and its link.
6. Scoop users get the newest build with `releases/latest/download/terrascope-br.json`. Only a build of the core's
   `main` is marked latest.

The weekly road file (`.github/workflows/build-roads.yml`) also runs on pushes to `main` that change its inputs. It
publishes its own `roads-<time>` release and is not an app release.

## The wiki's Changelog page

The [wiki's Changelog page](https://github.com/fernandoaugusto21/terrascope-br/wiki/Changelog) is regenerated after each Windows release: the last step of the `publish` job in `build-windows.yml` runs `packaging/windows/wiki_changelog.py` on the windows-* releases (their names and notes), and commits the page to the wiki when it changed. The page keeps a version's section after the workflow prunes that build, because it reads the page already in the wiki (`--previous`). A failure of that step is a warning on the run and does not fail the release. To regenerate it by hand, make a `releases.json` with a `{name, tagName, publishedAt, body}` object per windows-* release (`gh release list --json name,tagName,publishedAt`, then `gh release view <tag> --json body` for each body), then run `python packaging/windows/wiki_changelog.py --releases releases.json --previous Changelog.md --out DIR` from a clone of the wiki (`https://github.com/fernandoaugusto21/terrascope-br.wiki.git`) and copy `DIR/Changelog.md` over its `Changelog.md`, commit and push.

## Merge procedure (for scripts and agents)

Run from a checkout with `gh` logged in to an account that can merge. Replace `31` with the pull request number:

    PR=31
    count=0
    while [ "${count:-0}" -eq 0 ]; do                  # 1. wait until the checks exist
      sleep 10
      count=$(gh pr checks "$PR" --json name --jq 'length' 2>/dev/null)
    done
    gh pr checks "$PR" --watch                         # 2. wait until they finish; non-zero exit if one fails
    gh pr merge "$PR" --merge                          # 3. merge, only after every check passed

Do not merge on the output of the first `gh pr checks` call alone, and do not treat "no checks reported" as a pass.
