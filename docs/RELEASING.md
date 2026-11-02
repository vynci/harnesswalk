# Releasing

A release is a GitHub Release with a new tag `v<version>` on `main`, made in the GitHub UI.
The tag runs `.github/workflows/release.yml`, which
builds the sdist and the wheel (with the web app inside), installs the wheel in a clean venv
and runs it, publishes it to [PyPI](https://pypi.org/project/harnesswalk/) with trusted
publishing, pushes a `linux/amd64` and `linux/arm64` image to `ghcr.io/vynci/harnesswalk`,
and creates the GitHub Release with the version's changelog section. Nothing in the repo or
its tooling tags or pushes on its own: the owner does both by hand.

## Every release

1. On an up-to-date `main` with a clean tree, prepare it:

   ```sh
   make release-prep VERSION=0.1.0
   ```

   This sets the version in `pyproject.toml`, `harnesswalk/__init__.py` and `uv.lock`, moves the
   `Unreleased` section of `CHANGELOG.md` under `[0.1.0] - <today>`, updates the compare links,
   and prints the commands below. `uv run python tools/release.py prep 0.1.0 --dry-run` shows
   what it would change.
2. Check it locally: `make test`, `make lint`, `make web-build && make wheel-smoke` (the wheel
   from a clean venv, with the app), and `make fresh-clone` (the README in a clean container).
3. Commit and push `main`, then wait for `ci` to pass:

   ```sh
   git commit -am "chore(release): v0.1.0"
   git push origin main
   ```

4. In GitHub, Releases, "Draft a new release": choose "Create new tag" `v0.1.0` on `main`,
   title `Harnesswalk 0.1.0`, leave the notes empty (the workflow fills them from the
   changelog), tick "Set as a pre-release" only for an `a`, `b` or `rc` version, and publish.
   Publishing creates the tag, which runs `release` (PyPI, GHCR, notes and assets on this
   release), and the publish event runs `deploy`, which ships this commit to
   harnesswalk.vynci.dev. A tag pushed from the command line publishes packages but does not
   deploy, because a release created by a workflow token fires no `release` event.
5. Watch the `release` and `deploy` workflows. The `pypi` job waits for an approval when the `pypi`
   environment has required reviewers (recommended). When it is done:
   `pip install harnesswalk==0.1.0`, `docker pull ghcr.io/vynci/harnesswalk:0.1.0`, and the
   release page lists the sdist and the wheel.

A tag with `a`, `b` or `rc` (`v0.2.0rc1`) is published as a pre-release: PyPI marks it so, the
image gets only its exact tag (no `0.2` or `latest`), and the GitHub Release is a pre-release.

If the workflow fails after PyPI accepted the upload, do not move the tag: PyPI never takes
the same version twice. Fix forward with the next patch version.

Spec releases are separate (`spec/VERSIONING.md`): bump `SPEC_VERSION` in its own commit and
move `spec/CHANGELOG.md` by hand.

## One-time setup (owner)

Do these once, before the first tag.

### PyPI trusted publishing

Trusted publishing lets the workflow upload without any token stored in GitHub: PyPI trusts
an OpenID Connect token that GitHub issues to that one workflow and environment.

1. Sign in at [pypi.org](https://pypi.org) (turn on two-factor authentication if it is not).
2. The project does not exist yet, so add a *pending* publisher: Account settings, Publishing,
   "Add a new pending publisher", GitHub tab:
   - PyPI project name: `harnesswalk`
   - Owner: `vynci`
   - Repository name: `harnesswalk`
   - Workflow name: `release.yml`
   - Environment name: `pypi`
3. The first successful run creates the project and turns the pending publisher into a normal
   one (Project, Settings, Publishing). Nothing else is needed on PyPI.

Optional but useful: do the same on [test.pypi.org](https://test.pypi.org) with environment
`testpypi` if you ever want a dry run; the workflow publishes only to PyPI today.

### GitHub repository settings

1. **Environment `pypi`**: Settings, Environments, New environment `pypi`. Add yourself as a
   required reviewer and limit deployment branches and tags to `v*` tags. The PyPI publisher
   above names this environment, so only this workflow, on this environment, can publish.
2. **Actions permissions**: Settings, Actions, General, Workflow permissions: leave the default
   read-only token. The workflow asks for `packages: write`, `contents: write` and
   `id-token: write` only in the jobs that need them.
3. **Tag protection** (recommended): Settings, Rules, Rulesets, a tag ruleset for `v*` that only
   you can create, so nobody else can start a release.
4. **Discussions**: turn on Settings, General, Features, Discussions, which the issue chooser
   (`.github/ISSUE_TEMPLATE/config.yml`) links to for questions; or remove that link.
5. No repository variables or secrets are needed: the image push uses the workflow's own
   `GITHUB_TOKEN`, and PyPI uses trusted publishing.

### GHCR (the container image)

1. Nothing to create beforehand: the first `image` job pushes `ghcr.io/vynci/harnesswalk` and
   GitHub makes the package, linked to the repository through the image's
   `org.opencontainers.image.source` label.
2. After the first push, open the package (your profile, Packages, `harnesswalk`), Package
   settings: set its visibility to **Public** (packages start private), and check that the
   repository has write access under "Manage Actions access" (it does when the package was
   created by this repository's workflow).
3. Pull it to check: `docker run --rm -p 8000:8000 ghcr.io/vynci/harnesswalk:0.1.0`, then
   open http://127.0.0.1:8000 (it needs a model; add `-e HARNESSWALK_PROVIDER=fake` for the
   scripted one).
