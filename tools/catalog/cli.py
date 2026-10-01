"""`oh-catalog` -- the command line entry point.

Deliberately thin. Every subcommand delegates to a function in a module that a
test imports directly, so that nothing the CLI can do is reachable only through
the CLI, and so the acceptance script in task 8.1 can drive the same code the
pipeline drives.
"""

from __future__ import annotations

import sys
from typing import Annotated

import typer

from . import paths
from .validate import validate_all

app = typer.Typer(
    name="oh-catalog",
    help="Validate and generate the Open House catalog.",
    no_args_is_help=True,
    add_completion=False,
)


@app.command()
def validate(
    quiet: Annotated[
        bool,
        # typer declares `Option` as an overload set whose second arm mentions
        # `ParamType[Unknown]`, which strict mode reports as partially unknown.
        # The gap is in typer's annotations rather than in this file, so the
        # suppression is scoped to the call rather than to the module.
        typer.Option(  # pyright: ignore[reportUnknownMemberType]
            "--quiet", "-q", help="Print nothing on success."
        ),
    ] = False,
) -> None:
    """Run every check that reads only this repository.

    Exits non-zero if any check fails, printing each diagnostic with the
    artifact it names.
    """
    report = validate_all()
    if report.ok:
        if not quiet:
            typer.echo("catalog is valid")
        raise typer.Exit(code=0)
    typer.echo(report.render(), err=True)
    typer.echo(f"\n{len(report.diagnostics)} problem(s)", err=True)
    raise typer.Exit(code=1)


@app.command()
def root() -> None:
    """Print the repository root this build resolved to.

    Exists because every path in this package is derived from the location of
    the installed module rather than the working directory, and when that is
    wrong it is wrong in a way that is easier to print than to infer.
    """
    typer.echo(str(paths.ROOT))


def main() -> None:
    app()


if __name__ == "__main__":
    sys.exit(app())
