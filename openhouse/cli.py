"""The CLI: the ten registry operations, generated, plus the scenario runner.

`control-surface` asks for a Typer application installed as `oh-house` that
exposes exactly one subcommand per registry operation, and asks that those
subcommands be *generated* rather than hand-written, because a subcommand written
beside the registry is a second place for an operation's parameters to be
declared and the disagreement that produces is silent. So every command below is
built from `OPERATIONS`: its signature, its help text and its option defaults all
come from the descriptor, and its body is one line of forwarding to the facade.

Exactly the ten, and not the house-control facility's four beside them. The
registry's sentence is "exactly one subcommand per registry operation", and
`operations.py` fixes the registry at the ten decision-driving operations --
`add_entity`, `remove_entity`, `set_availability` and `restart` are the port's
control face, reachable through the facade and through a scenario's step verbs,
and a CLI that also spelled them would make "the registry" mean two things.

What the CLI does *not* do is decide anything. The one thing that looks like
logic is `--json`, and it is serialisation rather than behaviour: the requirement
names it as this face's own (`"SHALL accept --json to emit its result as a single
JSON object"`), and a result that could not be written to stdout would make the
operation unusable from a shell. Everything else is binding: an argument is read,
converted to the kind the registry declares, and passed on.

`scenario run` is the runner's entry point and not an eleventh operation. It
drives the same operations an agent drives, so it is a *client* of the registry,
and it is excluded from the registry comparison for that reason
(`scenario-runner`). It takes a file or a directory, because the corpus is a set
of seeds and a corpus that could only be run one file at a time would be a
reading list.
"""

# Typer's whole API is a call in an argument default -- there is no other way to
# declare an option, and the generation below needs an option as a *value* rather
# than as a decorator, since it is built from the registry at import time.
# ruff: noqa: B008

from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, cast

import typer
import yaml
from typer.models import OptionInfo

from engine.vocabulary import Vocabulary
from sim.fixtures import DEFAULT_SEED, DEFAULT_STARTED_AT
from sim.scenario import LoadError, ScenarioFailed
from tools.catalog import paths

from .facade import open_session
from .operations import OPERATIONS, Operation
from .results import jsonable, result_document
from .scenarios import run_corpus, run_file

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .facade import OpenHouse

__all__ = ["app", "main"]

#: The name the console script installs, and the name the usage line prints.
PROG = "oh-house"

#: The parameter kinds that arrive from a shell as JSON text. A shell has no
#: object literal, so a document parameter is text the CLI parses into the shape
#: the registry declares -- a conversion, not a behaviour, and the same one every
#: face makes in its own transport's terms.
_JSON_KINDS = frozenset({"object", "array"})


def _empty_house() -> Mapping[str, object]:
    """The house a session is opened over when the caller names neither a fixture
    nor a file.

    Not a fixture, because a fixture is one of four *built* houses and "nothing"
    is not one of them; and not literally roomless, because
    `schemas/house/1.0.0.json` gives `rooms` a `minItems` of one, so the smallest
    house the format can express is one room binding no slot. This one binds
    nothing and makes no house-scope slot available, so no behaviour can resolve
    anything in it -- which is what "empty" has to mean for a session that exists
    to be read (`snapshot`, `get_decision_log`) or to have a pack checked against
    it.

    A function rather than a module constant so each session gets its own
    document: the collections here are lists, because JSON Schema reads a tuple as
    a mapping's value rather than as an array, and a shared list handed to every
    caller would be a document one of them could edit for all the others.
    """
    room: Mapping[str, object] = {
        "id": "empty",
        "name": "Empty",
        "type": "hallway",
        "bindings": {},
    }
    slots: list[str] = []
    return {"name": "empty", "rooms": [room], "house_scope": {"slots": slots}}


class CLIError(Exception):
    """A failure this face reports, naming what it was about and why.

    The two halves travel apart as well as together: a caller reading the code
    gets `about` -- the operation or `session` -- and `reason` -- the cause --
    without parsing the sentence for them, and `_fail` writes the sentence. The
    requirement asks for both by name ("with a message naming the offending
    operation and its cause").

    A plain exception rather than the framework's own error type, because the
    only thing that has to be true of a reported failure is that it reaches
    `_fail`: nothing here depends on Typer catching it, so nothing here depends
    on which exception class Typer happens to catch.
    """

    def __init__(self, about: str, reason: str) -> None:
        self.about = about
        self.reason = reason
        super().__init__(f"{about}: {reason}")


