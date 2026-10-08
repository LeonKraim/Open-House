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
that offers to add them. Two of its tabs, Store and Dev, are further marked
admin-only inside the panel; that is a display affordance, and the server refuses
an admin-only command from a non-admin regardless of what is on screen.

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
it. *House* answers "what is installed, and where", across every room, because
the question a person has when something behaves oddly is a question about the
house and not yet about a room. *Profiles* shows what profiles exist and lets an
admin move a room onto one, keeping the room and the axis together so a person
can see which profiles can run at once; it is also where a profile leaves the
house and comes back — one profile from its own row, or every profile at once, as
a file that imports anywhere. The house-wide and per-room backup files are gone:
a document is a profile and only a profile, so what travels between houses is the
thing a person actually authored. *Activity* is the engine's decision log as "why
did this happen", with each row expandable to read the reason in full; its live
stream is subscribed on request rather than on mount. *Health* is the list of what
is wrong right now — a dead sensor, a required slot unbound, a pack whose engine
range no longer matches — each linking to the room it concerns. *Store* browses
the pack catalog and installs from it, and *Dev* is the workbench that is not for
a person's house at all: it turns an automation into a module and a module back
into automations. Those last two are administrator work rather than a person's
house, so they sit at the end of the strip as one pair, where an admin's tab strip
reads the same as everyone else's with the tools appended rather than interleaved.

**A profile for the whole house, at the top of the Rooms tab.** *Whole house*
sits above the room list, and on the House tab as well, because it is the one
setting both screens are really about. Choosing one there is not a room's
setting: it puts every room on a profile at once *and* puts the house itself
back the way it was — the packs installed, every module hosted with the
configuration it was on, the rooms, each room's own settings, and the house's
own. So it is the answer to "make all of it like that again". **Take a profile
from this house** reads the house in front of you, asks what to call it, and
puts the house on it — which, for one taken from that very house, changes
nothing: the act is naming, so that "the way it was last week" is a selection
rather than a rebuild by hand. **Rename** moves it under the new name and every
room on it stays on it. **Delete** drops it and the house falls back to having
no profile, each room left where it is, because a house profile is a bundle and
unbundling it is not the same as putting the house back. Applying one
*replaces* rather than merges: a pack installed since it was taken, or a module
placed since, comes off, because the point is the house as it stood. The
profiles themselves travel as files in the Profiles tab; taking one from this
house is the one thing authored here. The Profiles tab is also where a profile
is renamed or dropped *without* putting the house on it — the only other way to
reach the rename would be to restore the whole house first.

**A profile you took from this house keeps up with the house.** It is not a
photograph that goes out of date the moment you touch anything. While the house
is *on* a profile taken from it, every change you deliberately make that the
profile covers — a device rebound, a module moved to another configuration, one
of its settings changed — is written into that profile as part of making it, so
"This Evening" means the house as it now is rather than as it was the afternoon
you named it. Making the change and then taking the profile again by hand would
be two steps for one intention, and the second is the one people forget. Two
things are deliberately outside this. A profile that arrived as a *file* rather
than from this house is a set of instructions and is left exactly as written.
And restoring a profile is not an edit to it — the house going back to "This
Evening" does not rewrite "This Evening" with what it found there.

**Switching the house profile disables any room or house page you have open.**
A page draws its controls from values read while one profile was in force, so the
moment the house is put on another, everything on that page is an answer about a
house that no longer exists — and a card's settings save themselves, with nothing
pressed, a moment after the typing stops. That is exactly how a page left open in
another browser tab used to write its old answers over the profile just chosen.
So it cannot happen: a page says **"This page has been disabled because you
switched your house profile. Please reload."**, stops accepting clicks and keys
entirely, and offers one button. Reloading is the only cure, because only a fresh
read draws the house as it is now; nothing on the old page is worth keeping, and
nothing on it would be accepted anyway — the server refuses a write that was
decided against a profile it has since replaced. Switching a profile from the
House tab itself counts: the page you switched it on is as stale as any other.

