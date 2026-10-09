"""The setup flow, in the six steps the spec names.

The order is confirmation first, then invention, then consent: the person
confirms which areas become rooms, picks each room's type, sees the bindings the
flow guessed, chooses who counts for away detection, reads a plain-language
review of the whole thing, and activates it. Every step after the first carries
forward what the steps before it decided, and the review is built from the same
plan Activate writes -- so what the person read is what they get, not a
description of it.

**The flow decides nothing the engine owns.** It decides *rooms*: which area,
which type, which entity is bound to which slot. It does not decide behaviours,
schedules or thresholds; those are the engine's, and the setup document this flow
writes (`ha_adapter.setup_flow.SetupPlan.to_document`) is the setup the engine
reads, not the engine's answers.

**All the judgement lives in `ha_adapter.setup_flow`.** The guessing, the review
prose and the document are pure functions there, tested without Home Assistant;
this module is rendering and the registry reads, and it is deliberately thin so
there is little here that can be wrong in a way a test cannot see.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryData,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import floor_registry as fr
from homeassistant.helpers.selector import (
    AreaSelector,
    AreaSelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from ha_adapter.setup_flow import (
    Area,
    RoomSuggestion,
    SetupPlan,
    load_module_slots,
    load_room_types,
    load_slot_domains,
    plan_setup,
)

from .const import (
    DATA_AREA_ID,
    DATA_BINDINGS,
    DATA_ROOM_TYPE,
    DOMAIN,
    SUBENTRY_ROOM,
    catalog_root,
)
from .host import entity_ids_in_area
from .node_red import OPTION_EDITOR_URL, OPTION_TOKEN, OPTION_URL
from .store import OPTION_PUBLISHER as STORE_OPTION_PUBLISHER
from .store import OPTION_URL as STORE_OPTION_URL

__all__ = ["OpenHouseConfigFlow", "RoomSubentryFlow"]

#: The schema version the setup document is written at. Bumped when the shape of
#: what Activate stores changes, so the engine can read an older entry knowingly.
_SCHEMA_VERSION = 1

_CONF_AREAS = "areas"
_CONF_PEOPLE = "people"
_CONF_ROOM_TYPE = "room_type"


def _read_areas(hass: HomeAssistant) -> tuple[Area, ...]:
    """Every area, with the entities Home Assistant files under it.

    The entity list is the *effective* area's, so a device assigned to the area
    brings its entities with it -- see `host.entity_ids_in_area`. That is exactly
    the candidate set the binding guess reads, so a device that exists but is
    filed elsewhere is not proposed.
    """
    areas = ar.async_get(hass)
    floors = fr.async_get(hass)
    result: list[Area] = []
    for area in areas.async_list_areas():
        floor = floors.async_get_floor(area.floor_id) if area.floor_id else None
        result.append(
            Area(
                area_id=area.id,
                name=area.name,
                entity_ids=entity_ids_in_area(hass, area.id),
                floor_name=floor.name if floor is not None else None,
            )
        )
    return tuple(result)


def _read_people(hass: HomeAssistant) -> tuple[tuple[str, str], ...]:
    """Every person Home Assistant knows, as `(id, name)` pairs."""
    people: list[tuple[str, str]] = []
    for state in hass.states.async_all("person"):
        name = state.attributes.get("friendly_name", state.entity_id)
        people.append((state.entity_id, str(name)))
    return tuple(people)


class OpenHouseConfigFlow(ConfigFlow, domain=DOMAIN):
    """The six-step first-run flow, from confirming areas to Activate."""

    VERSION = _SCHEMA_VERSION

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Rooms are the one kind of subentry this integration has."""
        return {SUBENTRY_ROOM: RoomSubentryFlow}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """The one thing this integration has to be told rather than shown."""
        return OpenHouseOptionsFlow()

    def __init__(self) -> None:
        self._all_areas: tuple[Area, ...] = ()
        self._chosen_area_ids: list[str] = []
        self._room_type_choices: dict[str, str] = {}
        self._away_people: list[str] = []
        self._room_types: Mapping[str, tuple[str, ...]] = {}
        self._slot_domains: Mapping[str, tuple[str, ...]] = {}
        self._module_slots: tuple[str, ...] = ()

    # -- Steps --------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Enter the flow at its first named step.

        Home Assistant requires the entry step to be `user`; the spec's first
        step is *confirm areas*, so this only hands over to it, and the form a
        person sees is the named one.
        """
        return await self.async_step_confirm_areas(user_input)

    async def async_step_confirm_areas(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm which of the instance's areas become rooms."""
        self._async_abort_entries_match({})
        if not self._load_vocabulary():
            return self.async_abort(reason="catalog_missing")
        self._all_areas = _read_areas(self.hass)
        if not self._all_areas:
            return self.async_abort(reason="no_areas")

        if user_input is not None:
            self._chosen_area_ids = list(user_input[_CONF_AREAS])
            return await self.async_step_pick_room_types()

        schema = vol.Schema(
            {
                vol.Required(
                    _CONF_AREAS,
                    default=[area.area_id for area in self._all_areas],
                ): AreaSelector(AreaSelectorConfig(multiple=True))
            }
        )
        return self.async_show_form(step_id="confirm_areas", data_schema=schema)

    async def async_step_pick_room_types(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick each chosen room's type, defaulting to the flow's suggestion."""
        chosen = self._chosen_areas()
        if user_input is not None:
            self._room_type_choices = {
                area.area_id: user_input[area.area_id][_CONF_ROOM_TYPE]
                for area in chosen
            }
            return await self.async_step_guess_bindings()

        suggestions = {
            area.area_id: _suggestion_for(area, self._room_types) for area in chosen
        }
        room_type_names = sorted(self._room_types)
        # One section per area, keyed by the area id: a section is the only form
        # element Home Assistant groups labels under, and the id cannot collide
        # the way two rooms of the same name would.
        schema = vol.Schema(
            {
                vol.Required(area.area_id): section(
                    vol.Schema(
                        {
                            vol.Required(
                                _CONF_ROOM_TYPE,
                                default=suggestions[area.area_id].room_type,
                            ): SelectSelector(
                                SelectSelectorConfig(options=room_type_names)
                            )
                        }
                    )
                )
                for area in chosen
            }
        )
        return self.async_show_form(
            step_id="pick_room_types",
            data_schema=schema,
            description_placeholders={
                "guessed": ", ".join(
                    f"{area.name}: {suggestions[area.area_id].room_type}"
                    for area in chosen
                    if suggestions[area.area_id].confident
                )
                or "nothing was recognised, so every room is a guess"
            },
        )

    async def async_step_guess_bindings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the bindings the flow guessed, for the person to accept."""
        if user_input is not None:
            return await self.async_step_choose_people()

        plan = self._plan()
        lines = [
            f"{room.area_id}: "
            + ", ".join(
                f"{guess.slot} -> {guess.entity_id or 'nothing yet'}"
                for guess in plan.bindings_for(room.area_id)
            )
            for room in plan.rooms
        ]
        return self.async_show_form(
            step_id="guess_bindings",
            data_schema=vol.Schema({}),
            description_placeholders={"bindings": "\n".join(lines)},
        )

    async def async_step_choose_people(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose which people count for away detection."""
        people = _read_people(self.hass)
        if user_input is not None:
            self._away_people = list(user_input[_CONF_PEOPLE])
            return await self.async_step_review()

        schema = vol.Schema(
            {
                vol.Required(
                    _CONF_PEOPLE,
                    default=[person_id for person_id, _ in people],
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=person_id, label=name)
                            for person_id, name in people
                        ],
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                )
            }
        )
        return self.async_show_form(step_id="choose_people", data_schema=schema)

    async def async_step_review(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Read the plain-language review of what activation will do."""
        if user_input is not None:
            return await self.async_step_activate()

        plan = self._plan()
        return self.async_show_form(
            step_id="review",
            data_schema=vol.Schema({}),
            description_placeholders={
                "review": "\n".join(line.text for line in plan.review),
                "away_people": ", ".join(self._away_people) or "nobody",
            },
        )

    async def async_step_activate(self) -> ConfigFlowResult:
        """Write the plan: one room subentry per room, and the away people."""
        plan = self._plan()
        self._async_abort_entries_match({})
        return self.async_create_entry(
            title="Open House",
            data={
                "schema_version": _SCHEMA_VERSION,
                "away_people": list(self._away_people),
            },
            subentries=[
                ConfigSubentryData(
                    data=room_document,
                    subentry_type=SUBENTRY_ROOM,
                    title=_area_name(self._all_areas, str(room_document[DATA_AREA_ID])),
                    unique_id=str(room_document[DATA_AREA_ID]),
                )
                for room_document in plan.to_document()["rooms"]
                if isinstance(room_document, dict)
            ],
        )

    # -- Internals ----------------------------------------------------------

    def _load_vocabulary(self) -> bool:
        """Read the room types and slot domains, or report that they are absent."""
        root = catalog_root()
        if root is None:
            return False
        self._room_types = load_room_types(root)
        self._slot_domains = load_slot_domains(root)
        self._module_slots = load_module_slots(root)
        return True

    def _chosen_areas(self) -> tuple[Area, ...]:
        wanted = set(self._chosen_area_ids)
        return tuple(area for area in self._all_areas if area.area_id in wanted)

    def _plan(self) -> SetupPlan:
        return plan_setup(
            areas=self._chosen_areas(),
            people=_read_people(self.hass),
            room_types=self._room_types,
            slot_domains=self._slot_domains,
            room_type_overrides=self._room_type_choices,
            slots=self._module_slots,
        )


class OpenHouseOptionsFlow(OptionsFlow):
    """Where the Node-RED this house casts through lives.

    Three fields and they are all things only the person knows. **Two addresses,
    because there are two things that reach Node-RED and they do not travel the
    same way.** Home Assistant opens a connection to it to push a flow, and the
    browser opens a page on it to edit one -- and on a container stack the first
    goes by a service name that resolves only between containers while the second
    goes through a published port or the add-on's ingress. Neither is derivable
    from the other, or from the house. The *token* is optional because it is only
    needed where Node-RED's own `adminAuth` is on: the Home Assistant add-on's
    is, a bare container's is not.

    A house with no Node-RED leaves all three empty, and is not worse off for it:
    every cast but this one works without it, and the row that offers a flow says
    where to come and set the address.

    **The fourth field is the published Store's address, and an empty one is the
    ordinary state.** Most houses run their own modules and never publish or
    install from anybody else's server, so nobody has to fill this in, and a house
    that leaves it empty is not a house missing a setting: nothing outward-facing
    is opened without it, every published-Store command answers politely while it
    is blank, and the tab behaves exactly as it does for a house that has never
    heard of a Store. The address is typed here rather than discovered, because a
    Store is a server somebody else runs and nothing in Home Assistant can find
    one on this house's behalf.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the addresses, or keep what was given."""
        if user_input is not None:
            # Laid over the entry's options rather than swapped for them: the
            # publisher identity a claim stored (`store.OPTION_PUBLISHER`) is not
            # a field on this form, and an update built from the form alone would
            # drop a name this house has already claimed.
            merged = {**self.config_entry.options, **user_input}
            # *Unless the Store itself was changed*, in which case that name is
            # the previous Store's to give and not this one's. Carried across, it
            # authenticates as a stranger -- or, on a Store where somebody else
            # holds the same name, as that person, which is the one thing the
            # uniqueness of a name exists to prevent. So a changed address forgets
            # the claim and the tab asks for a name again, which is the screen
            # that can settle who this house is on the new Store.
            was = str(self.config_entry.options.get(STORE_OPTION_URL) or "").strip()
            now = str(user_input.get(STORE_OPTION_URL) or "").strip()
            if was != now:
                merged.pop(STORE_OPTION_PUBLISHER, None)
            return self.async_create_entry(data=merged)
        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        OPTION_URL, default=str(options.get(OPTION_URL) or "")
                    ): str,
                    vol.Optional(
                        OPTION_EDITOR_URL,
                        default=str(options.get(OPTION_EDITOR_URL) or ""),
                    ): str,
                    vol.Optional(
                        OPTION_TOKEN, default=str(options.get(OPTION_TOKEN) or "")
                    ): str,
                    vol.Optional(
                        STORE_OPTION_URL,
                        default=str(options.get(STORE_OPTION_URL) or ""),
                    ): str,
                }
            ),
        )


