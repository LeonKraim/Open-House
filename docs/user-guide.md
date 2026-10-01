# Open House user guide

Open House is a Home Assistant custom integration that runs a house's lighting
for the people in it. It reads the areas and devices the instance already has,
asks a person which areas are rooms, and then decides what to do from the
bindings those rooms carry. A person configures it once through a setup flow and
changes it afterwards through a sidebar panel; the plan's exit criterion for the
panel phase is that the whole journey runs with no YAML, so nothing in the
product asks a person to edit a file.

This guide describes what the code in this repository does, not what the plan
intends it to do. Where a piece is unbuilt, the section on gaps near the end says
so, and the sections before it point there rather than implying behaviour that is
not there.

## Installing and the first run

The integration lives at `custom_components/open_house`, which is where Home
Assistant looks for a custom integration. The plan has it distributed through
HACS; either way, once it is present and Home Assistant has restarted, adding the
integration starts the setup flow, and the sidebar tab appears.

The flow does not read its vocabulary from a copy of it. Both the setup flow and
the running engine read the frozen catalog from the checkout the integration was
loaded out of — `custom_components/` is a directory of that checkout, so the
catalog is an ancestor directory — and inside the container, where the source is
mounted apart from the config directory, it is read from that mount. If the
catalog genuinely is not there, the flow stops and says so rather than inventing
a room list.

### The six steps

The flow is six steps, in an order that is deliberate: confirmation first, then
invention, then consent. Every step after the first carries forward what the ones
before it decided, and the review at the end is built from the same plan that
Activate writes, so what a person reads is what they get rather than a
description of it.

The first step, *confirm areas*, shows every Home Assistant area with all of them
selected, and asks the person to un-tick the ones that are not rooms. An instance
with no areas cannot proceed, and an instance that already has an Open House
entry cannot start a second one.

The second step, *pick room types*, shows one row per chosen area, each a
selector of the room types the catalog names, and each opening on the type the
flow guessed. The step's description lists the areas it recognised with
confidence; when it recognised none, it says so plainly rather than presenting a
guess as a decision.

The third step, *guess bindings*, shows what the flow proposed for each room —
each slot and the entity it would bind, or "nothing yet" — as text to read. There
is nothing to fill in; continuing is the confirmation. The guess reads the
entity registry's view of an area, so only entities actually filed under the area
are proposed.

The fourth step, *choose people*, lists the people Home Assistant knows, all
selected, and asks which of them count for away detection.

The fifth step, *review*, shows the plan in plain language: what each room will
be, and who the away people are. It is generated from the same plan the last step
writes.

The sixth step, *Activate*, writes the plan: one room for each area chosen, the
area's id as the room's own identity so the same area cannot be added twice, and
the away people alongside them.

What the flow decides is rooms, and only rooms — which area, which type, which
entity fills which slot. Behaviours, schedules and thresholds are the engine's,
not the flow's. This matters because it is the reason the review can be trusted:
the flow is describing a plan the engine will read, not promising decisions the
engine has not made.

Adding a room later uses the same shape in miniature: choose an area and a type,
and the flow guesses that room's bindings the same way. An area that is already a
room is not offered.

## Rooms and what each one gets

Home Assistant areas are the source of truth for rooms; a room is the area
projected into the integration. Each room gets four entities, and they are
deliberately thin views onto one piece of state, so that the mode select and any
other view of the same room cannot drift apart by each holding a private copy.

The *mode* select chooses one of the house modes. The *profile* select chooses
which behaviour set the room runs. The *occupied* sensor reports whether the room
is occupied. The *auto-lighting* switch is the room's permission for the engine to
light it.

Occupancy is a reading and not a memory, and it has three answers rather than
two. It is on when the room's bound motion sensor reads motion, off when that
sensor reads clear, and unknown when the room has no motion sensor or the bound
one is unavailable. Unknown is not a stand-in for off: a room whose sensor has
dropped off the network is unreadable, not empty, and reporting it clear would
let an away shutdown empty a room a person is sitting in.

