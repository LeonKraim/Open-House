"""The library facade: one session over a house, no CLI, no MCP, no HA.

`control-surface`'s "The library facade opens a session and drives a house with
no CLI, no MCP and no Home Assistant" -- task 10.0 -- with the surface halves of
`install_pack` (10.4), `export_config`/`import_config` (10.5), the safety veto as
the surface meets it (11.1), and the two runtime round trips `snapshot`/`restore`
(3.4) and `get_decision_log` (4.1).

Every check here drives the facade directly and imports no CLI module and no MCP
module, so a surface that had grown a decision of its own would go uncovered --
that is what makes these the library's checks rather than a fourth face's. Each
test names the implementation that would falsify it: a facade that wrote a lock
from a `world` origin, dropped a refusal rather than recording it, kept a stale
copy of the log's bound, or let a restore resume carrying the log it had is the
implementation each one is written against.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest
from jsonschema.protocols import Validator
from jsonschema.validators import Draft202012Validator

from engine.adapter import ChangeOrigin
from engine.binding import InvalidHouseError
from engine.decision_log import DecisionRecord, Outcome
from engine.vocabulary import Vocabulary
from openhouse import packs
from openhouse.facade import open_session
from sim.fixtures import FIXTURE_NAMES
from sim.snapshot import UnsupportedSnapshotVersionError

if TYPE_CHECKING:
    from collections.abc import Callable

    from openhouse.facade import OpenHouse

ROOT = Path(__file__).resolve().parents[1]

#: The egress writes the veto is measured on: every spelling of "the lock is
#: open" and "the cover is open" that `engine/safety.py` closes over. Listing all
#: of them rather than one is the point -- a veto matching only the literal
#: strings `unlock` and `open` would pass a check written against one pair and
#: let `on` through to a lock.
EGRESS_WRITES: tuple[tuple[str, str], ...] = (
    ("lock.front_door", "unlocked"),
    ("lock.front_door", "unlock"),
    ("lock.front_door", "on"),
    ("cover.garage_door", "open"),
    ("cover.garage_door", "opening"),
)


@pytest.fixture(scope="module")
def vocabulary() -> Vocabulary:
    """The committed catalog, frozen for the module."""
    return Vocabulary.load(ROOT)


# -- The houses these checks drive -------------------------------------------


def _inline_house() -> dict[str, object]:
    """The smallest house the frozen schema admits that still binds a slot.

    A function and not a module constant so no two sessions share one document:
    the collections below are lists, and a shared one is a document a caller
    could edit for every later test.
    """
    return {
        "name": "an inline house",
        "rooms": [
            {
                "id": "foyer",
                "name": "Foyer",
                "type": "foyer",
                "bindings": {
                    "light_group": {"entity_id": "light.foyer"},
                    "motion_sensor": {"entity_id": "binary_sensor.foyer_motion"},
                },
            }
        ],
        "house_scope": {"slots": ["light_group"]},
    }


def _house_with_an_egress() -> dict[str, object]:
    """A house binding a lock and a cover, so the veto has something to refuse.

    Inline rather than a fixture because none of the four built houses binds a
    `lock` or a `cover`: a veto check run against one of them would pass by
    having no egress device to refuse.
    """
    return {
        "name": "a house with an egress",
        "rooms": [
            {
                "id": "foyer",
                "name": "Foyer",
                "type": "foyer",
                "bindings": {
                    "lock": {"entity_id": "lock.front_door"},
                    "light_group": {"entity_id": "light.foyer"},
                    "motion_sensor": {"entity_id": "binary_sensor.foyer_motion"},
                    "door_contact": {"entity_id": "binary_sensor.front_door"},
                },
            },
            {
                "id": "garage",
                "name": "Garage",
                "type": "garage",
                "bindings": {
                    "cover": {"entity_id": "cover.garage_door"},
                    "light_group": {"entity_id": "light.garage"},
                    "motion_sensor": {"entity_id": "binary_sensor.garage_motion"},
                },
            },
        ],
        "house_scope": {"slots": ["light_group"]},
    }


def _session(name: str, vocabulary: Vocabulary) -> OpenHouse:
    """A session over the named fixture, or over the inline house for `inline`."""
    house: str | Mapping[str, object] = _inline_house() if name == "inline" else name
    return open_session(house=house, vocabulary=vocabulary)


def _world_write(session: OpenHouse) -> object:
    """`set_state` on a light: the world changing, and no egress action."""
    return session.set_state("light.foyer", "on")


def _user_write(session: OpenHouse) -> object:
    """`user_action` on a light: the one path to a `user` origin."""
    return session.user_action("light.foyer", "on")


def _fault_write(session: OpenHouse) -> object:
    """`inject_fault` on a light: a fault origin, told apart from both."""
    return session.inject_fault("light.foyer", "unavailable")


def _keys(value: object) -> set[str]:
    """Every key anywhere in a JSON document, however deeply nested.

    A JSON document is only mappings and lists, so walking those two is the whole
    of it. Used where the claim is about a name rather than a place: a
    substring search over the serialisation would be true of a document that
    merely mentioned the word, and blind to the same field smuggled under a
    different container.
    """
    if isinstance(value, Mapping):
        mapping = cast("Mapping[object, object]", value)
        return {str(key) for key in mapping} | {
            nested for item in mapping.values() for nested in _keys(item)
        }
    if isinstance(value, list):
        items = cast("list[object]", value)
        return {nested for item in items for nested in _keys(item)}
    return set()


# -- Opening a session -------------------------------------------------------


@pytest.mark.parametrize("name", FIXTURE_NAMES, ids=str)
def test_a_session_opens_over_a_fixture_and_names_it_as_its_source(
    name: str, vocabulary: Vocabulary
) -> None:
    """`source` is the fixture's own name, so a run says which house it drove.

    Falsified by a facade that reported one source for every house, which would
    leave a report of a run unable to say which fixture it was a run of.
    """
    session = open_session(house=str(name), vocabulary=vocabulary)
    assert session.source == str(name)
    assert session.house.name == f"the {name} fixture"


def test_a_session_opens_over_an_inline_house_and_names_it_inline(
    vocabulary: Vocabulary,
) -> None:
    """A house document is opened as `inline`, told apart from a fixture.

    Falsified by `source` being the document's `name` field: a caller could not
    then tell a house it passed in from one the build selected for it.
    """
    session = open_session(house=_inline_house(), vocabulary=vocabulary)
    assert session.source == "inline"
    assert session.house.name == "an inline house"


def test_a_session_opens_over_a_configuration_export_config_produced(
    vocabulary: Vocabulary,
) -> None:
    """A document the surface itself wrote is a document a session accepts.

    Falsified by an `export_config` whose output no `open_session` would take --
    an export that could not be read back is an export that round-trips only
    through `import_config`, which is the one operation it is tested against.
    """
    first = open_session(house=_inline_house(), vocabulary=vocabulary)
    document = first.export_config()
    second = open_session(house=document, vocabulary=vocabulary)
    assert second.source == "inline"
    assert second.export_config() == document


def test_the_facade_drives_a_house_without_the_surfaces_or_home_assistant() -> None:
    """The library half of the exit criterion, in a process that has neither.

    Driven in a fresh interpreter because `sys.modules` is process-wide: this
    session's other modules import the CLI and the MCP server, so an in-process
    check would report their imports rather than the facade's. Falsified by any
    import of `openhouse.cli`, `openhouse.mcp_server` or `homeassistant` reached
    from the facade's own import graph.
    """
    script = (
        "import sys\n"
        "from pathlib import Path\n"
        "from engine.vocabulary import Vocabulary\n"
        "from openhouse.facade import open_session\n"
        "session = open_session(house='minimal', vocabulary=Vocabulary.load(Path('.')))\n"
        "records = session.advance_time(minutes=5)\n"
        "session.snapshot()\n"
        "surfaces = [name for name in sys.modules "
        "if name in ('openhouse.cli', 'openhouse.mcp_server', 'homeassistant')]\n"
        "print(bool(records), not surfaces)\n"
    )
    finished = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert finished.stdout.strip() == "True True", finished.stderr


# -- The clock ---------------------------------------------------------------


def test_advance_time_moves_the_sessions_instant(vocabulary: Vocabulary) -> None:
    """`now` is the virtual clock, moved by `advance_time` and nothing else.

    Falsified by a facade that stamped its records with anything but the instant
    the tick read, or read a wall clock: two runs of one scenario would then
    differ for a reason the scenario does not contain.
    """
    session = open_session(house="minimal", vocabulary=vocabulary)
    assert session.now() == session.started_at
    records = session.advance_time(minutes=30)
    assert session.now() == session.started_at + timedelta(minutes=30)
    assert records
    assert {record.at for record in records} == {session.now()}


def test_advancing_by_zero_ticks_nothing(vocabulary: Vocabulary) -> None:
    """A zero advance returns nothing, writes no record and does not move time.

    Falsified by an `advance_time` that ticked at all for a zero delta: it would
    append a record for an instant that did not pass, and "advancing by zero
    changes nothing" would be false at the surface.
    """
    session = open_session(house="minimal", vocabulary=vocabulary)
    before = session.now()
    assert session.advance_time(minutes=0) == ()
    assert session.now() == before
    assert session.get_decision_log() == ()


def test_advancing_returns_the_records_it_appended(vocabulary: Vocabulary) -> None:
    """The tuple an advance returns is what then reads back off the log.

    Falsified by a facade that returned a reshaped or reordered copy: what a
    caller of the operation sees and what a reader of the log sees would no
    longer be the same records.
    """
    session = open_session(house="minimal", vocabulary=vocabulary)
    records = session.advance_time(minutes=5)
    assert records
    assert all(isinstance(record, DecisionRecord) for record in records)
    assert session.get_decision_log() == records


# -- The change origins ------------------------------------------------------


@pytest.mark.parametrize(
    ("write", "origin"),
    [
        (_world_write, ChangeOrigin.WORLD),
        (_user_write, ChangeOrigin.USER),
        (_fault_write, ChangeOrigin.FAULT),
    ],
    ids=["set_state", "user_action", "inject_fault"],
)
def test_each_write_operation_carries_its_own_origin(
    write: Callable[[OpenHouse], object],
    origin: ChangeOrigin,
    vocabulary: Vocabulary,
) -> None:
    """Each write reaches the port under the origin the engine reads as its own.

    Falsified by any two writes sharing an origin: manual-override detection
    reads the last writer, so an operation that wrote the wrong one would either
    trip override detection on a device no human touched or fail to trip it on
    one that was.
    """
    session = open_session(house="minimal", vocabulary=vocabulary)
    write(session)
    assert session.read_entity("light.foyer").last_origin is origin


def test_only_user_action_leaves_a_user_origin(vocabulary: Vocabulary) -> None:
    """`set_state` and `inject_fault` never write the `user` origin.

    Falsified by either reaching the port with `ChangeContext.user()`, which
    would make "the user did this" mean something a scenario can no longer rely
    on and suppress the very behaviour it was asserting.
    """
    session = open_session(house="minimal", vocabulary=vocabulary)
    session.set_state("light.foyer", "on")
    assert session.read_entity("light.foyer").last_origin is not ChangeOrigin.USER
    session.inject_fault("light.foyer", "off")
    assert session.read_entity("light.foyer").last_origin is not ChangeOrigin.USER


# -- The safety veto at the surface ------------------------------------------


@pytest.mark.parametrize(("entity_id", "state"), EGRESS_WRITES)
def test_set_state_cannot_open_an_egress_through_the_surface(
    entity_id: str, state: str, vocabulary: Vocabulary
) -> None:
    """A `world`-origin egress write is refused, recorded, and not applied.

    Falsified by any of the four claims failing: a write that reached the house,
    a return that reported a state the house does not hold, a record that was not
    appended, or one whose outcome, rule or actor was anything but the surface's
    own refusal.
    """
    session = open_session(house=_house_with_an_egress(), vocabulary=vocabulary)
    before = session.read_entity(entity_id)
    returned = session.set_state(entity_id, state)
    assert returned.state == before.state
    assert returned.last_origin is before.last_origin
    assert session.read_entity(entity_id) == before
    records = session.get_decision_log()
    assert len(records) == 1
    record = records[0]
    assert record.outcome is Outcome.REFUSED_UNSAFE
    assert record.rule is None
    assert record.actor == "set_state"
    assert record.state_delta == ()
    assert record.commands[0].slot == ""
    assert record.commands[0].entities == (entity_id,)
    assert record.commands[0].action == state


@pytest.mark.parametrize(("entity_id", "state"), EGRESS_WRITES)
def test_user_action_is_admitted_to_the_same_egress(
    entity_id: str, state: str, vocabulary: Vocabulary
) -> None:
    """A direct user unlock or open is applied, and records nothing.

    Falsified by a veto that refused the user too: a person opening their own
    door is not the system auto-unlocking it, and a house that would not obey
    one is a house that refuses its owner.
    """
    session = open_session(house=_house_with_an_egress(), vocabulary=vocabulary)
    returned = session.user_action(entity_id, state)
    assert returned.state == state
    assert returned.last_origin is ChangeOrigin.USER
    assert session.get_decision_log() == ()


def test_set_state_writes_an_action_that_is_not_an_egress(
    vocabulary: Vocabulary,
) -> None:
    """A `world` write that opens nothing is applied and records nothing.

    The control the veto checks above need: falsified by a facade that refused
    every write rather than only an egress one, which would pass every refusal
    check in this module while breaking the surface entirely.
    """
    session = open_session(house=_house_with_an_egress(), vocabulary=vocabulary)
    returned = session.set_state("light.foyer", "on")
    assert returned.state == "on"
    assert returned.last_origin is ChangeOrigin.WORLD
    assert session.get_decision_log() == ()


# -- Snapshot and restore ----------------------------------------------------


def test_a_snapshot_round_trips_the_state_it_captured(
    vocabulary: Vocabulary,
) -> None:
    """A restore returns the house to the state the snapshot was taken at.

    Falsified by a restore that re-derived the state instead of returning it --
    a device the snapshot recorded as on coming back off, which is a resumed run
    deciding differently from the one it resumed.
    """
    session = open_session(house="minimal", vocabulary=vocabulary)
    session.set_state("light.foyer", "on")
    taken = session.snapshot()
    session.set_state("light.foyer", "off")
    session.advance_time(minutes=5)
    assert session.read_entity("light.foyer").state == "off"
    session.restore(taken)
    assert session.read_entity("light.foyer").state == "on"
    assert session.read_entity("light.foyer").last_origin is ChangeOrigin.WORLD


def test_the_snapshot_document_carries_its_version(vocabulary: Vocabulary) -> None:
    """The document names the format version it was written in.

    Falsified by a snapshot without a `snapshot_version`: restore would have
    nothing to refuse an older or newer document on, and a versioned format that
    never records its version is one only in appearance.

    The literal is a supersession of what Phase 1 asserted: `1.3.0` is `1.2.0`
    with the installed set added to the engine-owned half, and the bump is the
    deliberate change `sim/snapshot.py` documents -- a `1.2.0` document records
    no packs, so resuming from one would resume a house whose packs nothing can
    account for.
    """
    session = open_session(house="minimal", vocabulary=vocabulary)
    document = session.snapshot()
    assert document["snapshot_version"] == "1.3.0"
    assert set(document) == {
        "snapshot_version",
        "entities",
        "engine_state",
        "clock",
        "random_seed",
        "random_position",
    }


def test_a_restore_refuses_a_version_the_build_does_not_understand(
    vocabulary: Vocabulary,
) -> None:
    """An unknown `snapshot_version` is refused rather than partly applied.

    Falsified by a restore that ignored the field: it would resume from a subset
    of a format the build has never seen rather than saying so.
    """
    session = open_session(house="minimal", vocabulary=vocabulary)
    document = dict(session.snapshot())
    document["snapshot_version"] = "0.0.1"
    with pytest.raises(UnsupportedSnapshotVersionError) as refusal:
        session.restore(document)
    assert refusal.value.version == "0.0.1"


def test_a_restore_does_not_carry_the_decision_log(vocabulary: Vocabulary) -> None:
    """The log is history, not state: neither operation carries a record.

    Falsified by a snapshot that embedded records, or a restore that kept the
    restoring session's log: either would make a resumed run's log depend on the
    run that preceded it, which is the bound the log exists to cap.
    """
    session = open_session(house="minimal", vocabulary=vocabulary)
    session.advance_time(minutes=5)
    assert session.get_decision_log()
    document = session.snapshot()
    assert "log" not in document
    # A key walk, not a substring search: `outcome` is the field a record
    # carries, so this catches a record wherever it sits -- and it is a claim
    # about a name, so a document that merely mentioned the word would not
    # satisfy it. `outcome` is the one name worth pinning: the snapshot does
    # carry `override_records`, so a blanket ban on record-shaped names would be
    # wrong.
    assert "outcome" not in _keys(document)
    session.restore(document)
    assert session.get_decision_log() == ()


# -- export_config and import_config -----------------------------------------


@pytest.mark.parametrize("name", [*FIXTURE_NAMES, "inline"], ids=str)
def test_a_house_round_trips_to_an_identical_configuration(
    name: str, vocabulary: Vocabulary
) -> None:
    """Exporting, importing and exporting again is the identity.

    Falsified by an export that dropped a binding or a room type, or an import
    that reconstituted a different house: the second export is compared to the
    first, so both halves have to be faithful for it to hold.
    """
    session = _session(name, vocabulary)
    document = session.export_config()
    session.import_config(document)
    assert session.export_config() == document


def test_the_exported_configuration_validates_against_the_house_schema(
    vocabulary: Vocabulary,
) -> None:
    """`export_config`'s output is a house document, not a private shape.

    Falsified by an export carrying a field the frozen schema does not admit --
    which is exactly the Phase 3 document this operation must not grow into.
    """
    document = _session("minimal", vocabulary).export_config()
    # `iter_errors` rather than `validate`, because the protocol's `validate` is
    # declared `(*args, **kwargs) -> None` and a call through it tells a reader
    # nothing about what was checked; this way the failures are named.
    validator = cast("Validator", Draft202012Validator(vocabulary.house_schema))
    errors = [error.message for error in validator.iter_errors(cast("Any", document))]
    assert errors == [], errors


def test_the_exported_configuration_binds_no_phase_three_field(
    vocabulary: Vocabulary,
) -> None:
    """The export is the house and its bindings, and nothing about a registry.

    Falsified by a binding carrying a `registry_id`, or the document carrying an
    export version or a profile: Phase 1 has no device registry to re-link
    against, so such a field would either be invented or silently dropped.
    """
    document: Any = _session("minimal", vocabulary).export_config()
    assert set(document) == {"name", "rooms", "house_scope"}
    for room in document["rooms"]:
        assert set(room) == {"id", "name", "type", "bindings"}
        for binding in room["bindings"].values():
            assert set(binding) == {"entity_id"}


def test_import_config_refuses_a_document_outside_the_house_schema(
    vocabulary: Vocabulary,
) -> None:
    """A Phase 3-shaped document is refused, and the field is named.

    Falsified by an `import_config` that read the fields it knew and ignored the
    rest: the operation would accept the Phase 3 export by accident, which is
    the failure the scope note exists to prevent. The refusal is the binding
    layer's own -- the extra key is a schema violation and not a slot the house
    cannot supply -- and it names the pointer it failed at.
    """
    session = _session("minimal", vocabulary)
    document = dict(session.export_config())
    document["export_version"] = "1.0.0"
    with pytest.raises(InvalidHouseError) as refusal:
        session.import_config(document)
    assert "export_version" in str(refusal.value)
    assert refusal.value.pointer == "/"


# -- get_decision_log --------------------------------------------------------


def _ticked(name: str, vocabulary: Vocabulary, *, ticks: int = 3) -> OpenHouse:
    """A session that has ticked `ticks` times, so its log holds several records."""
    session = _session(name, vocabulary)
    for _ in range(ticks):
        session.advance_time(minutes=1)
    return session


def test_the_window_returns_at_most_the_size_it_was_asked_for(
    vocabulary: Vocabulary,
) -> None:
    """A window smaller than the log returns exactly the size asked for.

    Falsified by a surface that returned the whole log whatever the window: a
    long session's read would grow without limit, which is the surface half of
    the log's bound.
    """
    session = _ticked("minimal", vocabulary)
    whole = session.get_decision_log()
    assert len(whole) > 2
    assert len(session.get_decision_log(window=2)) == 2


def test_the_window_is_the_most_recent_records_oldest_first(
    vocabulary: Vocabulary,
) -> None:
    """A window is a slice off the end of the log, in the log's own order.

    Falsified by a window that returned the *oldest* records, or reversed them:
    "the most recent records, oldest first within the window" is a claim about
    both ends, and only comparing against the tail of the log checks both.
    """
    session = _ticked("minimal", vocabulary)
    whole = session.get_decision_log()
    assert session.get_decision_log(window=3) == whole[-3:]


def test_the_window_defaults_to_the_logs_own_bound(vocabulary: Vocabulary) -> None:
    """No window means the log's own bound, not an unbounded read.

    Falsified by a facade defaulting to some number of its own -- or to nothing
    at all: the default has to be the log's, because the bound is the log's.
    """
    session = _ticked("minimal", vocabulary)
    bound = session.engine.log.bound
    assert session.get_decision_log() == session.get_decision_log(window=bound)


# -- install_pack ------------------------------------------------------------

#: A manifest that validates, binds the two slots the `minimal` fixture supplies
#: and declares one behaviour. Written here rather than taken from `packs/`
#: because the checks that are about *which* manifests install have to vary one
#: field at a time, and the shipped pack is one fixed document.
#:
#: Written to the current schema -- `pack-manifest/1.2.0.json` -- which is what
#: `install_pack` validates against: `kind` is one of the five kinds, and
#: `engine_api`, `license` and `i18n` are the three clauses that version requires
#: of every pack. The version is read through `tools.catalog.schemas`, so a
#: future version would change what this fixture has to carry without a second
#: place to remember.
#:
#: `__PROVIDED__` is where `_manifest` puts the path of the file this manifest
#: pins, which it writes beside the manifest. It is a placeholder rather than a
#: literal because the sandbox reads a `provides` path against the repository
#: root and requires the answer to stay inside the pack's own directory: a
#: fixture written under a temporary directory has to say where it is, and a
#: fixed repo-relative path would name a file the checkout does not contain.
#: Phase 1's fixture named one -- `packs/probe/light.yaml` -- because Phase 1's
#: `install_pack` had no sandbox to notice that the file was a fiction.
VALID_MANIFEST = """\
name: probe_pack
version: "1.0.0"
description: A pack a test installs.
kind: module
engine_api: ">=1.0.0 <2.0.0"
license: mit
requires_slots: [light_group, motion_sensor]
provides:
  - path: __PROVIDED__
    class: automation