**Importing as a module.** The Dev tab's *Import as a module* takes any
automation or blueprint and hosts it. Nothing is translated and nothing is
refused: a module is the automation Home Assistant would have run anyway, and
what Open House adds around it is where it sits, what it publishes, and which of
its values stay settable. Each input the source asks for is answered one of five
ways — the blueprint's own default, a literal typed in, a device picked from the
house, another module's output, or a **slot** — and any of those five answers may
then be *cast*, which is the sixth thing a row offers and the one that says what
the input is actually given. **Casting what you picked.** *Every* row carries a
menu beneath it, and the menu chooses what the input is given: **Input field**
leaves the answer above as the answer, and any of the other four swaps that
field out for the editor it names — so a row shows one control, never two, and
the one it shows is the one the module is built with. Empty gives the field back,
which is what makes a cast something reached for on demand rather than a second
answer to keep in step.

Four editors, and two of them are Home Assistant's own. **A condition** is its condition
builder — the same one automations and blueprints carry — and anything that
builder can express works, including conditions this integration has never heard
of. A condition cannot be written where an entity id goes: a state trigger's
`entity_id` is matched against the house's real entities and is never rendered,
so a condition put there would compare as text, match nothing, and leave an
automation that installs and never fires. So Open House works the condition out
itself, publishes the answer as a real `binary_sensor` of its own
(`binary_sensor.open_house_<module>_<input>`), and binds the input to *that* —
which is why a condition is offered on every row, including the ones a trigger
watches, and why "the house is asleep" can be the answer wherever a true or false
is wanted. **A template** is an expression Home Assistant renders into the field
the automation reads. It is the answer for a row nothing else can fill, and it is
the one the menu warns about on a row the trigger names, because there it
installs and never fires. **A Node-RED flow** is the third, and the only one that
is code in another program rather than an answer Open House computes. Pick it and
Open House does the wiring that is a fact about *this* module: it makes a
`sensor` for the input to read, pushes a flow into your Node-RED whose **output
node** hands a value back to it, and binds the input to what that entity holds.
When the row's own answer is a device — something a state trigger can watch — the
flow also arrives with its **input node** already on that device and the two
wired together, so it runs the moment you save. When the row's answer is a number
or a piece of text there is nothing to trigger on, so the flow arrives with the
output node alone and an empty left-hand side: drop in whatever starts it and
wire it over. Either way everything between the two ends is yours, with every
node Node-RED has, and Open House replaces its own two ends and keeps the rest
each time the module is built again. The row's own selector goes when you pick
this, as it does under the other two casts, and the row says instead what the
flow writes, which half arrives built, and where to build the rest. **Node-RED's
own editor is embedded in the row**, under that sentence: the real editor, not a
copy, so a flow is built beside the setting it answers rather than in a tab you
have to find your way back from. Once the flow exists the editor opens on that
flow's tab; before the first save there is no tab yet, so it opens where Node-RED
opens and the row says the flow is made when you save. A **Reload** button
refetches the editor — useful right after a save has pushed a flow that was not
there when the frame loaded — and **Open its own tab** opens the same editor at
the same address in a full tab, for when you want the room. Two things are needed for this and the row says so when they are
missing: an address for your Node-RED, set in the Open House integration's
settings (plus a token, if your Node-RED uses `adminAuth`; the Home Assistant
add-on does), and a Home Assistant *server* node in Node-RED, which any instance
that has used Node-RED with Home Assistant already has. Nothing is written into
the input until the flow itself writes it, so a flow you have not finished
building leaves the input reading `unknown` — which is visible, and not the same
as a value of zero. **HAOS script logic** is the fourth, and it is the one for
logic that is a *sequence* rather than an expression: pick it and the row asks
for a script of yours — one you already have, or one you make in the same place.
Open House calls the script when the module runs and binds the input to what it
hands back (`stop:` with `response_variable`, which is how Home Assistant returns
a value to whoever called it), so the answer can be an if, a loop and a call to
something else, which no single template can say. It is *called* rather than
watched, so — unlike a flow — there is nothing to wire in and no trigger to
connect, and the row says so instead of leaving you looking for one. **Home
Assistant's own script editor is embedded in the row**, the same editor rather
than a link to it, and it opens on the script you picked or on a new one. A
script cannot be written where a trigger names an entity, for the reason a
condition cannot. A slot is the
answer that makes a module reusable: answer the lux input with the room's ambient
light sensor, and
one module works in every room, acting on whichever device that room binds for
it. A slot its room has not bound yet does not stop the import. The module is
hosted and waits — its automation is not created, so nothing it publishes is
being written — and binding that device in the room creates the automation, with
no further import step. **An input that takes a device starts empty**, whatever
the blueprint declares for it: `default: device_tracker.me` names a device on
whoever wrote the blueprint's installation, and taking it for an answer would
point the module at a stranger's phone and make the row look filled. An *empty*
`default:`, which names no device at all, is left as the author wrote it, so an
optional device input stays optional. The same step asks which room the module sits in (the
whole house is the default, for a module that watches the house rather than one
room), because a slot is resolved against that room. Each of the source's own
variables may be ticked to publish as an entity other modules can read, and so
may what it *does*: every service call that drives a device is offered as the
devices it acted on and as each value it set on them, so an automation that turns
a light on at a brightness can publish that brightness to the house — a reading
that exists nowhere in the blueprint as a name.