Choosing is all the selects and the switch do. Setting a mode records the choice
and redraws; it does not call a service or move a light. Acting on a mode is the
engine's decision, made behind the adapter, and an entity that acted here would
be a second place the house's behaviour was decided. The auto-lighting switch is
a permission in the same way: turning it off says "do not run this room's lighting
for me", it does not turn the lights off, and turning it on does not turn them on.

The modes and profiles a fresh install offers — Home, Away, Sleep, Guest, and
Default — are defaults this skeleton ships, not the frozen vocabulary. The
engine's modes and profiles are pack data, and the short generic list here is what
the integration offers before a pack replaces it; the entity reports whichever
list is in force, so replacing the vocabulary never strands a person on an option
the select no longer offers.

## The panel

The panel is a sidebar tab titled "Open House", at the URL path `open-house`. It
is registered as admin-only, so Home Assistant does not show it to a non-admin at
all. The tab is registered when the integration loads rather than when a config
entry is set up, because an instance with no rooms yet still needs the one screen
that offers to add them. Two of its tabs, Store and Import/Export, are further
marked admin-only inside the panel; that is a display affordance, and the server
refuses an admin-only command from a non-admin regardless of what is on screen.

The panel is served locally, from `/local/open-house-panel.js` — a file the
instance already knows how to hand to a browser. It makes no remote call: the
screens read the running session through the integration's own websocket
commands, and the ones that read a pack catalog read the checkout's committed
registry rather than a network. The consequence is that the whole thing works on
a house with no internet, and that installing the panel is copying one built
bundle into the instance's `www/` directory.

The panel has eight tabs. *Overview* answers "is anything wrong, and what is the
house doing" from one command, so a room list and a health count are never shown
from two moments taken apart. *Rooms* is the list and the way into one room's
settings page; the settings page lists every slot the room type provides, with
each bound device's live state and health and the actions to rebind or replace
it. *Modules* answers "what is installed, and where", across every room, because
the question a person has when something behaves oddly is a question about the
house and not yet about a room. *Profiles* shows what profiles exist and lets an
admin move a room onto one, keeping the room and the axis together so a person
can see which profiles can run at once. *Store* browses the pack catalog and
installs from it. *Activity* is the engine's decision log as "why did this
happen", with each row expandable to read the reason in full; its live stream is
subscribed on request rather than on mount. *Health* is the list of what is wrong
right now — a dead sensor, a required slot unbound, a pack whose engine range no
longer matches — each linking to the room it concerns. *Import/Export* takes a
full backup and restores one through a dry-run diff, a re-link step for entities
that are gone, and an automatic snapshot so undo is not something a person has to
remember to reach for.

The room settings page shows a pack's high-level options by rendering a schema
the server builds from the installed packs, with no pack-supplied JavaScript; a
pack that adds an option adds it to the schema and the control appears. There are
no such options today — see the gaps.

## What actually makes the house act

The selects, the switch and the occupancy sensor show a house; the engine is the
part that moves a light. It is one engine, held by the live session, and it is
driven from two triggers.

The first is time: the engine is ticked every thirty seconds while nothing
changes. An interval earns its place because the engine notices things no event
announces, chiefly that a room has been quiet long enough to turn its light off,
which is a fact about the passage of time and not about any device's state. The
engine's own quiet timeout is five minutes, and a tick every half minute notices
the timeout within half a minute of it being met.

The second is a state change on an entity the engine only reads. The watched set
is drawn from the domain rather than from a slot name: a `binary_sensor` or
`sensor` bound in a room is an input, and a change to it makes the engine decide
again immediately rather than at the next interval. A `light` is deliberately not
watched, because a state change the component itself caused would wake the
component again, and because the engine's own write is already in its decision
log. So a room's bound motion and lux sensors drive its bound lights through the
engine's decisions and nothing else's.

