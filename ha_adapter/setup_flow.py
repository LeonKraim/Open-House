"""The first-run setup flow, as pure functions the integration renders.

`spec.txt`'s Phase 4 exit criterion is that "a fresh HA install reaches working
automation through the setup flow alone", and the flow's steps are named there:
confirm areas, pick room types, auto-guess bindings, choose people for away
detection, a plain-language review, and Activate. This module is that flow with
Home Assistant taken out of it -- dataclasses in, dataclasses out -- so the part
that can be *wrong* (the guessing, the plain-language review, the document the
activation writes) is tested in a checkout with no Home Assistant installed, and
`custom_components/open_house/config_flow.py` is left with nothing but rendering
and the calls into this module.

**Areas are the source of truth for rooms** (the spec's words). A room is a
`Area`, its name is the area's name, and its id is the area's id; the flow never
invents a room an area does not describe. A room type is the flow's *suggestion*
about how to treat the area, which the person confirms -- `RoomSuggestion`
carries `confident=False` when the guess is a fallback, so the UI can ask rather
than assume.

**Everything here is a guess the person overrides.** The room-type guess reads the
area's name; the binding guess reads the entities the area holds and the slot
vocabulary's accepted domains; neither can be more than a starting point, and
both carry the reason they guessed so the review can say why.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import yaml

from engine import manifest as pack_manifest
from engine.adapter import domain_of
from engine.declared_slots import declared_slots, key_of, slot_keys
from tools.registry.errors import RegistryError
from tools.registry.index import load_index

__all__ = [
    "Area",
    "BindingGuess",
    "Candidate",
    "PersonChoice",
    "ReviewLine",
    "RoomSuggestion",
    "SetupPlan",
    "SetupStep",
    "load_module_slots",
    "load_room_types",
    "load_slot_domains",
    "plan_setup",
    "rank_candidates",
    "setup_steps",
]

#: The room type the flow falls back to when an area's name matches nothing. It
#: is the most generic of the room types the catalog marks `default: true`
#: (`catalog/room_types.yaml`), and it is a *guess the person confirms* -- the
#: suggestion it appears in carries `confident=False` so the UI asks.
_FALLBACK_ROOM_TYPE = "living_room"

#: A word split, used to compare an area or entity name against a room type or
#: slot. `[a-z0-9]+` rather than `\\w+` so an underscore and a hyphen are both
#: separators and `living_room`, `living room` and `Living-Room` compare equal.
_WORDS = re.compile(r"[a-z0-9]+")


class SetupStep(StrEnum):
    """The steps of the first-run flow, in the order the spec names them.

    A closed set, and the order of the members is the order of the flow: the
    review is assembled from what the earlier steps decided, and Activate writes
    what the review showed.
    """

    CONFIRM_AREAS = "confirm_areas"
    PICK_ROOM_TYPES = "pick_room_types"
    GUESS_BINDINGS = "guess_bindings"
    CHOOSE_PEOPLE = "choose_people"
    REVIEW = "review"
    ACTIVATE = "activate"


def setup_steps() -> tuple[SetupStep, ...]:
    """The flow's steps, in order."""
    return tuple(SetupStep)


@dataclass(frozen=True, slots=True)
class Area:
    """One Home Assistant area, as the flow sees it.

    `entity_ids` are the entities Home Assistant reports as belonging to the
    area, which is what the binding guess reads. `floor_name` is carried for the
    review's benefit and is never a key: the area id is.
    """

    area_id: str
    name: str
    entity_ids: tuple[str, ...] = ()
    floor_name: str | None = None


@dataclass(frozen=True, slots=True)
class Candidate:
    """One entity the server proposes for a slot, with its confidence.

    `score` is `0..1` and exists so the *server* owns the order: the panel
    renders the list it is given rather than sorting it, which is what keeps a
    rebind picker and the setup flow's guess from disagreeing about which device
    is the obvious one.

    `friendly_name` falls back to the entity id when nothing has named the
    entity, because a picker row with an empty label is a row a person cannot
    choose between.
    """

    entity_id: str
    friendly_name: str
    domain: str
    score: float


@dataclass(frozen=True, slots=True)
class RoomSuggestion:
    """A room type suggested for one area, and why."""

    area_id: str
    room_type: str
    #: `True` when the area's name matched the room type's words; `False` when
    #: the suggestion is the fallback and the UI should ask rather than assume.
    confident: bool
    reason: str


@dataclass(frozen=True, slots=True)
class BindingGuess:
    """One slot of a room, and the entity guessed for it (or `None`)."""

    area_id: str
    slot: str
    entity_id: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class PersonChoice:
    """One Home Assistant person, and whether they count for away detection."""

    person_id: str
    name: str
    for_away_detection: bool


