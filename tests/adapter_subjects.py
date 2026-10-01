"""The implementations the `HouseAdapter` contract suite is pointed at.

The contract suite in `test_house_adapter_contract.py` is a pure function of the
port: it names no method or attribute that is not declared on `HouseAdapter`, so
it can be pointed at a second implementation without being edited -- and
therefore without being weakened exactly when it is first asked to hold that
second implementation to the contract. The one thing a suite of that shape
cannot contain is the *name* of an implementation, so the names live here, in a
registry the suite iterates, and nowhere else.

The registry is deliberately data rather than discovery. Discovery would make
"no implementation is registered" indistinguishable from "no implementation
exists yet", and the phase's worst outcome is a green run over an empty
parameter list. So the suite carries a guard that fails while this mapping is
empty or does not name the fake, and registering an implementation is an
explicit line here.

Two registrations are expected, and neither lives in this task:

- `simulation` (task 3.2) registers `FakeHouseAdapter`, the subject the suite is
  first run against. It is keyed by the class name so the guard's assertion --
  "`FakeHouseAdapter` is registered" -- is exactly the spec's words.
- Phase 4 registers the Home Assistant adapter under `ha_adapter/`, which is
  what closes the loop the port exists for: the same suite, unchanged, held
  against a second implementation.

Each value is a zero-argument factory rather than a class, because the fake is
constructed with its substrate -- the run's `VirtualClock` and seeded
`RandomStream` (see `simulation`) -- and the suite must not know that. The
factory is the seam through which an implementation supplies whatever it needs
while presenting the suite the bare port.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sim.adapter import FakeHouseAdapter
from sim.clock import VirtualClock
from sim.entropy import RandomStream

if TYPE_CHECKING:
    from collections.abc import Callable

    from engine.adapter import HouseAdapter

#: The registry key the fake house is registered under. The suite's guard asserts
#: this key is present, so an empty registry fails rather than collecting zero
#: subjects and passing.
FAKE_HOUSE_ADAPTER = "FakeHouseAdapter"

#: The fixed instant and seed a contract subject is built with. The suite drives
#: no behaviour, so the values are arbitrary -- but they are fixed rather than
#: drawn from the wall clock or an unseeded generator, so a subject is identical
#: from one test to the next and a failure is reproducible.
_STARTED_AT = datetime(2026, 1, 1, tzinfo=UTC)
_SEED = 0


def _build_fake() -> HouseAdapter:
    """A fresh fake with its substrate, presenting the bare port to the suite."""
    stream = RandomStream.from_seed(_SEED)
    clock = VirtualClock.started_at(_STARTED_AT)
    return FakeHouseAdapter(clock=clock, random_stream=stream)


#: Implementation name -> factory returning a freshly built, drivable adapter.
#:
#: `FakeHouseAdapter` is the subject the suite is first run against this phase;
#: Phase 4 registers the Home Assistant adapter under `ha_adapter/` here, which is
#: what closes the loop the port exists for -- the same suite, unchanged, held
#: against a second implementation.
IMPLEMENTATIONS: dict[str, Callable[[], HouseAdapter]] = {
    FAKE_HOUSE_ADAPTER: _build_fake,
}
