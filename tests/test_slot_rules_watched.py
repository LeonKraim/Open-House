"""A slot rule is watched only if the watcher looks in the right place.

`SlotRuleWatcher._wanted` is the list of rules the house is running, and every
one of them gets a `_Listener`. It is read against the *installed* packs, because
the reader it feeds -- `live_modules.slot_rules_of` -- looks a pack up in the
engine's installed set, so a rule can only ever be found under a name that set
holds. For a while `_wanted` walked the hosted *records* instead: modules a
person imported from a blueprint, whose slugs are not pack names. Every slug was
therefore a name the engine holds no pack under, every `slot_rules_of` answered
`{}`, no listener was built, and a rule a person set was recorded on the row and
simply never re-decided -- the slot sat on the device it had at the moment the
rule was written, for as long as the house ran.

The two readings are reproduced here over one real session: the pair the watcher
asks now (`installed_names_by_room`) finds the rule, and a hosted-record-style
name -- a slug that is not an installed pack -- finds nothing, which is what
every ask did before. The *function itself* is exercised rather than re-stated:
`slot_rules.py` imports `homeassistant` at import time, so it cannot be imported
by this suite, and `_wanted` is read as source through `ast` -- the way
`test_binding_rows.py` reads `modules.py` -- and called with a host that carries
this session. Everything it reaches for, `live_modules`, imports cleanly.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from typing import Any

from engine.solar import Location
from ha_adapter import live_modules
from ha_adapter import slot_rules as rules
from ha_adapter.composition import LiveRoom, room_id
from ha_adapter.live import LiveSession
from ha_adapter.testing import FakeHaTransport

ROOT = Path(__file__).resolve().parents[1]

SLOT_RULES = ROOT / "custom_components" / "open_house" / "slot_rules.py"

#: The committed pack this suite installs, and the slot it declares: the same
#: one `test_live_modules.py` uses, so a rule here is a rule on a real pack.
EXAMPLE = ROOT / "packs" / "official" / "example-pack.yaml"

LOCATION = Location(latitude=51.5, longitude=-0.1, time_zone="Europe/London")

#: A name no installed pack answers to, of the shape a hosted *record* carries --
#: the slug of a module imported from a blueprint rather than of a pack the
#: engine holds. This is the name the old `_wanted` asked about.
HOSTED_SLUG = "example_pack_motion"


def _session() -> LiveSession:
    """The hall, bound, with the example pack installed and a rule on its slot."""
    transport = FakeHaTransport()
    transport.set_state("binary_sensor.hall_motion", "off")
    transport.set_state("sensor.hall_lux", "12")
    transport.set_state("light.hall", "off")
    hall = LiveRoom(
        id=room_id("hall"),
        name="Hall",
        type="hallway",
        bindings={
            "motion_sensor": "binary_sensor.hall_motion",
            "ambient_light_sensor": "sensor.hall_lux",
            "light_group": "light.hall",
        },
    )
    session = LiveSession.build(
        house_name="Test House",
        rooms=(hall,),
        modes=("Home", "Away"),
        transport=transport,
        root=ROOT,
        location=LOCATION,
    )
    live_modules.install(session, EXAMPLE)
    live_modules.set_slot_rule(
        session,
        room_id="hall",
        pack="example_pack",
        slot="light_group",
        kind="template",
        value="{{ states('sensor.hall_lux') }}",
    )
    return session


def _watcher_function(name: str) -> Any:
    """One method of `SlotRuleWatcher`, compiled out of the source it lives in.

    Found by name so it does not break when it moves, and compiled in a namespace
    holding the two modules it reads (`live_modules` and `slot_rules`, both
    importable) rather than the whole module, which cannot be imported at all.
    """
    source = SLOT_RULES.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(SLOT_RULES))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            segment = ast.get_source_segment(source, node)
            assert segment is not None
            namespace: dict[str, Any] = {"live_modules": live_modules, "rules": rules}
            exec("from __future__ import annotations\n" + segment, namespace)
            return namespace[name]
    raise AssertionError(f"{SLOT_RULES.name} has no {name}")


class _Session:
    """The host `_wanted` reads its session off, which is all it touches."""

    def __init__(self, session: LiveSession) -> None:
        self.session = session


class _Watcher:
    """A watcher standing in for `SlotRuleWatcher`, carrying one real session."""

    def __init__(self, session: LiveSession) -> None:
        self._host = _Session(session)


def test_the_watcher_finds_a_rule_on_an_installed_pack() -> None:
    """**The rule the house is running, which is what `_wanted` has to answer.**

    Built from the source, so it is the function the product runs rather than a
    paraphrase: a rule set through `live_modules.set_slot_rule` on an installed
    pack is exactly what the watcher must come back with, keyed by pack, room and
    slot -- the key its `_Listener` is made from.
    """
    session = _session()
    wanted = _watcher_function("_wanted")

    found = asyncio.run(wanted(_Watcher(session)))

    assert list(found) == [("example_pack", "hall", "light_group")]
    assert found[("example_pack", "hall", "light_group")].kind == "template"


def test_the_name_the_watcher_used_to_ask_under_finds_no_rule_at_all() -> None:
    """**The reading that made every listener missing, reproduced.**

    `slot_rules_of` resolves a pack in the engine's installed set, so a hosted
    record's slug -- which is not one -- answers `{}`. That is the whole defect:
    `_wanted` iterated those slugs and got nothing for each, so
    `installed_names_by_room` is the reading that finds a rule and this is the
    reading that never could.
    """
    session = _session()

    assert HOSTED_SLUG not in session.engine.installed.names
    assert (
        dict(live_modules.slot_rules_of(session, pack=HOSTED_SLUG, room_id="hall"))
        == {}
    )
    # And the pack's own name, the pair the fixed `_wanted` builds from, does
    # find it -- the two readings side by side, so a future change that went back
    # to the wrong one fails here rather than on the live house.
    assert ("example_pack", "hall") in live_modules.installed_names_by_room(session)
    assert list(
        live_modules.slot_rules_of(session, pack="example_pack", room_id="hall")
    ) == ["light_group"]
