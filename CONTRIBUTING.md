# Contributing to Open House

Thanks for looking. This file covers how to get a working checkout, how to run
the checks, and what the repository expects of a change.

## Getting a checkout

Open House is built and tested against Python 3.12 and Node 22 or newer. It uses
[uv](https://docs.astral.sh/uv/) for the Python environment; there is no
committed Python lock, so `uv sync` resolves the set on the day.

```console
uv sync --all-groups
```

`--all-groups` pulls in the `dev` group, which is where the linters, the type
checker and `pre-commit` live. Home Assistant itself is not a dependency of the
Python environment: the engine, the adapter's pure parts, the tooling and the
whole test suite run without it, and the container is how the integration
itself is exercised.

## Running the checks

The two commands CI runs on the Python side:

```console
uv run python -m pytest -q
uv run python -m tools.catalog.cli validate
```

`oh-catalog validate` is every invariant the repository enforces — the layout,
the engine's import purity, the registry boundary, schema succession and
immutability, the catalog's own data files, and the licence records — in one
command. It prints nothing on success.

The panel is its own toolchain:

```console
cd panel
npm ci
npm run typecheck
npm run build
npm test
```

`npm ci` and not `npm install`: `panel/package-lock.json` is committed, so `ci`
installs exactly what another developer ran.

`uv run pyright` type-checks strictly over the Python tree. Run it without a
path argument: the `exclude` list under `[tool.pyright]` in `pyproject.toml`
wins over an explicit path, so `pyright engine` would check no files at all and
report success for a tree it never looked at.

## The hooks

`.pre-commit-config.yaml` runs `ruff check --fix`, `ruff format`, `pyright` and
`oh-catalog validate`. Install them once:

```console
uv run pre-commit install
```

Every hook runs the project virtualenv's own copy of each tool, so a shell with
`.venv` on `PATH` (or `uv run`) is the only precondition. To run them all by
hand, as CI does:

```console
uv run pre-commit run --all-files
```

## The container

`docker/docker-compose.yml` stands up a Home Assistant instance with the
integration, a Mosquitto broker and the Node-RED the integration can embed. The
whole thing is drivable headlessly — `tools/ha/` holds the onboarding and control
helpers — which is how the end-to-end walk and the browser checks are run.

```console
docker compose -f docker/docker-compose.yml up -d
```

The UI is on <http://localhost:8123> once the container reports ready.

## What a change should carry

- **A test that fails without the change.** A check proven only against the tree
  that already passes is a check nobody has seen fail. Where the repository has a
  fixture pair — a `fake_root` for violating input and the real tree for the
  committed one — use both, because they answer different questions.
- **A reason in the code.** The comments in this repository say *why* a thing is
  the way it is, especially where the alternative was tried and was wrong. That
  is the house style; a terser diff that loses the reason loses the point.
- **No secrets.** Tokens, the Store's admin credentials and the Home Assistant
  runtime state are all gitignored. Keep them that way.

## Licence

The repository carries no `LICENSE` file yet — the licence is pending. By
contributing you agree your contribution may be distributed under whatever
licence the project settles on.