def _fail(about: str, reason: str) -> NoReturn:
    """Report a failure the way every subcommand reports one, and exit non-zero.

    One function so all eleven routes out of a command -- ten operations and the
    runner -- word a failure the same, and so the exit is a single statement
    rather than a rule each of them has to remember. `NoReturn` because every
    caller's next line assumes it did not come back.
    """
    typer.echo(f"Error: {about}: {reason}", err=True)
    raise typer.Exit(1)


# -- The session a subcommand acts on ---------------------------------------


class Session:
    """The global options that select the house a subcommand's session opens over.

    A group callback's five options rather than the same five on every
    subcommand, because the choice is the *session's* and not any one operation's:
    `oh-house --fixture minimal advance_time --minutes 5` names one house and one
    call, and repeating the options would be ten places for them to differ. The
    seed and the clock start travel here for the same reason -- they are a run's
    fixed inputs, and two calls of one run have to be at the same ones.
    """

    def __init__(
        self,
        *,
        fixture: str | None = None,
        house: Path | None = None,
        root: Path | None = None,
        seed: int | None = None,
        started_at: str | None = None,
    ) -> None:
        self.fixture = fixture
        self.house = house
        self.root = paths.ROOT if root is None else root
        self.seed = seed
        self.started_at = started_at

    def vocabulary(self) -> Vocabulary:
        """The corpus vocabulary, read from the root this session was pointed at."""
        return Vocabulary.load(self.root)

    def opening(self) -> tuple[int, datetime]:
        """The seed and instant a session is opened at, resolving this face's defaults.

        Resolution happens here rather than at each use because a caller that
        named neither has asked for *the* defaults, and the defaults are the
        fixtures' -- the same seed and the same instant a scenario's `given` block
        would leave unstated.
        """
        started = (
            DEFAULT_STARTED_AT if self.started_at is None else _instant(self.started_at)
        )
        return (DEFAULT_SEED if self.seed is None else self.seed), started

    def opened(self) -> OpenHouse:
        """The session these options describe, or a `CLIError` naming what is wrong.

        The house is a fixture's name, a document read from a file, or -- when
        neither was given -- an empty house.
        """
        if self.fixture is not None and self.house is not None:
            raise CLIError(
                "session",
                "a fixture and a house file were both named; a session is opened "
                "over one house",
            )
        seed, started = self.opening()
        house: str | Mapping[str, object]
        if self.fixture is not None:
            house = self.fixture
        elif self.house is not None:
            house = _read_house(self.house)
        else:
            house = _empty_house()
        try:
            return open_session(
                house=house,
                vocabulary=self.vocabulary(),
                seed=seed,
                started_at=started,
            )
        except Exception as error:
            raise CLIError("session", f"{type(error).__name__}: {error}") from error


app = typer.Typer(
    name=PROG,
    help="Drive one Open House session: the control surface's ten operations.",
    no_args_is_help=True,
    add_completion=False,
)
scenario_app = typer.Typer(
    help="Run scenarios: the runner's own entry point, composed over the ten.",
    no_args_is_help=True,
    add_completion=False,
)
app.add_typer(scenario_app, name="scenario")

#: A single-line key where a long one would be, so `--help` stays readable on the
#: ten commands that all take the same five.
_SESSION_HELP = {
    "fixture": "A fixture house to open the session over, by name.",
    "house": "A house document to open the session over.",
    "root": "The repository root the corpus is read from.",
    "seed": "The run's seed, if it is not the default.",
    "started_at": "The virtual instant the run starts at, ISO 8601.",
}


@app.callback()
def _session_options(
    ctx: typer.Context,
    fixture: str | None = typer.Option(
        None, "--fixture", help=_SESSION_HELP["fixture"]
    ),
    house: Path | None = typer.Option(None, "--house", help=_SESSION_HELP["house"]),
    root: Path | None = typer.Option(None, "--root", help=_SESSION_HELP["root"]),
    seed: int | None = typer.Option(None, "--seed", help=_SESSION_HELP["seed"]),
    started_at: str | None = typer.Option(
        None, "--started-at", help=_SESSION_HELP["started_at"]
    ),
) -> None:
    """Open House: one session, driven from a shell.

    The five options above select the session the subcommand acts on: a fixture by
    name, a house document, or neither for an empty house, plus the run's seed and
    the instant its clock starts at. They come before the subcommand:
    `oh-house --fixture minimal advance_time --minutes 5`.
    """
    ctx.obj = Session(
        fixture=fixture,
        house=house,
        root=root,
        seed=seed,
        started_at=started_at,
    )


