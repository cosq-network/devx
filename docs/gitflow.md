# GitFlow and Semantic Versioning

The repository uses GitFlow-style branches with semantic versioning managed by
`.github/workflows/version.yml` and `scripts/version.py`.

| Branch | Version behavior | Example |
|---|---|---|
| `feature/*` | Preserve the current version | `feature/cache` |
| `develop` | Move to the next minor development version once | `0.2.0-dev.0` |
| `release/X.Y` | Set the release version with patch `0` | `release/1.2` → `1.2.0` |
| `release/X.Y.Z` | Set the exact release version | `release/1.2.0` → `1.2.0` |
| `hotfix/X.Y.Z` | Set the exact patch version | `hotfix/1.2.1` → `1.2.1` |
| `main` | Preserve the stable version merged from release/hotfix | `1.2.0` |

The version workflow updates both `pyproject.toml` and `src/devx/__init__.py`.
It commits changes as the GitHub Actions bot with `[skip ci]` to avoid a loop.
The workflow only runs automatically for `develop`, `release/*`, and
`hotfix/*`; it can also be run manually with a branch name.

## Release flow

1. Create `feature/*` from `develop`.
2. Merge completed features into `develop`.
3. Create `release/X.Y` from `develop`; the workflow sets `X.Y.0`.
4. Validate the release candidate through CI.
5. Once the release branch is pushed, `version.yml` tags it `vX.Y.0`
   automatically and `release.yml` builds the wheel/sdist, signs them with
   Sigstore, and publishes to PyPI using OIDC trusted publishing.
6. Verify the build and PyPI upload, then merge the release branch into
   `main` and back into `develop`.

Publishing is configured via PyPI **trusted publishing** (`release.yml`):
no API token is checked in. Before the first release, register the publisher
on PyPI (project `devx-coding-agent` → *Trusted Publishers* → *Add
publisher*) with workflow name `release.yml`, environment `release`, and the
GitHub owner/repository owning this code. The `publish` job requires
`id-token: write` (present in `release.yml`) and the `changelog` job falls
back to full-history output when no previous tag exists, so the first
release can go out without a manual token or a hand-written changelog.

For urgent fixes, create `hotfix/X.Y.Z` from `main`, validate it, merge it
back into both `main` and `develop`, then tag the resulting `main` commit.