behaviours:
  - name: probe_behaviour
    trigger: state
    condition: state
    action: service
i18n:
  default:
    pack: Probe pack
    description: A pack a test installs.
    probe_behaviour: Probe behaviour
"""

#: The file `VALID_MANIFEST` pins. Shaped the way the sandbox reads an
#: automation -- it has a `trigger` and an `action` -- because a `provides` entry
#: declaring class `automation` has to pin a file of that class, and the shape is
#: read from the file rather than declared beside it.
PROVIDED_FILE = """\
alias: Probe automation
trigger:
  - platform: state
    entity_id: motion_sensor
    to: "on"
action:
  - service: light.turn_on
    target:
      entity_id: light_group
"""


def _manifest(tmp_path: Path, text: str) -> str:
    """Write a manifest and the file it pins, and return the manifest's path."""
    (tmp_path / "light.yaml").write_text(PROVIDED_FILE, encoding="utf-8")
    path = tmp_path / "pack.yaml"
    path.write_text(
        text.replace("__PROVIDED__", (tmp_path / "light.yaml").as_posix()),
        encoding="utf-8",
    )
    return str(path)


def test_a_manifest_that_validates_installs_and_enables_nothing(
    tmp_path: Path, vocabulary: Vocabulary
) -> None:
    """A valid manifest installs, reporting the pack and an empty enable set.

    Falsified by a result that omitted `enabled` -- leaving "installed" to be
    read as "running" -- or that reported a behaviour as enabled by the install.

    The result is read by key and not compared whole, which is a deliberate
    supersession of what this check asserted in Phase 1. `pack-install` requires
    the operation to answer what arrived and why, so the result carries the
    version, the digest, the bound slots, the satisfied dependencies and the
    flags as well as the three keys below. The three still have to mean what they
    meant -- the pack, that it is in, and that nothing it declared is on -- and
    the whole shape is asserted in `tests/test_pack_install.py`, which is where
    the record's own contract belongs.
    """
    session = open_session(house="minimal", vocabulary=vocabulary)
    result = session.install_pack(_manifest(tmp_path, VALID_MANIFEST))
    assert result["pack"] == "probe_pack"
    assert result["installed"] is True
    assert result["enabled"] == ()