Changing a room's mode or its auto-lighting switch also reaches the engine: those
choices fan out to the session, which applies them and ticks. A tick is also when
the Activity stream is fed, because the decision log notifies nobody on its own,
and a tick is the only moment it can have grown. A tick that fails is logged with
its traceback and abandoned, and the next interval tries again — the honest
behaviour for a house whose devices are still coming up, and the reason one
malformed room does not stop the timer.

## Packs and the Store

Packs extend what the house does. A pack is a manifest and the behaviours it
declares; the official set lives under `packs/official/`, and the checkout's
`registry/index.json` names the ones the store offers, by path to each manifest.
The Store tab installs one from that committed catalog — no network call is made
or needed — and the Modules tab lists what the house holds and where.

A pack lands in the house, and a module belongs to a room. The panel installs a
module into a room, but the engine's installed set is keyed by pack name, because
one house holds one version of one pack; the room a module reports is the room
whose bindings contain every entity the pack's slots reached. A pack whose slots
are spread over two rooms belongs to neither and reports no room, which is the
honest answer rather than picking one.

Installation is not activation. A pack that arrives with six behaviours arrives
with six disabled behaviours, and the enabling act is the switch. For a
so-called lighting pack this is the same split as the room's auto-lighting switch:
enabling writes a flag and rebuilds nothing, so a house that is running keeps its
modes, its dwell timers and its override records.

## What is not finished

These are real gaps. Nothing above should be read as working where one of these
applies.

A pack's behaviours are recorded when it is installed, but they are not
registered as live engine units. The engine is built from the integration's own
three behaviours — motion lighting, override and away shutdown — and installing a
pack does not add its declared behaviours to that set. The practical effect is
that enabling a module in the panel flips engine state, the enable flag, without
actuating anything: there is no unit registered that reads the flag and acts. The
Modules tab's "enabled" chip reflects the flag, not anything happening in the
house.

The pack format, `pack-manifest/1.2.0`, cannot express two of the things the
official packs are described as doing. A behaviour's action and condition are
block *kinds* with no value beside them, so a manifest can say a behaviour ends
in a service call but cannot say which state it sets, or that it enters a named
mode; and there is no `choose`, so a manifest cannot dispatch on a device's state.
The bed-time pack therefore turns the lights off and can set a thermostat, but
cannot enter Sleep mode, and the Roomba pack declares the services for its four
states but cannot choose between them from the vacuum's current state. Both can
only propose a command; the resulting device state is not asserted. There is a
related limit in the interpreter itself: a proposed command's action is the
service name, and a port that takes a state rather than a service call will write
that name as though it were a state.

The safety alert is written but not wired in. `engine/behaviours/safety_alert.py`
exists and describes a smoke, CO or leak alarm turning the house's lighting on and
being unsuppressible, but it is not in `default_behaviours()`, so it is never
registered and a smoke alarm is currently unhandled. The plan's Phase 8 safety
audit is not met.

The per-room high-level options are empty with today's packs. The room settings
page asks the server for the options its installed packs declare; no pack
declares one, because the manifest schema has no clause for one and no pack
registers a behaviour this build implements, so the answer is empty — a `null`
schema and `{}` values — and the page reads "No installed module declares
options for this room." The machinery to build the form is present and will draw
fields the day a pack declares an option this build can register.

The panel is local-only. It is served from `/local/open-house-panel.js` out of
the instance's own `www/` directory, it is registered as admin-only, and it reads
the checkout's committed catalog rather than a remote one. There is no hosted
panel and no non-admin view of it today.

## About these docs

The plan names MkDocs Material for the project's documentation, and this guide is
built with it: `mkdocs.yml` at the repository root takes `docs/` as its directory
and lists this guide, the attribution file and the reference notes in its
navigation. The plan also names a pack author guide and a security policy. Those
are not written yet; the attribution file is.