**A row you answered with logic is offered there too, and ticking it is what
"expose it" means.** Beside the source's own values, the publish list carries
every row holding a cast — a condition, a template, a flow, a script — because
each of those is already a value the module works out. Tick one and it becomes an
entity of its own (`sensor.open_house_<module>_<key>`, the same shape every other
output has), which any automation, dashboard or module in the house can read by
name. Nothing is duplicated: it is *that* answer published, not a second one to
keep in step — the binary sensor Open House makes for a condition, the value a
flow writes, the value a script returns, the expression you typed. A cast on a
row of a module you have already installed is offered in the same list when you
open the module's own Edit screen, so exposing something you built last month is
the same tick as exposing something you are building now.

**A slot can be the house's rather than a room's.** The five answers above
include a slot, and a slot is asked for by name; where an input is answered with
one, the import offers both the room's slots and **A global slot (the whole
house)**. A global slot is the house's own binding: one device standing in for a
role everywhere at once. Our example is *All the lights* — the one light group
the whole house's automations act through — and answering a module with it means
the module drives that group in every room, which is the opposite of what the
room's own slot means. Both are shown, because both are real: a module watching
the room's lux sensor works room by room, and one watching the house's works
everywhere, and the import cannot tell you which you meant. The room's own
binding still wins over the global one where a room has set it, and that is the
one place a global slot does not reach.

**Global slots are shown on every room's page, and bound from there.** A room's
settings page draws a **Whole house** section above the room's own slots, holding
the house's global bindings that this room's modules act through, with the live
state and the same rebind actions the rows below it have. They are listed there
rather than only in the House tab because that section is where a person looks
when a module in this room is acting on the wrong device — and they are *bound*
there too. Choosing a device on a room's page for one of these writes the
house's binding and not that room's, so the House tab shows it a moment later:
it is the same one binding, seen from two places. **Nothing here** clears it back
to what the rooms bind for themselves. One row is the exception and says so: a
role *this* room has bound for itself is answered by that binding, and the global
one stands behind it rather than over it, so the row names the device the room
uses instead.

**Filling a slot uses Home Assistant's own device picker.** The type a slot
accepts is a fact about the slot — a light slot takes lights — and the picker is
the same entity selector the automation editor and the blueprint importer use,
so it searches by friendly name, understands areas and devices, and lists
everything of the right type in one list even where a slot accepts several. The
list is everything your house has of that type, in every room: pointing a room's
slot at a lamp in the hall is allowed, because the binding is the room's either
way and what Home Assistant files under an area is not always what you think of
as being in that room. Where you are is what the list *starts* with — a room's
own devices come first, and a global slot, which belongs to no room, has no room
to lead with. Choosing writes the binding immediately; there is no "Use this"
step, which is one fewer click than the old picker and the same one an
automation's own device field takes.