def test_a_manifest_that_fails_its_schema_is_refused(
    tmp_path: Path, vocabulary: Vocabulary
) -> None:
    """A manifest outside the `pack-manifest` schema raises, naming the pack.

    Falsified by an `install_pack` that checked only the slots: a manifest that
    is not a manifest at all would install, and the schema would be decoration.
    """
    session = open_session(house="minimal", vocabulary=vocabulary)
    broken = VALID_MANIFEST.replace("name: probe_pack", "name: Probe Pack")
    with pytest.raises(packs.PackError) as refusal:
        session.install_pack(_manifest(tmp_path, broken))
    assert refusal.value.pack == "Probe Pack"
    assert "schema" in refusal.value.reason


def test_a_manifest_requiring_a_slot_the_house_binds_nowhere_installs_unwired(
    tmp_path: Path, vocabulary: Vocabulary
) -> None:
    """A required slot this house binds nowhere is recorded, not refused.

    The record carries the slot against an empty tuple, which is the fact a later
    reader -- and the live path's enable gate -- reads as "installed, not yet
    wired". Falsified by an `install_pack` that refused the pack: the module
    would then be one a person cannot put in a room before wiring what it needs.
    """
    session = open_session(house="minimal", vocabulary=vocabulary)
    demanding = VALID_MANIFEST.replace(
        "requires_slots: [light_group, motion_sensor]",
        "requires_slots: [vacuum]",
    )
    result = session.install_pack(_manifest(tmp_path, demanding))

    assert result["installed"] is True
    slots = result["slots"]
    assert isinstance(slots, Mapping)
    assert slots["vacuum"] == ()


