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
It commits changes as the GitHub Actions bot. The version logic is idempotent —
when the branch's version already matches the policy it makes no commit, which
avoids a bump/push loop. The workflow runs automatically for `develop`,
`release/*`, and `hotfix/*`; it can also be run manually with a branch name.

To preview which version a branch would get **without** writing anything:

```bash
python scripts/version.py --branch release/0.2.0   # prints, e.g. 0.2.0
```

---

## How a release is published

Two workflows automate the whole release, from branch push to a signed PyPI
upload — no manual version edits, tags, or API tokens:

- **`.github/workflows/version.yml`** (triggered by pushes to `develop`,
  `release/*`, `hotfix/*`): sets the version, commits it, and — on
  `release/*`/`hotfix/*` — creates and pushes the `vX.Y.Z` tag **pointing at
  the version-bump commit**. If the tag already exists it is a no-op, so
  re-pushing a release branch cannot create a duplicate or fail.
- **`.github/workflows/release.yml`** (triggered by `v*` tag pushes): builds
  the wheel + sdist, signs them with Sigstore, publishes to PyPI using OIDC
  **trusted publishing** (no stored credentials), and generates an SBOM plus a
  changelog artifact. The `publish` job declares `id-token: write`, and the
  `changelog` job falls back to full-history output when no previous tag
  exists, so the **first** release works too.

### Prerequisites (one-time, before the first release)

Register the trusted publisher on PyPI — this takes ~2 minutes and requires no
token or secret:

1. Sign in (or create an account) at [pypi.org](https://pypi.org), verify your
   email, and enable two-factor authentication (required for project
   management).
2. Go to **Account settings → Publishing → Add a new publisher**
   (`https://pypi.org/manage/account/publishing/`) and fill in:

   | Field | Value |
   |---|---|
   | Project name | `devx-coding-agent` |
   | Platform | GitHub |
   | GitHub owner | `cosq-network` |
   | Repository | `devx` |
   | Workflow name | `release.yml` |
   | Environment name | `release` |

3. Confirm the **pending publisher** prompt. Adding a publisher for a project
   that does not exist yet claims the name `devx-coding-agent` so nobody else
   can register it.

The GitHub `release` environment is created automatically the first time the
workflow references it; nothing else is needed on the GitHub side.

---

## Cutting a release (step by step)

### 1. Make sure `develop` is current

```bash
git checkout develop
git pull
```

All completed features must already be merged into `develop`.

### 2. Create and push the release branch

```bash
git checkout -b release/0.2.0 develop
git push origin release/0.2.0
```

### 3. Let the automation do the work

Pushing the branch triggers, in order:

1. **`version.yml`** sets `version = "0.2.0"` in `pyproject.toml` and
   `__version__ = "0.2.0"` in `src/devx/__init__.py`, commits as
   `github-actions[bot]`, and pushes the commit.
2. **`version.yml`** re-fetches the branch tip and pushes tag `v0.2.0` at the
   version-bump commit.
3. **`release.yml`** (fired by the tag) builds wheel + sdist, signs them with
   Sigstore, publishes to PyPI via trusted publishing, and emits SBOM +
   changelog artifacts.

You should see two green workflow runs: one for the branch push, one for the
tag.

### 4. Verify the release

- **GitHub Actions**: both runs are green; the release run produced `sbom` and
  `changelog` artifacts.
- **PyPI**: `https://pypi.org/project/devx-coding-agent/` lists `0.2.0` with
  signed files (Sigstore badges).
- **Fresh-environment smoke test** (use a temp venv; Windows uses
  `.devx-smoke\Scripts\devx`):

  ```bash
  python -m venv .devx-smoke
  .devx-smoke/bin/pip install "devx-coding-agent[all]"
  .devx-smoke/bin/devx --help
  ```

### 5. Finish the release (merge back)

```bash
git checkout main && git pull
git merge release/0.2.0 && git push origin main

git checkout develop && git pull
git merge release/0.2.0 && git push origin develop
```

The next push to `develop` advances the development version to the next minor
line (e.g. `0.3.0-dev.0`) automatically.

Optionally, create a **GitHub Release** from the `v0.2.0` tag and attach the
SBOM artifact from the release run (retained for 90 days), so security
consumers can find it alongside the source.

---

## Hotfixes

```bash
git checkout main && git pull
git checkout -b hotfix/0.2.1 main
# ... apply the fix and commit ...
git push origin hotfix/0.2.1
# → version bumps to 0.2.1, tags v0.2.1, publishes the patch
```

Then merge back into `main` and `develop` exactly as in **step 5** above.

---

## First-release specifics

- The `changelog` job has no previous tag to diff against, so it falls back to
  the full commit history — no hand-written changelog needed.
- Registering the trusted publisher claims the PyPI project name.
- No API token, `TWINE_PASSWORD`, or secret is ever configured or checked in.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Publish step: *"missing or insufficient OIDC token permissions"* | The `publish` job needs `id-token: write` (present in `release.yml`), and the PyPI publisher must match exactly: workflow `release.yml`, environment `release`, correct owner/repo. Re-check the publisher registration. |
| Upload rejected with *"File already exists"* (`400`) | That version is already on PyPI and uploads are immutable. Bump the version (e.g. a `hotfix/X.Y.Z` branch) instead of retrying the same tag. |
| `version.yml` says *"Version is already current"* | Expected: the branch already carries its policy version, so no commit is made. |
| Tag job prints *"Tag vX.Y already exists; nothing to do"* | Expected after the first successful run; re-pushing a release branch is a no-op by design. |
| Release run's `changelog` job failed on an older revision | Before the fix, `git describe` failed on a tag-less repo. The current `release.yml` guards this; make sure you are on the latest `release.yml`. |

> **Warning:** never delete and re-push an already-published tag to "fix" a bad
> upload — the PyPI release is immutable and stays online. Always bump to a new
> version instead.

---

## TestPyPI dry run (optional, before the real release)

To smoke-test the pipeline against the test index:

1. Register the same trusted publisher on [test.pypi.org](https://test.pypi.org)
   (same values; the "project name" is `devx-coding-agent` there too).
2. Temporarily add to the `publish` job in `release.yml`:

   ```yaml
   with:
     verify-metadata: true
     repository-url: https://test.pypi.org/legacy/
   ```

3. Cut the release as above and verify the upload on `test.pypi.org`.
4. Remove the `repository-url` line before cutting the real release. TestPyPI
   and PyPI are separate indexes, so the same version number can be uploaded to
   both.