@dataclass(frozen=True, slots=True)
class ReviewLine:
    """One plain-language line of the review, tied to the room it is about."""

    area_id: str
    text: str


@dataclass(frozen=True, slots=True)
class SetupPlan:
    """Everything the flow decided, ready to be shown and then activated."""

    rooms: tuple[RoomSuggestion, ...]
    bindings: tuple[BindingGuess, ...]
    people: tuple[PersonChoice, ...]
    review: tuple[ReviewLine, ...]

    def bindings_for(self, area_id: str) -> tuple[BindingGuess, ...]:
        """The guesses for one room, in the order they were made."""
        return tuple(binding for binding in self.bindings if binding.area_id == area_id)

    def to_document(self) -> dict[str, object]:
        """The plan as the value Activate writes into the config entry.

        A plain structure -- lists and strings -- because it is stored in Home
        Assistant's config entry data, where only JSON-shaped values survive. A
        room is a subentry (`subentry_type` `"room"`) whose data is its area id,
        its room type and its bindings; the people chosen for away detection are
        one list. Nothing here names a behaviour: this is the setup the engine
        reads, not the engine's decisions.
        """
        rooms: list[dict[str, object]] = []
        for room in self.rooms:
            bound = {
                guess.slot: guess.entity_id
                for guess in self.bindings_for(room.area_id)
                if guess.entity_id is not None
            }
            rooms.append(
                {
                    "subentry_type": "room",
                    "area_id": room.area_id,
                    "room_type": room.room_type,
                    "bindings": bound,
                }
            )
        return {
            "schema_version": 1,
            "rooms": rooms,
            "away_people": [
                person.person_id for person in self.people if person.for_away_detection
            ],
        }


def _words(text: str) -> frozenset[str]:
    return frozenset(_WORDS.findall(text.lower()))


def load_room_types(root: Path) -> dict[str, tuple[str, ...]]:
    """Room type name -> the slots it provides, from `catalog/room_types.yaml`.

    Read from the artifact rather than restated here, so the flow's suggestion
    of what a room needs is the same vocabulary the engine binds against and the
    two cannot drift.
    """
    document = yaml.safe_load((root / "catalog" / "room_types.yaml").read_text("utf-8"))
    return {
        entry["name"]: tuple(entry.get("provides_slots", ()))
        for entry in document["room_types"]
    }


def load_module_slots(root: Path) -> tuple[str, ...]:
    """Every slot the published packs declare, required and optional, sorted.

    **The devices a room can be given are the *modules'* slots and not the room
    type's.** `catalog/room_types.yaml`'s `provides_slots` was the old answer --
    a type's shape deciding what a room of that kind may hold -- and it is not
    this one, because a module may be installed into any room and the slots to
    fill are therefore whatever some module could act through, in whatever room.
    A room type is still a *label* a person picks and still what the flow guesses
    from the area's name; it just no longer decides which devices are
    configurable.

    A pack may also bring a device the catalog does not name -- `fridge_contact`
    for a fridge-door module (`engine/declared_slots.py`) -- and that device is
    one a room must be able to bind, or the module that declares it could never be
    wired. So the pack's own `slots` clause is read here too, and every name comes
    out as the *key* it binds under rather than as the name the manifest wrote: a
    declaration written `separate: true` is bound by the room under a
    pack-qualified key, and a list of written names would offer the panel a slot
    no binding can ever fill.

    Read from `registry/index.json` and the manifests it names -- the same two
    artifacts `ha_adapter.live_modules` offers from -- so the slots a person can
    bind are exactly the slots a pack can require, and the two cannot drift. A
    manifest that will not load is skipped rather than raised: a catalog edited
    into nonsense must not take the setup flow down with it, and the packs that
    do load still name their slots.
    """
    named: set[str] = set()
    for name, document in _published_manifests(root):
        keys = slot_keys(name, document)
        for clause in ("requires_slots", "optional_slots"):
            named.update(keys.get(slot, slot) for slot in _names(document.get(clause)))
        named.update(key_of(name, slot) for slot in declared_slots(document))
    return tuple(sorted(named))


