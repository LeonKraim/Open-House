"""The CI configuration, checked -- task 7.8.

CI runs on this repository alone. The four reference clones under `ressources/`
and the withheld-expression store under `.local/` are excluded by `.gitignore`:
two of the four repos grant no licence to redistribute, and a pipeline pinned to
four third-party repositories fails when *they* change rather than when *we* do.
The clones are an input to a one-time local generation step whose outputs are
committed, so the pipeline validates the committed corpus, schemas, examples and
invariants and needs no network.

So no CI job may read either directory. That claim is enforced here over the
*committed configuration*, not over the machine a run happens to land on. A job
that reads `ressources/` fails on any runner only because the directory is
absent, and that failure looks exactly like a job that reached for it on a
machine where it is present and then broke -- the two are the same red pipeline
and only one of them is the intended state of the world. Checking the
configuration turns the first into a diagnostic that names the job and says why
the directory is not there.

The check reads only this repository, so its boundary is the CI boundary and not
the local one; the two checks that read the clones are the `git ls-files` closure
and the prose gate, and neither is this one. It is exercised by its own tests;
whether it has joined `validate.py`'s `_CHECKS` is visible there rather than
asserted here, so this file does not claim a registration the run may not yet
perform.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import yaml

from . import paths
from .errors import CheckError, Report, read_text
from .narrow import as_mapping, as_sequence, as_text

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

CI_CHECK = "ci-configuration"

#: Where the workflow files live, relative to the repository root. Resolved at
#: call time rather than bound at import, because the fixtures redirect
#: `paths.ROOT` into a temporary tree and a path fixed at import would keep
#: reading whichever tree the module happened to be loaded from.
WORKFLOW_DIRECTORY = ".github/workflows"

#: Workflow files are YAML. `.github/workflows/` holds nothing else that matters
#: to this check, and an entry that is neither suffix is not a workflow.
_SUFFIXES = (".yml", ".yaml")

#: A path naming one of the two directories CI must never read.
#:
#: Anchored on both sides so that a longer token containing either -- a
#: `pressources/`, a `backup.local/`, a `ressources.yaml` -- is not the
#: directory. The lookbehind deliberately admits a leading `/` (a real path
#: separator) and excludes a leading word character, a dot and a hyphen, so
#: `/ressources/x` and `/.local/x` match while `x.local/` does not.
_FORBIDDEN = re.compile(r"(?<![\w.\-])(ressources|\.local)(?![\w.\-])")


def workflow_files() -> list[Path]:
    """The committed workflow files, by suffix, when the directory exists.

    An absent directory yields nothing rather than a failure. A tree with no
    workflows has no job to read anything, which is vacuously the property this
    check asserts; the fixtures in the suite build trees with no `.github/` at
    all, and a check that failed there would fail on every one of them for a
    reason none of them is about.
    """
    directory = paths.ROOT / WORKFLOW_DIRECTORY
    if not directory.is_dir():
        return []
    return [
        path
        for path in sorted(directory.iterdir())
        if path.is_file() and path.suffix in _SUFFIXES
    ]


def check_ci_configuration(report: Report) -> None:
    """No CI job reads `ressources/` or `.local/`.

    Reported per job, naming the job, because the two fixes it could call for
    are different: a job that genuinely needs the clones belongs in the local
    generation step whose outputs are committed, and a job that merely mentions
    a path needs a different path. A diagnostic against the file alone would
    leave the reader to work out which job, and which of the two, they are
    looking at.
    """
    for path in workflow_files():
        source = f"{WORKFLOW_DIRECTORY}/{path.name}"
        document = _load(source, path)
        jobs_value = document.get("jobs")
        jobs = as_mapping(jobs_value)
        if jobs_value is not None and not jobs:
            # Reported rather than skipped: a `jobs` block written in a shape
            # this cannot read is a job that could read anything, and skipping
            # it is the one result a guard must never produce.
            raise CheckError(
                CI_CHECK,
                source,
                "does not declare `jobs` as a mapping, so no job can be checked",
            )
        for job_id, job in jobs.items():
            found = _first_read(job)
            if found is not None:
                report.add(CI_CHECK, f"{source}:{job_id}", _message(job_id, found))
        # Fields outside `jobs` -- a workflow-level `env`, `defaults` or a path
        # filter under `on:` -- are read too, and belong to no one job, so the
        # diagnostic is against the file.
        outside = {key: value for key, value in document.items() if key != "jobs"}
        found = _first_read(outside)
        if found is not None:
            report.add(
                CI_CHECK,
                source,
                f"names `{found}` outside any job; the reference clones and the "
                "withheld-expression store are excluded from this repository and "
                "no CI step may reach for them",
            )


def _message(job_id: str, found: str) -> str:
    return (
        f"job `{job_id}` names `{found}`, a directory CI must not read; the "
        "reference clones and the withheld-expression store are excluded from "
        "this repository, and a job that reached for either would fail on a "
        "checkout where they are absent for a reason the configuration should "
        "have stated"
    )


def _first_read(node: object) -> str | None:
    """The first forbidden directory named anywhere under a workflow node."""
    for text in _scalars(node):
        match = _FORBIDDEN.search(text)
        if match is not None:
            return match.group(0)
    return None


def _scalars(value: object) -> Iterator[str]:
    """Every string scalar reachable from a workflow node, in document order.

    A workflow is nested maps and lists, and the fields that can name a
    directory -- `run`, `env`, `with`, a `paths:` filter -- sit at different
    depths in a job and in a step. Walking the value rather than naming the
    fields keeps a shape this did not anticipate in scope, which is the
    direction a guard should err in: it fails on the thing it was written for
    whether or not the form it arrived in was on a list.
    """
    text = as_text(value)
    if text is not None:
        yield text
        return
    for item in as_sequence(value):
        yield from _scalars(item)
    for _, item in as_mapping(value).items():
        yield from _scalars(item)


def _load(source: str, path: Path) -> dict[str, object]:
    """A workflow file as a mapping, or a `CheckError` naming it.

    Raising rather than skipping, for the reason the rest of the package gives:
    a workflow read as empty would scan no job, and a guard that scans nothing
    passes everything -- including the job that reads the clones.
    """
    try:
        text = read_text(path)
    except (OSError, UnicodeDecodeError) as exc:
        raise CheckError(CI_CHECK, source, f"cannot be read: {exc}") from exc
    try:
        loaded: object = yaml.safe_load(text)
    except (yaml.YAMLError, RecursionError) as exc:
        raise CheckError(CI_CHECK, source, f"cannot be parsed: {exc}") from exc
    if loaded is None:
        return {}
    document = as_mapping(loaded)
    if not document:
        raise CheckError(CI_CHECK, source, "is not a mapping of jobs")
    return document
