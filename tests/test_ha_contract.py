"""Hold `HAAdapter` to the `HouseAdapter` contract, through the same suite.

`tests/test_house_adapter_contract.py` is a pure function of the port and takes
its subjects from `tests/adapter_subjects.py`'s `IMPLEMENTATIONS` registry. That
registry is where an implementation is *named* -- the suite itself names none, so
it cannot be edited, and therefore weakened, exactly when it is first asked to
hold a second implementation. Phase 4's job is to add a line to that registry.

**Why this file registers the adapter rather than editing the registry.**
`tests/adapter_subjects.py` is owned by another task this phase, and its own
docstring says Phase 4 registers the adapter "under `ha_adapter/` here". This file
is the registration: it adds `HAAdapter` to the shared `IMPLEMENTATIONS` mapping
at import time, *before* the shared suite module is imported, so the suite's
`_SUBJECT_NAMES = sorted(IMPLEMENTATIONS)` -- computed once, at import -- already
contains the adapter and every parametrized test in it runs twice. The ordering
is load-bearing, so it is asserted rather than assumed
(`test_the_shared_suite_collects_the_ha_adapter`): if the suite were ever imported
first, that guard fails and says so, rather than the adapter quietly not being
tested.

Pytest collects modules in sorted order, and `tests/test_ha_adapter.py` (which
does not import the suite) and this file both sort before
`tests/test_house_adapter_contract.py`, so the registration lands first. The
guard is what turns "so it should" into "and it did".
"""

from __future__ import annotations

from ha_adapter.testing import build_adapter
from tests.adapter_subjects import FAKE_HOUSE_ADAPTER, IMPLEMENTATIONS

#: The name `HAAdapter` is registered under. The shared suite keys subjects by
#: class name, so this is the adapter's class name and nothing else.
HA_ADAPTER = "HAAdapter"

#: Register before the shared suite is imported, so its module-level
#: `_SUBJECT_NAMES` picks the adapter up. Importing the suite here would freeze
#: the list without it, which is why the import is below and marked.
IMPLEMENTATIONS[HA_ADAPTER] = build_adapter

from tests import test_house_adapter_contract as contract_suite  # noqa: E402


def test_the_shared_suite_collects_the_ha_adapter() -> None:
    """The ordering this file relies on actually held.

    A guard, and the reason it is not vacuous: if the import order ever changed,
    the adapter would stop being run through the contract while every test stayed
    green, which is the failure mode the registry's own guard exists to prevent
    one level up.
    """
    assert HA_ADAPTER in contract_suite._SUBJECT_NAMES
    assert FAKE_HOUSE_ADAPTER in contract_suite._SUBJECT_NAMES


def test_the_registry_holds_exactly_both_implementations() -> None:
    """The fake and the Home Assistant adapter, and nothing else this phase."""
    assert set(IMPLEMENTATIONS) == {FAKE_HOUSE_ADAPTER, HA_ADAPTER}


def test_the_registered_factories_build_a_fresh_subject_each_time() -> None:
    """Each value is a zero-argument factory, so no two tests share a subject."""
    first = IMPLEMENTATIONS[HA_ADAPTER]()
    second = IMPLEMENTATIONS[HA_ADAPTER]()
    assert first is not second