# -- The generated operation subcommands ------------------------------------


def _annotation(kind: str) -> type:
    """The Python type a registry kind arrives as on a command line."""
    if kind in _JSON_KINDS:
        return str
    return {"string": str, "integer": int, "number": float, "boolean": bool}[kind]


def _option(operation: Operation, name: str) -> OptionInfo:
    """A parameter's `typer.Option`, from the registry descriptor.

    `required` and the default come straight from the descriptor, so a parameter
    the registry marks optional is optional here and a required one is not
    silently defaulted -- which is the drift the generation exists to prevent.
    """
    parameter = next(item for item in operation.parameters if item.name == name)
    spelling = f"--{name.replace('_', '-')}"
    if parameter.required:
        return typer.Option(..., spelling, help=parameter.description)
    return typer.Option(parameter.default, spelling, help=parameter.description)


def _signature(operation: Operation) -> inspect.Signature:
    """The command's signature: the context, the registry's parameters, and `--json`.

    Typer reads a callback's signature, so declaring one is how a command is
    generated without `exec`: the descriptor in, an `--hours`, an `--entity-id`
    and their help text out. The two parameters the registry knows nothing about
    are stated here and nowhere else -- `ctx` is Typer's own injection and is not
    an option at all, and `json` is the one flag the CLI requirement adds to every
    subcommand, which the registry comparison is required to except.
    """
    return inspect.Signature(
        [
            inspect.Parameter(
                "ctx", inspect.Parameter.KEYWORD_ONLY, annotation=typer.Context
            ),
            *(
                inspect.Parameter(
                    parameter.name,
                    inspect.Parameter.KEYWORD_ONLY,
                    default=_option(operation, parameter.name),
                    annotation=_annotation(parameter.kind),
                )
                for parameter in operation.parameters
            ),
            inspect.Parameter(
                "json",
                inspect.Parameter.KEYWORD_ONLY,
                default=typer.Option(
                    False, "--json", help="Emit the result as one line of JSON."
                ),
                annotation=bool,
            ),
        ]
    )


def _command(operation: Operation) -> Callable[..., None]:
    """The subcommand for one operation: the descriptor's signature, and the exit rules.

    The body is where "exit non-zero with a message naming the offending operation
    and its cause" lives, so all ten report a failure the same way and a handler
    that raised cannot escape as a traceback.
    """

    def run(ctx: typer.Context, **values: object) -> None:
        try:
            session = ctx.obj
            if not isinstance(session, Session):
                raise CLIError(operation.name, "the session options were not parsed")
            result = operation.handler(session.opened(), **_values(operation, values))
        except CLIError as error:
            _fail(error.about, error.reason)
        except Exception as error:
            # Anything the operation itself raised: the operation is what failed,
            # and its exception's own words are the cause.
            _fail(operation.name, f"{type(error).__name__}: {error}")
        _emit(
            result_document(operation.name, result),
            as_json=bool(values.get("json")),
        )

    run.__name__ = operation.name
    run.__doc__ = f"{operation.summary}\n\n{operation.scope}"
    run.__signature__ = _signature(operation)  # type: ignore[attr-defined]
    return run


def _values(operation: Operation, values: Mapping[str, object]) -> dict[str, object]:
    """The command's arguments as the keywords the registry's handler takes.

    A document-kind parameter arrives as text and is parsed here; a parameter that
    is absent is left out rather than passed as `None`, so the handler's own
    default is the one that applies.
    """
    payload: dict[str, object] = {}
    for parameter in operation.parameters:
        if parameter.name not in values:
            continue
        value = values[parameter.name]
        if parameter.kind not in _JSON_KINDS or not isinstance(value, str):
            payload[parameter.name] = value
            continue
        try:
            payload[parameter.name] = json.loads(value)
        except json.JSONDecodeError as error:
            raise CLIError(
                operation.name, f"{parameter.name} is not JSON: {error}"
            ) from error
    return payload


