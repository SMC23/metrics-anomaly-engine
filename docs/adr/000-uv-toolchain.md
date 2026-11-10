# ADR-000: Use uv to pin Python 3.12 and lock dependencies

## Status

Accepted.

## Context

The project requires Python 3.12, but development moved to WSL2 (Ubuntu), where the system
interpreter is 3.14. `requires-python = ">=3.12,<3.13"` rejects that interpreter, so a local
3.12 is needed without changing the system one.

`pyproject.toml` only declares lower bounds. Without a lock file, local machines, CI and the
Docker image can each resolve different versions, which conflicts with the project priority of
reproducible results.

## Decision

Use [uv](https://docs.astral.sh/uv/) as the Python toolchain manager:

- `backend/.python-version` selects Python 3.12; uv installs it when missing.
- `backend/uv.lock` records exact versions and hashes and is committed.
- `make install`, `make lint`, `make test`, CI and the Docker build all use `--locked`, so a
  lock file out of sync with `pyproject.toml` is an error instead of a silent re-resolution.
- The uv version is pinned to the same exact release in CI and in the `Dockerfile`.
- Dependency sets stay as the existing `dev` and `eval` extras. The image installs neither.

uv is a development and build tool. The application does not import it, and it is not present
in the final image.

## Alternatives considered

| Alternative | Why not |
|---|---|
| pyenv + pip + venv | Needs compiling Python locally and adds no lock file by itself. |
| pip-tools | Solves locking but not the interpreter, and needs one output file per dependency set. |
| Poetry | Also locks, but does not install Python and would require rewriting project metadata. |
| Keep pip without a lock | Builds would not be reproducible. |

## Consequences

- Contributors need uv installed. Everything else is handled by `make install`.
- Upgrading dependencies is an explicit step (`uv lock --upgrade`) that shows up as a diff.
- Upgrading uv means changing the version in both `ci.yml` and the `Dockerfile`.
- The stack gains one tool that is not a runtime dependency; `CLAUDE.md` section 4 lists it.
