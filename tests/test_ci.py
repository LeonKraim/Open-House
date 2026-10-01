"""The CI configuration check -- task 7.8.

The boundary this check guards is drawn by the pipeline against itself, so the
tests come in the two shapes the rest of the suite uses. A *violating* workflow
is built on a fixture -- a job that reads `ressources/`, a `paths:` filter that
names `.local/`, a file that will not parse -- because the committed tree is by
construction the one that passes, and a guard proven only against it is a guard
that has never been seen to fail. A *clean* workflow, and the committed
workflow, are exercised on the real tree, because a fixture that merely
resembles the pipeline cannot say the pipeline is green.

The check is not registered in `validate._CHECKS` until the coordinator wires it
on approval, and the tests call it directly for that reason: run through
`validate.validate_all()` they would test the wiring rather than the check, and
the wiring is `tests/test_check_registry.py`'s subject. A direct call also keeps
the guard's own behaviour -- what it flags, and what it deliberately declines to
-- independent of whether it has been registered yet.

The last tests are the other half of task 7.8, and they are not about the guard:
they assert that the committed workflow actually runs the two commands the task
names, because a guard that keeps the jobs clone-free is worth nothing if the
jobs it guards do not run the checks in the first place.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest
import yaml

from tools.catalog import ci, paths
from tools.catalog.errors import CheckError, Report

from .conftest import write

if TYPE_CHECKING:
    from pathlib import Path

WORKFLOW = ".github/workflows/ci.yml"

#: An ordinary workflow, in the shape the committed one takes: a job, a checkout
#: and a validator step. The control the violating fixtures are read against.
GOOD = """\
name: CI
on: push
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: python -m tools.catalog.cli validate
"""


def _findings() -> list[tuple[str, str]]:
    """The guard's findings, as the pair a report carries."""
    report = Report()
    ci.check_ci_configuration(report)
    return [(d.where, d.message) for d in report.diagnostics]


def _messages() -> str:
    return "\n".join(f"{where}: {message}" for where, message in _findings())


# --------------------------------------------------------------------------
# A job that reads one of the two directories
# --------------------------------------------------------------------------


def test_a_job_that_reads_the_clones_is_named(fake_root: Path) -> None:
    write(
        fake_root,
        WORKFLOW,
        "name: CI\n"
        "on: push\n"
        "jobs:\n"
        "  build:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - run: cat ressources/ccostan/config/configuration.yaml\n",
    )

    findings = _findings()
    assert len(findings) == 1, _messages()
    where, message = findings[0]
    assert where == f"{WORKFLOW}:build"
    assert "`ressources`" in message


def test_a_job_that_reads_the_local_store_is_named(fake_root: Path) -> None:
    write(
        fake_root,
        WORKFLOW,
        "name: CI\n"
        "on: push\n"
        "jobs:\n"
        "  gate:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - run: tools/prose_gate.py < .local/raw-verbatim.json\n",
    )

    findings = _findings()
    assert len(findings) == 1, _messages()
    where, message = findings[0]
    assert where == f"{WORKFLOW}:gate"
    assert "`.local`" in message


def test_a_directory_named_under_a_step_env_is_read_too(fake_root: Path) -> None:
    """The read need not be the `run` line: an `env` that points a step at a
    clone reaches the clone just as surely, and a guard that read only `run`
    would pass it."""
    write(
        fake_root,
        WORKFLOW,
        "name: CI\n"
        "on: push\n"
        "jobs:\n"
        "  build:\n"
        "    runs-on: ubuntu-latest\n"
        "    env:\n"
        "      CATALOG_SOURCE: ressources/johnkoht\n"
        "    steps:\n"
        "      - run: echo hi\n",
    )

    assert [where for where, _ in _findings()] == [f"{WORKFLOW}:build"]


def test_a_path_filter_outside_every_job_is_named(fake_root: Path) -> None:
    """A trigger that only runs when `ressources/` changes would keep the
    directory in the pipeline's life even though no step reads it, and it sits
    outside `jobs`, so it is reported against the file rather than a job."""
    write(
        fake_root,
        WORKFLOW,
        "name: CI\n"
        "on:\n"
        "  push:\n"
        "    paths:\n"
        "      - 'ressources/**'\n"
        "jobs:\n"
        "  validate:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - run: echo hi\n",
    )

    findings = _findings()
    assert len(findings) == 1, _messages()
    where, message = findings[0]
    assert where == WORKFLOW
    assert "outside any job" in message


# --------------------------------------------------------------------------
# What the guard declines to flag
# --------------------------------------------------------------------------


def test_an_ordinary_workflow_passes(fake_root: Path) -> None:
    write(fake_root, WORKFLOW, GOOD)
    assert _findings() == [], _messages()


