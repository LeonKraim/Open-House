"""The validator: the whole catalogue checked in one pass.

`oh-catalog validate` is the command every other part of the project leans on --
it is the pre-commit hook, it is the CI gate, and it is what the acceptance
script in task 8.1 drives. It is therefore one function, so that "the validator
passed" means the same thing everywhere it is said.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from . import invariants, licenses, paths, schemas
from .errors import CheckError, Report
from .narrow import as_mapping

if TYPE_CHECKING:
    from collections.abc import Callable

CATALOG_CHECK = "catalog-schema"


def check_catalog_data_files(report: Report) -> None:
    """Every data file under `catalog/` validates against its catalog schema.

    The pairing is by filename stem: `catalog/slots.yaml` is validated by
    `schemas/catalog/slots.json`. A data file with no schema fails naming the
    file, because the alternative -- skipping it -- would let a new file arrive
    unvalidated simply by not adding a schema for it.

    Markdown is exempt. `README.md` and `overlap.md` are documents; requiring a
    schema for prose would mean inventing one to satisfy the checker.
    """
    schema_dir = paths.SCHEMA_CATALOG
    for data_file in paths.catalog_data_files():
        relative = data_file.relative_to(paths.ROOT).as_posix()
        schema_path = schema_dir / f"{data_file.stem}.json"
        if not schema_path.is_file():
            report.add(
                CATALOG_CHECK,
                relative,
                f"no schema at schemas/catalog/{data_file.stem}.json; every data "
                "file under catalog/ must validate against one",
            )


def load_catalog_schema(stem: str) -> dict[str, object]:
    path = paths.SCHEMA_CATALOG / f"{stem}.json"
    loaded: object = json.loads(path.read_bytes().decode("utf-8"))
    document = as_mapping(loaded)
    if not document:
        msg = f"schemas/catalog/{stem}.json is not a JSON object"
        raise ValueError(msg)
    return document


#: Every check that reads only this repository, in the order they run. A list
#: rather than a sequence of calls so that `validate_all` can wrap each one in
#: the same `CheckError` handling without repeating it nine times, and so that
#: adding a check is one line rather than two.
_CHECKS: tuple[Callable[[Report], None], ...] = (
    invariants.check_version_control,
    invariants.check_layout,
    invariants.check_engine_purity,
    invariants.check_registry_boundary,
    invariants.check_declared_dependency_names,
    schemas.check_runtime_schemas,
    schemas.check_immutability,
    check_catalog_data_files,
    licenses.check_licenses,
)


def validate_all() -> Report:
    """Run every check that reads only this repository.

    The two clone-reading checks -- the `git ls-files` closure and the prose
    gate -- are deliberately absent: they run locally, their outputs are
    committed, and CI must pass without the clones present. Everything here is a
    pure function of the committed tree, which is what task 7.7 verifies by
    running the suite in a checkout with `ressources/` and `.local/` removed.

    A check that raises `CheckError` -- a schema that will not parse, a history
    that cannot be read -- is collected as a diagnostic rather than allowed to
    escape. Continuing past it would mean reporting findings derived from
    something already known to be broken, and letting it escape would mean the
    command that is the pre-commit hook and the CI gate could fail with a
    traceback instead of naming the file at fault.
    """
    report = Report()
    for check in _CHECKS:
        try:
            check(report)
        except CheckError as exc:
            report.add(exc.check, exc.where, exc.message)
    return report