def _names(value: object) -> tuple[str, ...]:
    """A clause that is a list of names, or nothing when it is not one."""
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def load_slot_domains(root: Path) -> dict[str, tuple[str, ...]]:
    """Slot name -> the entity domains it accepts.

    The catalog's rows for every slot it names, and the packs' own declarations
    for the slots it does not: a pack that declares `fridge_contact` states which
    domains a fridge-door contact may come from, and without that row the panel's
    device picker would have no domain to rank candidates by and would offer
    nothing for the very slot the pack needs bound.

    The catalog wins a name it carries, the same precedence
    `ha_adapter.declared_units.with_declared_slots` applies to the vocabulary: a
    pack declaring `door_contact` is reusing the room's own door contact, and
    the catalog is the authority on what that name accepts. A pack that will not
    load contributes nothing rather than failing the caller, for
    `load_module_slots`' reason.
    """
    document = yaml.safe_load((root / "catalog" / "slots.yaml").read_text("utf-8"))
    declared: dict[str, tuple[str, ...]] = {}
    for name, manifest in _published_manifests(root):
        for slot in declared_slots(manifest):
            declared.setdefault(key_of(name, slot), slot.accepts_domains)
    return {
        **declared,
        **{
            entry["name"]: tuple(entry.get("accepts_domains", ()))
            for entry in document["slots"]
        },
    }


def _published_manifests(
    root: Path,
) -> Iterable[tuple[str, Mapping[str, object]]]:
    """Each published pack's name and manifest document, skipping what will not load.

    The index and its manifests read once for every caller that wants the packs'
    own clauses -- the declared slots here and in `load_module_slots` -- with the
    same tolerance: a catalog edited into nonsense must not take the flow down.
    """
    try:
        index = load_index(root / "registry")
    except RegistryError:
        return
    for entry in index.entries:
        try:
            manifest = pack_manifest.load_manifest(root / entry.pointer.path)
        except (OSError, pack_manifest.MalformedManifestError):
            continue
        yield manifest.name, manifest.document


def _suggest_room_type(
    area: Area, room_types: Mapping[str, tuple[str, ...]]
) -> RoomSuggestion:
    """Guess the room type from the area's name.

    The room type whose words the area's name contains, longest match first so
    `living_room` beats a hypothetical `room`; a name nothing matches yields the
    fallback with `confident=False`, which is the difference between "we know"
    and "we are asking".
    """
    area_words = _words(area.name)
    best: str | None = None
    best_overlap = 0
    for name in room_types:
        overlap = len(area_words & _words(name))
        if overlap > best_overlap:
            best, best_overlap = name, overlap
    if best is not None:
        return RoomSuggestion(
            area_id=area.area_id,
            room_type=best,
            confident=True,
            reason=f"the area's name contains the words of the room type {best!r}",
        )
    fallback = (
        _FALLBACK_ROOM_TYPE
        if _FALLBACK_ROOM_TYPE in room_types
        else next(iter(room_types))
    )
    return RoomSuggestion(
        area_id=area.area_id,
        room_type=fallback,
        confident=False,
        reason="the area's name matched no room type, so this is a guess to confirm",
    )


def rank_candidates(
    *,
    slot: str,
    domains: Sequence[str],
    entity_ids: Sequence[str],
    names: Mapping[str, str] | None = None,
    query: str | None = None,
    limit: int | None = None,
) -> tuple[Candidate, ...]:
    """The entities that could fill `slot`, best first, with a confidence each.

    One rule, two readers. The setup flow's guess is the winner of this list and
    the panel's "rebind" picker is the whole of it, so a person who rebinds by
    hand sees the same ordering the flow would have guessed from -- a picker that
    ranked differently from the guesser would make the flow's default look
    arbitrary at exactly the moment someone is deciding whether to trust it.

    The score is the share of the *slot's* words the entity's name accounts for,
    in `0..1`. It is deliberately a fraction of the slot rather than a count of
    shared words: a slot called `motion_sensor` is matched equally well by
    `motion` and by `sensor`, and a count would rank `binary_sensor.hall_motion`
    above `binary_sensor.motion` for no reason a person could see.

    `names` is the friendly name per entity, used only for the returned label --
    the *ranking* never reads it, because a friendly name is what a person typed
    and an entity id is what the house calls the device, and ranking on the
    former would make the order depend on a language.
    """
    slot_words = _words(slot)
    query_words = _words(query) if query else frozenset()
    labels = names or {}
    scored: list[Candidate] = []
    for entity_id in entity_ids:
        domain = domain_of(entity_id)
        if domain not in domains:
            continue
        words = _words(entity_id)
        if query_words and not query_words <= words:
            continue
        score = len(slot_words & words) / len(slot_words) if slot_words else 0.0
        scored.append(
            Candidate(
                entity_id=entity_id,
                friendly_name=labels.get(entity_id, entity_id),
                domain=domain,
                score=round(score, 4),
            )
        )
    scored.sort(key=lambda candidate: (-candidate.score, candidate.entity_id))
    return tuple(scored if limit is None else scored[:limit])