**A slot can be decided by logic instead of picked — "Set it to".** Every row
under a card's *Acts on* carries a menu beside the device, and it offers the same
four answers the input rows do. It is a different question from the one above it:
the device control says *which* device this module reaches through a slot, and
this says that the device is to be **worked out** rather than chosen — by
something that changes as the house does. A rule belongs to the module and not to
the room, exactly as a device override does, so two modules in one room can hold
different rules on the one slot and neither moves the room's binding.

The four are:

* **A template** — an expression Home Assistant renders, and what it renders to
  is an entity id: *that* is the device this module acts on. It is re-rendered
  whenever anything the template reads changes, so it follows the house by
  itself and there is no "when" to give it.
* **HAOS script logic** — the same script cast the input rows offer, and it
  *returns* the device: the script is called, and the entity id it hands back is
  what the slot becomes. Because a script runs only when somebody calls it, this
  one asks for a **when** — the entities whose change should call it — and says so
  rather than leaving you looking for a trigger. What the script hands back is
  read under the name `oh_value`, so its `stop:` action has to be written
  `response_variable: oh_value` (or into a variable of that name before the
  stop); the row shows the accepted spellings rather than making you guess.
* **A Node-RED flow** — the flow's own entity is the device, so the rule is set
  by picking the entity that flow writes. A flow is already running and already
  writing it, so this is the one kind that was deciding something before you
  asked: Open House just follows it. Save the flow first — there is no entity to
  pick until you have.
* **A condition** — and this one is different from the other three in a way worth
  reading rather than skimming. A condition cannot name a device: Home Assistant's
  conditions answer yes or no and never an entity. So it decides *whether* the
  slot uses a device rather than *what* the slot is. Pick it and the row asks for
  the device the condition **gates**: while the condition holds, this module acts
  on that device; while it does not, the module falls back to what the room (or
  the house) binds for that slot. "The office socket, after dark" is the shape of
  it. The device is recorded with the condition, so switching the rule off
  restores the room's own device rather than losing it.

The row says which of the four is in force in one sentence under the device
(`decided by a script, when sensor.lux changes`), beside the chip naming the
kind, with **Clear** beside it to take the rule back off the slot. Nothing is
written until **Set it** is pressed: a rule is written a field at a time, and the
server refuses a half-written one in its own words — a condition that gates no
device, a script with nothing to call it — so the refusal appears under the row
it belongs to rather than at the top of the page.

**A rule can be detached into a module of its own.** The **Detach...** button
under a slot row does what the same button on an input row does (see below): the
logic moves out into a module of its own, and the slot then reads what that
module publishes, so the same answer is worked out once and anything in the house
can use it.

Each input may be ticked to *keep
as a setting*: every input is kept by default, answered once at import and left
changeable afterwards, rebuilding the automation from the same blueprint rather
than replacing the module or its entities.

**A placed module holds several sets of answers, and you switch between them.**
The Settings section of a card is one configuration; the Configuration bar above
it is where a second one comes from. **New** copies the one the module is running
and makes it the running one, so the way to "these lights, but dimmer in the
evening" is one value changed on a copy rather than the same blueprint placed
twice — and since each room has its own copy of a module, each room can hold its
own set. **Rename** changes what a configuration is called and nothing else.
**Delete** drops one, and is refused when it is the last: a module is always
running *some* set of answers, and the command that means "stop running it" is
taking the module out of the house. **Switching is a rebuild, not a second
module.** It lands on the same automation and the same output entities, so the
module keeps its name, its outputs and every consumer reading them, and only one
configuration is ever running — the house does not end up with two automations
fighting over one light. What each configuration answers is its own, including
which inputs it answers with a Node-RED flow: a flow the dropped configuration
was the last to name comes out of Node-RED with it, and one another configuration
still names stays, so switching back still finds it. What you *save* on a card
belongs to the configuration you saved it in, and switching away leaves it there
rather than carrying it over: switching back shows it again. An edit you have
made and not saved is the exception — a switch loses it, and the bar says so when
there was one.