for _operation in OPERATIONS.values():
    app.command(name=_operation.name)(_command(_operation))


# -- The scenario runner's entry point --------------------------------------


@scenario_app.command("run")
def scenario_run(
    ctx: typer.Context,
    path: Path = typer.Argument(
        ..., help="A scenario file, or a directory of them to run as a corpus."
    ),
    seed: int | None = typer.Option(None, "--seed", help="Override the given seed."),
    json_output: bool = typer.Option(
        False, "--json", help="Emit the run, or the failure, as one line of JSON."
    ),
) -> None:
    """Run a scenario, or every scenario in a directory, and report what happened.

    The house, the seed and the clock start come from each scenario's own `given`
    block; `--seed` overrides the seed for every scenario in a corpus, and the
    override is applied by opening the session at it rather than by rewriting the
    scenario, so the result names the seed the run actually used.

    A failing run writes the failure report -- one JSON object naming the decision
    the scenario disagreed with, the log slice up to it, and the inputs that
    replay it -- to stdout, its one-line summary to stderr, and exits non-zero.
    """
    session = ctx.obj
    try:
        if not isinstance(session, Session):
            raise CLIError("scenario run", "the session options were not parsed")
        if session.fixture is not None or session.house is not None:
            raise CLIError(
                "scenario run",
                "a scenario's `given` block names the house; --fixture and "
                "--house would name a second one",
            )
        if not path.exists():
            raise CLIError("scenario run", f"{path} does not exist")
        started = (
            _instant(session.started_at) if session.started_at is not None else None
        )
        vocabulary = session.vocabulary()
        runs = (
            run_corpus(path, vocabulary=vocabulary, seed=seed, started_at=started)
            if path.is_dir()
            else (run_file(path, vocabulary=vocabulary, seed=seed, started_at=started),)
        )
    except CLIError as error:
        _fail(error.about, error.reason)
    except ScenarioFailed as failure:
        # The failure is a document and not only a message: the report goes to
        # stdout so a CI job can capture it, and the one-line summary to stderr so
        # a person reading the log sees it without piping through `jq`.
        typer.echo(failure.report.summary(), err=True)
        _emit(failure.report.to_document(), as_json=json_output)
        raise typer.Exit(1) from failure
    except LoadError as error:
        _fail("scenario run", str(error))
    except Exception as error:
        _fail("scenario run", f"{type(error).__name__}: {error}")
    _emit(
        {"scenario": str(path), "runs": [jsonable(run) for run in runs]},
        as_json=json_output,
    )


# -- Reading a value, writing a result --------------------------------------


def _instant(text: str) -> datetime:
    """An ISO 8601 instant, or a `CLIError` naming what it was instead."""
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as error:
        raise CLIError(
            "session", f"--started-at {text!r} is not an instant: {error}"
        ) from error
    if moment.tzinfo is None:
        raise CLIError("session", f"--started-at {text!r} has no time zone")
    return moment


def _read_house(path: Path) -> Mapping[str, object]:
    """The house document at `path`, or a `CLIError` naming why it could not be read.

    YAML, read with a safe loader, which is how a house is written everywhere else
    in the corpus -- and YAML carries JSON, so a `.json` house reads too rather
    than being a second format to support. What the document *means* is not read
    here: the schema is the house's own and the failure names it.
    """
    try:
        loaded: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise CLIError("session", f"{path} is not a house document: {error}") from error
    if not isinstance(loaded, dict):
        raise CLIError(
            "session",
            f"{path} is not a house document: a house is a mapping, not "
            f"{type(loaded).__name__}",
        )
    return cast("Mapping[str, object]", loaded)


def _emit(document: object, *, as_json: bool) -> None:
    """Write one result document, on one line or indented.

    The flag selects the *shape* and not the content: both forms carry the same
    single JSON object, because a caller piping `--json` into `jq` and a caller
    reading the default output are looking at the same result.
    """
    if as_json:
        typer.echo(json.dumps(document, sort_keys=True))
    else:
        typer.echo(json.dumps(document, indent=2, sort_keys=True))


def main() -> None:
    """The console script's entry point."""
    app()