def _guess_binding(
    area: Area,
    slot: str,
    domains: tuple[str, ...],
    room_type: str,
) -> BindingGuess:
    """Guess the entity for one slot: the best-named entity of an accepted domain.

    Only entities in the area are candidates, and only those whose domain the
    slot accepts -- the same domain rule the engine binds by
    (`catalog/slots.yaml`), so a guess can never propose a lamp for a motion
    slot. Ties are broken by entity id so the guess is deterministic. No
    candidate yields `entity_id=None`, which the review shows as a device to
    pick by hand rather than as a binding.

    The ordering is `rank_candidates`', not a second copy of it: the panel's
    "rebind" picker lists that same ranking, and the flow's default has to be
    the top of the list a person is about to be shown.
    """
    ranked = rank_candidates(slot=slot, domains=domains, entity_ids=area.entity_ids)
    if not ranked:
        return BindingGuess(
            area_id=area.area_id,
            slot=slot,
            entity_id=None,
            reason=f"the area holds no entity of domain {_domains(domains)}",
        )
    chosen = ranked[0]
    if chosen.score > 0:
        reason = f"its name shares a word with the {slot!r} slot"
    else:
        reason = f"it is the only {_domains(domains)} in the area"
    return BindingGuess(
        area_id=area.area_id,
        slot=slot,
        entity_id=chosen.entity_id,
        reason=reason,
    )


def _domains(domains: tuple[str, ...]) -> str:
    return " or ".join(domains)


def _review_for(room: RoomSuggestion, guesses: Sequence[BindingGuess]) -> ReviewLine:
    """One plain-language line about a room and what it will control.

    Deliberately prose a person reads rather than a slot list: the review step's
    whole job is to say what the setup will do in words a household understands,
    and it is built from the same guesses the activation writes so the two cannot
    disagree.
    """
    bound = [guess for guess in guesses if guess.entity_id is not None]
    title = room.room_type.replace("_", " ")
    if not bound:
        text = (
            f"{room.area_id} will be a {title}, but no devices are bound yet, so "
            "nothing will move on its own until you pick some."
        )
    else:
        devices = ", ".join(sorted({guess.entity_id for guess in bound}))
        text = f"{room.area_id} will be a {title}, using {devices}."
    return ReviewLine(area_id=room.area_id, text=text)


def plan_setup(
    *,
    areas: Sequence[Area],
    people: Sequence[tuple[str, str]],
    room_types: Mapping[str, tuple[str, ...]],
    slot_domains: Mapping[str, tuple[str, ...]],
    room_type_overrides: Mapping[str, str] | None = None,
    slots: Sequence[str] | None = None,
) -> SetupPlan:
    """Assemble the whole plan from areas, people and the vocabulary.

    `people` is a sequence of `(person_id, name)` pairs. The away-detection
    default is on for every person the instance knows, because the setup's hard
    part is *choosing* and the safe default is that a house tracks the people in
    it; the person turns them off for a guest or a shared account.

    `room_type_overrides` is what the person changed on the room-types step: area
    id to the room type they confirmed. A type the person set replaces the guess
    and is recorded as the room's type.

    `slots` is the slot names a room is guessed against -- every room's, whatever
    its type -- and it defaults to the room type's own `provides_slots` so a
    caller that has not read the registry still gets a plan. The setup flow
    passes `load_module_slots`: the devices a room may be given are the
    *modules*' slots, because a module can be installed into any room, and a
    room type is a label rather than a shape.
    """
    overrides = room_type_overrides or {}
    chosen: list[RoomSuggestion] = []
    for area in areas:
        suggested = _suggest_room_type(area, room_types)
        override = overrides.get(area.area_id)
        if override is not None and override != suggested.room_type:
            suggested = RoomSuggestion(
                area_id=area.area_id,
                room_type=override,
                confident=True,
                reason="you picked this room type",
            )
        chosen.append(suggested)
    rooms = tuple(chosen)
    by_area = {area.area_id: area for area in areas}

    bindings: list[BindingGuess] = []
    review: list[ReviewLine] = []
    wanted = None if slots is None else tuple(slots)
    for room in rooms:
        area = by_area[room.area_id]
        domains = slot_domains
        room_bindings = [
            _guess_binding(area, slot, domains.get(slot, ()), room.room_type)
            for slot in (
                room_types.get(room.room_type, ()) if wanted is None else wanted
            )
        ]
        bindings.extend(room_bindings)
        review.append(_review_for(room, room_bindings))

    people_choices = tuple(
        PersonChoice(person_id=person_id, name=name, for_away_detection=True)
        for person_id, name in people
    )
    return SetupPlan(
        rooms=rooms,
        bindings=tuple(bindings),
        people=people_choices,
        review=tuple(review),
    )
