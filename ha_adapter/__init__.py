"""The Home Assistant implementation of the `HouseAdapter` port -- Phase 4.

`HAAdapter` is the port over a real instance, built on `HaTransport`
(`transport.py`): `RestTransport` speaks to Home Assistant over its REST API and
`FakeHaTransport` (`testing.py`) is an in-memory instance the contract suite
drives without a network. `setup_flow` is the first-run setup logic the
`custom_components/open_house` integration renders, kept here as pure functions
so it can be tested with no Home Assistant present.

Nothing in this package imports `homeassistant`. That is not a stylistic choice:
`tools/catalog/invariants.py` imports every package as a subprocess with Home
Assistant absent, so a module-level Home Assistant import would fail the layout
check, and the port's whole reason to exist is that the engine ships with no Home
Assistant present. The integration under `custom_components/open_house/` is where
the Home Assistant types are, and it is a separate tree on purpose.
"""

from __future__ import annotations

from .adapter import HAAdapter, OriginNotAllowedError
from .setup_flow import (
    Area,
    BindingGuess,
    PersonChoice,
    ReviewLine,
    RoomSuggestion,
    SetupPlan,
    SetupStep,
    plan_setup,
)
from .testing import FakeHaTransport, build_adapter
from .transport import HaApiError, HaState, HaTransport, RestTransport

__all__ = [
    "Area",
    "BindingGuess",
    "FakeHaTransport",
    "HAAdapter",
    "HaApiError",
    "HaState",
    "HaTransport",
    "OriginNotAllowedError",
    "PersonChoice",
    "RestTransport",
    "ReviewLine",
    "RoomSuggestion",
    "SetupPlan",
    "SetupStep",
    "build_adapter",
    "plan_setup",
]