def test_a_manifest_requiring_a_slot_no_vocabulary_declares_is_refused(
    tmp_path: Path, vocabulary: Vocabulary
) -> None:
    """A slot name no slot file defines fails, and says no house can supply it.

    Falsified by a check that only asked the house: a pack naming a slot the
    corpus has never heard of would be reported as a house's shortcoming rather
    than as a pack that cannot be installed anywhere.
    """
    session = open_session(house="minimal", vocabulary=vocabulary)
    invented = VALID_MANIFEST.replace(
        "requires_slots: [light_group, motion_sensor]",
        "requires_slots: [nonsense_slot]",
    )
    with pytest.raises(packs.PackError) as refusal:
        session.install_pack(_manifest(tmp_path, invented))
    assert "nonsense_slot" in refusal.value.reason
    assert "vocabulary" in refusal.value.reason


def test_the_shipped_example_pack_installs(vocabulary: Vocabulary) -> None:
    """The pack the project ships installs into a house that supplies its slots.

    Falsified by an `install_pack` the shipped pack fails: the one hand-written
    example would then be a document that never worked, and the check would be
    against fixtures the surface wrote for itself.

    Read by key for the reason the check above is: the result grew in Phase 2 and
    its whole shape is asserted where the record's contract lives.
    """
    session = open_session(house="minimal", vocabulary=vocabulary)
    manifest = ROOT / "packs" / "official" / "example-pack.yaml"
    result = session.install_pack(str(manifest))
    assert result["pack"] == "example_pack"
    assert result["installed"] is True
    assert result["enabled"] == ()


def test_installing_a_pack_does_not_enable_a_behaviour(
    vocabulary: Vocabulary,
) -> None:
    """A house that installs a pack still runs nothing until something enables it.

    Falsified by an `install_pack` that set an enable flag: a light would turn
    on for a reason no operation in the registry describes, and installation
    would have become activation.
    """
    session = open_session(house="minimal", vocabulary=vocabulary)
    manifest = ROOT / "packs" / "official" / "example-pack.yaml"
    session.install_pack(str(manifest))
    session.set_state("binary_sensor.foyer_motion", "on")
    session.advance_time(minutes=1)
    session.set_state("binary_sensor.foyer_motion", "off")
    session.advance_time(minutes=30)
    assert session.read_entity("light.foyer").state == "off"
    outcomes = {record.outcome for record in session.get_decision_log()}
    assert Outcome.ACTED not in outcomes