def _suggestion_for(
    area: Area, room_types: Mapping[str, tuple[str, ...]]
) -> RoomSuggestion:
    """The flow's room-type guess for one area, as a standalone suggestion.

    `plan_setup` makes the same guess; this exposes it for the pick step's
    default, so the select a person sees opens on exactly the room type the
    guess would have chosen.
    """
    return plan_setup(
        areas=(area,),
        people=(),
        room_types=room_types,
        slot_domains={},
    ).rooms[0]


def _area_name(areas: Sequence[Area], area_id: str) -> str:
    for area in areas:
        if area.area_id == area_id:
            return area.name
    return area_id


class RoomSubentryFlow(ConfigSubentryFlow):
    """Add or change one room: an area, its type, and its bindings."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a room, refusing an area that is already a room."""
        entry = self._get_entry()
        taken = {
            subentry.unique_id
            for subentry in entry.subentries.values()
            if subentry.subentry_type == SUBENTRY_ROOM
        }
        areas = tuple(
            area for area in _read_areas(self.hass) if area.area_id not in taken
        )
        if not areas:
            return self.async_abort(reason="no_areas")

        root = catalog_root()
        if root is None:
            return self.async_abort(reason="catalog_missing")
        room_types = load_room_types(root)
        slot_domains = load_slot_domains(root)
        module_slots = load_module_slots(root)

        if user_input is not None:
            area_id = str(user_input[DATA_AREA_ID])
            area = next(
                (candidate for candidate in areas if candidate.area_id == area_id), None
            )
            if area is None:
                return self.async_abort(reason="already_added")
            plan = plan_setup(
                areas=(area,),
                people=(),
                room_types=room_types,
                slot_domains=slot_domains,
                room_type_overrides={area.area_id: user_input[DATA_ROOM_TYPE]},
                slots=module_slots,
            )
            room = plan.rooms[0]
            return self.async_create_entry(
                title=area.name,
                data={
                    DATA_AREA_ID: area.area_id,
                    DATA_ROOM_TYPE: room.room_type,
                    DATA_BINDINGS: _bindings_document(plan, area.area_id),
                },
                unique_id=area.area_id,
            )

        schema = vol.Schema(
            {
                vol.Required(DATA_AREA_ID): AreaSelector(AreaSelectorConfig()),
                vol.Required(DATA_ROOM_TYPE): SelectSelector(
                    SelectSelectorConfig(options=sorted(room_types))
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)


def _bindings_document(plan: SetupPlan, area_id: str) -> dict[str, str]:
    """The plan's bindings for one room, as the slot -> entity mapping stored."""
    return {
        guess.slot: guess.entity_id
        for guess in plan.bindings_for(area_id)
        if guess.entity_id is not None
    }
