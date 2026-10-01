"""Every check is registered, or is named as one that deliberately is not.

A check module is an entry point and a body, and only the entry point makes the
second reachable. Nothing in `tools/catalog/` links the two: `validate.py` names
its checks by hand, and a module that is written and never added to `_CHECKS`
has no symptom at all. It imports, it lints, it type-checks, and it runs -- when
called directly, which is what its own tests do. `oh-catalog validate` and the
pre-commit hook simply never reach it.

That is not hypothetical. Task 4.1's check arrived in exactly this state, and a
reviewer reading only the check and its tests approved it, because both were
correct. What was missing was the one line in a third file, and the test that
would have caught it -- `assert _diagnostics() == []` on the committed tree --
is also true of a check that never runs. The defect was invisible in both
directions at once.

So the claim here is a closure over the package rather than a list of names: a
new `check_*(report)` in `tools/catalog/` is either registered or it fails this
test. The exemption list below is deliberately explicit and deliberately short,
because it is the only way a check can be absent from the run without saying so,
and an exemption that is inferred is an exemption nobody reviewed.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from typing import TYPE_CHECKING

from tools.catalog import validate

if TYPE_CHECKING:
    from collections.abc import Iterator

#: The checks that read the four reference clones under `ressources/`.
#:
#: The spec draws this boundary itself: everything else is a pure function of
#: this repository and runs in CI, and exactly two reads -- the `git ls-files`
#: closure and the prose gate -- need the clones, so they run locally and their
#: outputs are committed instead. CI runs from a checkout with `ressources/`
#: absent, which is what task 7.7 exercises.
#:
#: Written as `module.function` and checked for existence in the other
#: direction below, so this list cannot rot into naming a function that was
#: renamed or deleted while quietly exempting nothing.
LOCAL_ONLY: frozenset[str] = frozenset(
    {
        "repos.check_ha_versions",
        "prose_gate.check_prose_gate",
    }
)


def _entry_points() -> Iterator[tuple[str, object]]:
    """Every module-level `check_*(report)` in `tools.catalog`, dotted.

    Discovered rather than listed, which is the opposite of how this project
    treats most enumerations, and for a reason that does not carry over: the
    fixtures elsewhere name their files because a test that reads its
    expectations out of the directory it is checking agrees with any directory.
    Here the directory *is* the thing under test -- the claim is that the set of
    checks and the set of registrations are the same set -- so deriving one side
    from the code is the only construction in which the comparison means
    anything.
    """
    package = importlib.import_module("tools.catalog")
    for module_info in pkgutil.iter_modules(package.__path__):
        module = importlib.import_module(f"tools.catalog.{module_info.name}")
        for name, member in vars(module).items():
            if not name.startswith("check_") or not callable(member):
                continue
            if getattr(member, "__module__", None) != module.__name__:
                continue
            parameters = list(inspect.signature(member).parameters)
            if len(parameters) != 1:
                continue
            yield f"{module_info.name}.{name}", member


def test_every_check_entry_point_is_registered_or_named_local_only() -> None:
    """The closure, both ways. Discovery is what makes it a closure.

    A module missing from the run and a module named as exempt are two
    different states, and the message says which one the reader is looking at,
    because the fix differs: one adds a line to `_CHECKS`, the other adds a line
    to `LOCAL_ONLY` and to the spec's account of why CI can pass without it.
    """
    discovered = dict(_entry_points())
    registered = set(validate._CHECKS)

    unregistered = sorted(
        name
        for name, member in discovered.items()
        if member not in registered and name not in LOCAL_ONLY
    )
    assert unregistered == [], (
        f"{unregistered} define a check that nothing calls. A check is reachable "
        "only through `tools/catalog/validate.py`'s `_CHECKS`, so one that is "
        "absent from it never runs in `oh-catalog validate` or in the pre-commit "
        "hook, and its own tests -- which call it directly -- pass regardless. "
        "Register it, or name it in LOCAL_ONLY if it reads the clones."
    )


def test_the_exemption_list_names_checks_that_exist_and_are_not_registered() -> None:
    """Both directions on the list itself, since it is the only way past the test above.

    An exemption naming a function that no longer exists exempts nothing and
    reads as though it did. An exemption naming a function that *is* registered
    is worse: it claims a check is local-only while the run performs it, so the
    next reader would leave a clone-reading check in CI believing it was
    excluded.
    """
    discovered = dict(_entry_points())
    registered = set(validate._CHECKS)

    for name in sorted(LOCAL_ONLY):
        assert name in discovered, (
            f"LOCAL_ONLY names {name!r}, which is not a check entry point in "
            "tools/catalog; an exemption for something that no longer exists "
            "exempts nothing and hides that it exempts nothing"
        )
        assert discovered[name] not in registered, (
            f"LOCAL_ONLY names {name!r}, which `_CHECKS` also registers; a check "
            "that reads the clones must not run in CI, where the clones are absent"
        )
