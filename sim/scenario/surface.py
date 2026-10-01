"""The surface a scenario drives, as `sim/` states it for itself.

The runner drives a session, and a session is the composition root's
(`openhouse/facade.py`). `sim/` may not import the composition root -- directly
or under a guarded import (`design.md` D12, `control-surface`'s boundary, and the
`composition-root-purity` check that enforces both) -- so the shape the runner
needs is declared here as a protocol rather than borrowed from there.

This is the same device `assertions.ReadEntity` uses to need no `engine/` import,
and the reason is the same: what a package requires of another is a fact about
the requiring package, and stating it here is what keeps the dependency pointing
one way. `openhouse.facade.OpenHouse` satisfies this protocol structurally, and
the two types meet wherever the composition root hands a session to this
package: `openhouse/scenarios.py` (`run_scenario` and `run_directory`) and
`openhouse/mcp_server.py`'s `run_scenario` tool, among others. A disagreement
between the protocol and the facade is visible at those call sites to a type
checker, which is why nothing here is annotated with the facade's own name.

The returns are protocols where the value is an engine type: naming
`DecisionRecord` would be the `engine/` import this package does not make, and
`DecisionRecordLike` below states the one member `sim/` actually reads.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol

from .assertions import ReadEntity

__all__ = ["DecisionRecordLike", "ScenarioSurface"]


class DecisionRecordLike(Protocol):
    """A decision record, as much of it as `sim/` reads: its document form.

    Only `to_document` is named because that is the whole of the coupling. Every
    record the runner keeps it converts, and the assertions read the converted
    document, so the engine's field set stays the engine's to change without a
    second copy of it being written down here.
    """

    def to_document(self) -> Mapping[str, object]: ...


class ScenarioSurface(Protocol):
    """What a scenario drives: the ten operations, the port's four, and a read.

    The ten registry operations are what a step verb compiles to; the four
    outside the registry are the house-control facility a scenario may also
    reach; and `read_entity` and `now` are what an assertion reads. One protocol
    rather than several because a scenario's steps and its assertions are handed
    the same object, and two protocols would let a surface satisfy one and not
    the other without anything saying so.

    `seed` and `started_at` are the run's fixed inputs, which a failure report
    repeats so a disagreement can be replayed. They are read from the session and
    not from the clock, because a restore moves the clock.
    """

    seed: int
    started_at: datetime

    def now(self) -> datetime: ...

    def read_entity(self, entity_id: str) -> ReadEntity: ...

    def advance_time(self, *, minutes: float) -> tuple[DecisionRecordLike, ...]: ...

    def set_state(self, entity_id: str, state: str) -> ReadEntity: ...

    def user_action(self, entity_id: str, state: str) -> ReadEntity: ...

    def inject_fault(self, entity_id: str, fault: str) -> ReadEntity: ...

    def snapshot(self) -> Mapping[str, object]: ...

    def restore(self, document: Mapping[str, object]) -> None: ...

    def get_decision_log(
        self, *, window: int | None = None
    ) -> tuple[DecisionRecordLike, ...]: ...

    def install_pack(self, manifest: str) -> Mapping[str, object]: ...

    def export_config(self) -> Mapping[str, object]: ...

    def import_config(self, document: Mapping[str, object]) -> None: ...

    def add_entity(
        self,
        entity_id: str,
        state: str,
        *,
        attributes: Mapping[str, object] | None = None,
    ) -> ReadEntity: ...

    def remove_entity(self, entity_id: str) -> None: ...

    def set_availability(self, entity_id: str, available: bool) -> None: ...

    def restart(self) -> None: ...