**A card's settings save themselves.** There is no Save button under the rows:
change a value and the module is built again once the typing stops, into the same
automation and the same outputs, so the room is running what the card is showing
without anybody pressing anything. A cast saves the same way, with one thing held
back — an editor you have opened and not filled in yet is not an answer, so it
waits for the condition or the template that will be one. Choosing a Node-RED flow
*is* an answer, and the flow is pushed as soon as you choose it. What the card
says afterwards is which of the two things happened, because a module waiting for
a device is saved and still not running.

**Detaching a cast makes it a module.** A cast is a small piece of logic attached
to a row — on an input, or on a slot — and **Detach...**, under that row, moves it
out: the same logic becomes a module of its own, hosted like any other import, and
the row then reads what that module publishes (`sensor.open_house_<module>_<key>`)
instead of working the answer out for itself. That is the whole point — one
answer, worked out once, in one place, readable by anything in the house — and it
is the way to stop a condition you are fond of from being re-written in three
modules. The dialog asks the two things a cast cannot answer for itself: what the
new module is called, and where it sits — **the room the logic came from**, or the
**whole house** — which is the same placement choice every other way of adding a
module asks for. A template and a script say nothing about when they should run,
so for those two the dialog also asks what should **start** it; a condition names
the entities it decides about and a flow already runs, so for those the field is
merely a way to watch something extra rather than a requirement. When it is done
the dialog says what the module is called, where it went, and what will start it,
and stays up to be read: for a condition the trigger is *derived* from the
condition, so it is the one fact about the result you could not have worked out
for yourself. Detach is offered only where the house actually holds the logic —
a cast you have typed into a form and not yet saved is not a cast the house has,
so save first; the button says so rather than refusing mysteriously.

**A card's Edit button opens the module itself, not that room's copy of it.** The
button is on the card because a room is where you notice the module wants
changing, but the screen behind it is the one a fresh import uses — what fills
each input, which of those stay settable, what it publishes, what it is called —
arriving filled with the answers the module already carries rather than blank.
The source is the one thing not offered there: every answer names an input *that*
document declares, so making a module out of a different blueprint is an import,
and the screen says so instead of hiding the tabs it does not have. Saving
rebuilds every room running the module, into the same automations and the same
output entities, and **a room that changed one of those answers for itself keeps
its own**: what this screen edits is the answer a room follows, never the answer
a room has given. The save step names the rooms it is about to rebuild, so the
reach of the change is on screen before the button is pressed.

The room settings page shows a pack's high-level options by rendering a schema
the server builds from the installed packs, with no pack-supplied JavaScript; a
pack that adds an option adds it to the schema and the control appears, and the
cards above the page show the same options for the module that declares them.

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

One gap this guide used to record is closed, and it changes what the rest of the
page means, so it is worth saying first. A pack's behaviours are live the moment
it is installed: `ha_adapter/declared_units.py` resolves each installed pack back
to the manifest the registry published and hands the engine those
`DeclaredBehaviour` units beside its own four, so a module enabled in the panel is
a module the engine evaluates, and the Modules tab's "enabled" chip is a fact
about the house rather than about a record.

The pack format still cannot dispatch on what a device reads. `match` gives a
state condition the readings it acts on, `mode` gives a service action the mode it
enters, `for` turns a match from "is open" into "has been open this long", `slots`
lets a pack name a device the catalog does not, and `options` lets it offer a
person a number, a switch or a choice — but there is no `choose`, so a behaviour
cannot branch on the state it just read. The Roomba pack declares the services for
its four states and cannot choose between them from the vacuum's current state.
There is a related limit in the interpreter itself: a proposed command's action is
the service name, and a port that takes a state rather than a service call will
write that name as though it were a state.

The per-room high-level options are drawn from what the room's installed modules
declare. The room settings page asks the server for the options its installed
packs declare and the server builds a JSON Schema from each pack's `options`
clause, so a pack that adds an option adds a field to the form; a room with no
such module answers with a `null` schema and `{}` values and the page reads "No
installed module declares options for this room."

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
