# Releasing IraLens

A release is four steps: version, verify, build, publish. The first three
are automatable from the repository; the fourth needs the project's PyPI
account, and only that fourth step is not in the repository.

## 1. Version

The version is written in four places and they must agree — a test says so
(`tests/test_release.py::test_the_version_is_the_same_everywhere_it_is_written`):

| where | what |
|---|---|
| `pyproject.toml` | `version = "0.3.5"` |
| `iralens/__init__.py` | `__version__ = "0.3.5"` |
| `iralens/engine/native.py` | the MCP `clientInfo` version |
| `README.md` | the version badge |

The changelog gains a section for the version (`## 0.3.5 — <date>`).

## 2. Verify

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check --select E9,F,B .
.venv/bin/python -m mypy iralens
.venv/bin/python benchmarks/acceptance_17.py
.venv/bin/python benchmarks/run_scraping.py
```

## 3. Build

```bash
.venv/bin/python -m pip install --upgrade build
.venv/bin/python -m build              # writes dist/iralens-0.3.5.tar.gz and dist/iralens-0.3.5-*.whl
.venv/bin/python -m pip install twine && .venv/bin/python -m twine check dist/*
cd dist && sha256sum *.tar.gz *.whl > SHA256SUMS && cd ..
```

`dist/` and `build/` are git-ignored; the artifacts are attached to the
GitHub release, not committed.

## 4. Publish

### 4a. The GitHub release (no credentials needed beyond the repo)

```bash
git tag -a v0.3.5 -m "IraLens 0.3.5"
git push origin v0.3.5
gh release create v0.3.5 --title "IraLens 0.3.5" --notes-file notes.md
```

The assets are attached by the `release.yml` workflow, which builds the
sdist and the wheel from the tagged commit, checks them with `twine`, and
uploads them (with their `SHA256SUMS`) to the release. It runs on the
`release: published` event, and can be re-run by hand with
`workflow_dispatch`.

The release assets are installable on their own:

```bash
pip install https://github.com/pritamduttachoudhury0-collab/IraLens/releases/download/v0.3.5/iralens-0.3.5-py3-none-any.whl
```

### 4b. PyPI (needs the project's PyPI account — one step)

The `iralens` name is **unclaimed** on PyPI, so the project has to be
created before the first upload. The repository already contains everything
the upload needs:

* `.github/workflows/publish.yml` — a *trusted publishing* workflow. It
  exchanges the job's GitHub OIDC token for a short-lived PyPI upload token,
  so no API token is ever stored in the repository.
* `dist/` — the sdist and the wheel, built and checked in step 3.

To enable it, once, in the PyPI account that owns the project:

1. Create the project `iralens` at <https://pypi.org/manage/project/iralens/settings/publishing/>
   (or claim the name from an existing account).
2. Add a **pending publisher** for the repository
   `pritamduttachoudhury0-collab/IraLens` and the workflow
   `.github/workflows/publish.yml` (leave the environment empty — the
   workflow does not declare one).
3. Run the `Publish to PyPI` workflow (`workflow_dispatch`): it builds the
   artifacts, publishes them, and attaches them to the release.
4. To make every future release publish itself, add
   `release: [published]` to the `on:` block of `publish.yml`.

If the project must be created by the account holder, the same artifacts
can be uploaded by hand with `twine upload dist/*` after
`~/.pypirc` holds a `pypi` API token. **No credential is ever stored in
this repository.**

## 5. Installing the latest developed version

Even before PyPI, the newest merged code is installable:

```bash
pip install "iralens @ git+https://github.com/pritamduttachoudhury0-collab/IraLens.git"
```

`main` is the branch that carries every merged change, so this always
installs the latest developed version, not a released snapshot.