def test_a_checkout_with_no_workflow_is_not_a_failure(fake_root: Path) -> None:
    """The fixtures build trees with no `.github/` at all, and the guard runs
    inside the registered suite on every one of them; failing there would fail
    every test for a reason none of them is about."""
    assert not paths.RESSOURCES.exists()
    assert not paths.LOCAL.exists()
    assert ci.workflow_files() == []
    assert _findings() == [], _messages()


def test_a_name_that_only_resembles_the_path_is_not_read(fake_root: Path) -> None:
    """`company.local`, `myressources.txt` and `ressources.yaml` are not the
    directory, and a guard that matched a substring would refuse a workflow that
    never touches a clone -- the failure mode that makes a guard get disabled."""
    write(
        fake_root,
        WORKFLOW,
        "name: CI\n"
        "on: push\n"
        "jobs:\n"
        "  validate:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - run: echo company.local/thing myressources.txt\n"
        "      - run: cat ressources.yaml\n",
    )

    assert _findings() == [], _messages()


def test_a_file_in_the_directory_that_is_not_a_workflow_is_ignored(
    fake_root: Path,
) -> None:
    """`.github/workflows/` holds no workflow but still ships; a README that
    happens to mention a path is not a job and must not be read as one."""
    write(fake_root, ".github/workflows/README.md", "run `ressources/` locally\n")
    assert _findings() == [], _messages()


def test_a_workflow_with_a_yaml_suffix_is_read_too(fake_root: Path) -> None:
    write(
        fake_root,
        ".github/workflows/deploy.yaml",
        "name: deploy\non: push\njobs:\n  ship:\n    steps:\n      - run: ls .local\n",
    )

    assert [where for where, _ in _findings()] == [".github/workflows/deploy.yaml:ship"]


# --------------------------------------------------------------------------
# A workflow that cannot be read
# --------------------------------------------------------------------------


def test_a_workflow_that_will_not_parse_is_named(fake_root: Path) -> None:
    write(fake_root, WORKFLOW, "jobs: [\n")

    with pytest.raises(CheckError) as raised:
        _findings()
    assert raised.value.check == ci.CI_CHECK
    assert raised.value.where == WORKFLOW


def test_a_jobs_block_that_is_not_a_mapping_is_named(fake_root: Path) -> None:
    """A `jobs` block written in a shape the guard cannot read is a job that
    could read anything; skipping it is the one result a guard must never
    produce, so it is a failure rather than a silence."""
    write(fake_root, WORKFLOW, "name: CI\non: push\njobs:\n  - build\n")

    with pytest.raises(CheckError) as raised:
        _findings()
    assert raised.value.where == WORKFLOW


# --------------------------------------------------------------------------
# The committed tree
# --------------------------------------------------------------------------


def test_the_committed_workflows_read_no_clone(real_root: Path) -> None:
    assert ci.workflow_files(), "there is no workflow to check"
    assert _findings() == [], _messages()


def _run_commands() -> list[str]:
    """Every `run` command in every job of every committed workflow."""
    commands: list[str] = []
    for path in ci.workflow_files():
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        jobs = document["jobs"]
        for job in jobs.values():
            for step in job.get("steps", []):
                if "run" in step:
                    commands.append(str(step["run"]))
    return commands


def test_the_committed_workflow_runs_the_validator_and_the_hooks(
    real_root: Path,
) -> None:
    """The guard is worth nothing if the jobs it keeps clone-free do not run
    the checks. The task's two commands, pinned so a step cannot be dropped from
    the pipeline without a test failing rather than a green build going quiet."""
    commands = _run_commands()
    assert any("tools.catalog.cli validate" in c for c in commands), commands
    assert any(re.search(r"pre-commit run --all-files", c) for c in commands), commands


def test_the_committed_workflow_runs_the_suite(real_root: Path) -> None:
    """Task 12.0, and with it `control-surface`'s exit criterion.

    The criterion is stated as something that *runs*: "an AI agent can build a
    house, run scenarios, read the log, and iterate with no HA installed". The
    requirement asks that it be checked in CI and not demonstrated by hand,
    "because an unasserted property of an environment is a hope". The loop is a
    pytest module, so a pipeline whose only jobs are the validator and the hooks
    proves it in a developer's checkout and nowhere else -- which is exactly what
    the acceptance mapping recorded as a gap against this requirement before this
    job existed.

    Pinned by name and not by count, for the same reason the test above pins its
    two commands: a job can be renamed, reordered or rewritten and this stays
    true, while a step *deleted* fails here rather than in a green build that
    quietly stopped running the suite. The pattern is the invocation and not the
    word, so a step that runs the pinned interpreter's pytest satisfies it and a
    bare `pytest` no longer does. It is not a proof that the step runs anything:
    the pattern is unanchored, so a step whose command merely *names* the
    invocation -- an `echo`, or a collection with `--collect-only` -- matches it
    too, and this test does not tell those apart from a real run.
    """
    commands = _run_commands()
    assert any(re.search(r"python -m pytest\b", c) for c in commands), commands
