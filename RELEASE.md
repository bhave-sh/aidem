# Release runbook

aidem is published to PyPI via GitHub Actions on tag push. This is the
operator's checklist for cutting a release. The whole flow is:
**bump version → push tag → CI publishes → verify**.

## Prerequisites (one-time, already done)

- Trusted Publishing configured on PyPI and TestPyPI for
  `bhave-sh/aidem` + workflow `release.yml` + environment `release`.
- Tag ruleset `release-tags` restricts `v*` tag creation to `bhave-sh`.
- Branch ruleset `main` requires PR + passing `Test` check (you can bypass as owner).
- `release.yml` workflow file committed in `.github/workflows/`.

## Routine release

### 1. Make sure main is green

The latest commit on `main` should have a passing `Test` workflow run.

```bash
gh run list --workflow=Test --limit=3
```

If the latest is not green, fix issues before releasing.

### 2. Bump the version

Edit `pyproject.toml` and `src/aidem/__init__.py`. Keep both in sync:

```bash
# Examples (pick one based on what changed):

# Patch: bugfix
sed -i '' 's/^version = ".*/version = "0.1.1"/' pyproject.toml
sed -i '' 's/^__version__ = ".*/__version__ = "0.1.1"/' src/aidem/__init__.py

# Minor: backward-compatible feature
sed -i '' 's/^version = ".*/version = "0.2.0"/' pyproject.toml
sed -i '' 's/^__version__ = ".*/__version__ = "0.2.0"/' src/aidem/__init__.py

# Major: breaking change
sed -i '' 's/^version = ".*/version = "1.0.0"/' pyproject.toml
sed -i '' 's/^__version__ = ".*/__version__ = "1.0.0"/' src/aidem/__init__.py
```

Also update the `@click.version_option(version="...")` literal in
`src/aidem/cli.py` if you want `aidem --version` to stay in sync
without import resolution.

### 3. Commit and push the bump

```bash
git add pyproject.toml src/aidem/__init__.py src/aidem/cli.py
git commit -m "chore: bump version to 0.1.1"
git push origin main
```

Wait for the `Test` workflow to pass on this commit.

### 4. Create and push the tag

The tag name **must** match the version exactly, prefixed with `v`.

```bash
VERSION=0.1.1
git tag v${VERSION}
git push origin v${VERSION}
```

This triggers `release.yml`.

### 5. Watch the release workflow

```bash
gh run watch --workflow=release.yml
```

The four jobs run sequentially:

1. **build** — `uv build` + `twine check`. ~30 seconds.
2. **publish-testpypi** — uploads to TestPyPI. ~60 seconds (first-run creates the project there).
3. **publish-pypi** — uploads to PyPI. ~60 seconds (first-run creates the project there).
4. **github-release** — creates a GitHub Release with auto-generated notes and attaches the built artifacts.

Each publish job prints SHA-256 hashes of the files it uploads. Copy
them down if you want to verify against the GitHub Release artifacts.

### 6. Verify the release

```bash
# Confirm PyPI has the new version
pip install --dry-run aidem==0.1.1

# Or install in an isolated env to confirm the CLI works
uvx --from aidem==0.1.1 aidem --version
```

Also confirm the GitHub Release exists with artifacts attached:
`https://github.com/bhave-sh/aidem/releases/tag/v0.1.1`

## Recovery: yanking a bad release

PyPI does not allow re-publishing a version, ever. If a release is
broken, **yank** it so `pip install aidem==X` no longer picks it up
(`pip install aidem` upgrades to a newer one unaffected):

```bash
pip install twine
twine register dist/aidem-0.1.1*  # only if the project needs registering
# Then on pypi.org: Manage → Releases → 0.1.1 → Yank
```

Yanking on TestPyPI: same flow at `https://test.pypi.org`.

## Pre-release / test only (no real PyPI publish)

To validate the build pipeline without publishing to real PyPI:

Either:

- Push a **rc** tag (`v0.1.1rc1`) — still publishes both places, so this
  is not a "skip real PyPI" option; it's only rc-named.
- Use **`workflow_dispatch`** on the Actions tab to run the workflow
  manually without a tag — but the publish jobs will then publish the
  current `pyproject.toml` version, which may collide with an existing
  PyPI version. Use this only for retrying a publish after fixing
  metadata, not for dry runs.

For a true dry run, build and check locally instead:

```bash
uv build
uv run twine check dist/*
```

## Common pitfalls

- **"File already exists" on PyPI** — the version is already published. Bump and retry. PyPI is immutable.
- **Trusted publishing "403 Forbidden"** — the trusted-publisher config on PyPI doesn't match exactly. Compare owner/repo/workflow/environment against the failing run.
- **Tag push rejected** — the tag ruleset blocked you. Confirm you're signed in as `bhave-sh`; if you're a future collaborator without bypass, file a release PR instead.
- **`Test` status check missing on PR** — the `Test` workflow file must be on the base branch for branch protection to find the check. If you rename it, update the ruleset's required status check name.
- **3.14 classifier mismatch** — CI only runs 3.11-3.13; if you add a 3.14 classifier, also update `test.yml`'s matrix and run `uv sync --extra dev` to lock compatible wheels.