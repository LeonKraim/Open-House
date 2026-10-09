# Security

What this project protects, what it does not, and where the line between the two
actually falls. It is written to be read before trusting a pack, a broker or the
panel, and it is specific to this codebase rather than to home automation in
general: every claim below names the file that makes it true, and the section at
the end is the honest list of what is left open.

The shape of the project decides most of the answers. A pack is a *description
of intent* the engine carries out, never a program the engine runs. That single
decision is why the pack sandbox can be stated as a list of things a pack cannot
express rather than a list of checks that run while it does — which is the
subject of the first section, and the strongest thing the project has to say
about itself.

## The pack sandbox

`engine/sandbox.py` enforces four rules, and they are decided at *validation*,
before the engine sees the pack at all. Nothing here is a runtime guard: a pack
that validates and installs cannot exceed its grant because the interpreter has
no facility to express the excess. A command, once installed, is a service, a
slot and the entities it names, and there is no field through which a pack could
write a branch, a loop, a variable or a template. A runtime guard would imply the
interpreter *can* express the thing being guarded, and a bug in the guard would
be a security bug rather than a validation bug.

**A pack cannot branch, loop, order or assign.** Its trigger, condition and
action are terms the published vocabulary carries, and none may be a term the
declarative subset forbids. That subset is `declarative_subset` in
`catalog/pack-policy.yaml` and it names what a pack may *not* use — `if`,
`choose`, `repeat`, `parallel`, `variables`, `stop`, `wait_template`,
`wait_for_trigger`, and the `template` trigger and condition. A condition that
reads state is declarative; a condition that computes is a Jinja expression over
the house, which is the one evaluation surface the interpreter must never
acquire, and it is refused.

**A pack cannot name an entity, a device or a room.** It reaches an entity only
through a slot it declares, that its own behaviour names, and that the room the
pack is installed in has bound. A literal `domain.object_id` is not a slot — a
slot name never contains a dot — and a pack that names one is refused as a
`literal_reference`. The reach is bound to what the house supplied: a slot bound
nowhere makes a behaviour that reaches only through it *inert*, not dangerous:
the pack installs because the slot was optional and nothing happens because there
is no entity to act on.

**A pack cannot call a service it did not declare.** The services a manifest's
behaviours name *are* the pack's permission set, computed from those clauses
rather than written a second time, so a behaviour added without the permissions
being edited widens the set. A call outside it is refused. Two published lists
then judge the calls inside it. `banned_services` in `catalog/pack-policy.yaml`
refuses the pack outright, because every service on it acts on the system that
*hosts* the pack rather than on a device in the house — `homeassistant.restart`,
`homeassistant.stop`, `hassio.host_reboot`, `hassio.host_shutdown`,
`hassio.addon_stdin`, `shell_command.*`, `python_script.*`, `update.install`. A
`flagged_services` entry is dangerous and legitimate, so the pack installs and
the install result names the flag; `lock.unlock`, `lock.open` and
`alarm_control_panel.alarm_disarm` are the three. A flag never becomes a refusal,
and the two lists are read by two branches so they cannot be collapsed by a code
path that treats them alike.

**A pack cannot confer a file outside itself.** Every `provides` entry names a
repo-relative path, and that path must resolve to a file inside the pack's own
directory, and the file must be of the class the entry declares. The class is
checked against the file's own shape rather than taken on the entry's word, which
is what stops a pack conferring an artifact nobody can pin.

What a pack therefore cannot reach is worth stating directly: it has no network
access, no filesystem access beyond the `provides` files it names in its own
directory, no way to execute code, and no horizon wider than the slots a room
bound for it. The full list of reasons a pack can be refused — sixteen of them —
is in `docs/reference/pack-sandbox.md`, and the distinction between a term the
vocabulary *does not publish* (`unknown_trigger`) and one it publishes and the
subset forbids (`forbidden_trigger`) is deliberate: an author who wrote `repeat`
is told they wrote a term the project knows and refuses, not that the project has
never heard of it.

The sandbox does one thing narrow enough to name as a gap: it does not check a
binding's domain against the slot it is bound to. A `light_group` slot bound to a
`cover` is a mis-binding it passes.

## Trust boundaries: tiers, revocation and checksums

A pack's `tier` is a statement about how it arrived and what it was allowed to
say when it did. `registry/tiers.yaml` defines four: `official` (published by
the project and signed), `verified` (published by a third party after a human
read the manifest), `community` (published with no review, and therefore
carrying no dangerous permission), and `local` (sideloaded from the user's own
config directory, reviewed by no one and published to nothing). Three of the
four fields are read by a check rather than by a person. The banned list is not
tierable: a service on the banned list is refused whatever tier a pack claims,
because the ban is about what acts on the host rather than on a device in the
house, and a tier can permit a *flagged* service but cannot unban a banned one.

A revocation is not a deletion. `registry/revocations.yaml` is the source of
truth, `registry/revoked.json` is generated from it, and a revoked pack keeps its
pointer and its index entry — the store refuses to install it and says why. A
bare `name` revokes every version of a pack; naming a `version` revokes one
release.

Every published pack is pinned by a checksum. The digest is
`engine.install.digest`, a `sha256:` over the manifest document's canonical JSON
form rather than over the file's bytes, so a reordered key or a changed
indentation is not an edit to a pack. The same digest appears as the `sha256`
field of `registry/index.json` and of each pointer file.

Where that digest is actually checked is the honest part of this section, and it
is narrower than the records suggest. `tools/registry/store.py`'s `verify`
recomputes the digest and refuses a mismatch, and `resolve_verified` refuses a
revoked entry before it will hand back a manifest at all. Those two methods are
the enforcement — and they are reached by `tools/registry/cli.py` and the tests,
not by the path a household actually installs through. The panel's install
command resolves the file through `views.pack_path` (which reads the index only
for the name, the tier and the path) and then calls
`ha_adapter.live_modules.install`, which validates the manifest schema, the
slots against the house and the sandbox, records a freshly computed digest, and
never compares it to the pointer's `sha256`, never consults the revocation list,
and never applies the tier's `permits_flagged`. Put plainly: on the live install
path a pinned digest and a revocation are recorded facts that nothing enforces,
and the tier that is supposed to forbid a community pack a flagged service is a
publish-time check (`tools/registry/checks.py`) and not an install-time one. A
checkout that installs only its own `packs/official/` set does not feel this;
anything broader should not lean on the checksum until the live path calls the
store.

## The Home Assistant surface

The integration's websocket commands are in
`custom_components/open_house/websocket_api.py`. Every command but
`open_house/capabilities` is wrapped in `_admin`, which reads the connection's
user and answers `unauthorized` — "Open House's panel is for administrators" —
whenever the user is absent or is not an administrator. `capabilities` is the one
exception because its whole job is to tell a screen whether it is allowed to ask.

The panel is registered with `require_admin=True` in
`custom_components/open_house/__init__.py`, and the panel is not the boundary: a
websocket command is reachable by anything that can authenticate, so the check is
in the command module and the flag on the panel is a convenience on top of it.

The consequence is worth saying without decoration: **the panel is an
administrator surface, so admin means full control of the house.** Every command
that changes a binding, installs a pack, edits a profile or actuates a device is
admin-only, and an administrator can do all of them. Anyone given an
administrator account in Home Assistant can reach the whole of Open House,
whether or not they can see the sidebar tab.

## Secrets

`.env.local` at the repository root holds `HA_BASE_URL` and `HA_TOKEN`, where
`HA_TOKEN` is a Home Assistant long-lived access token. It is gitignored
(`.env.local`, the first entry in `.gitignore`) and it is used by the local
tooling, not by the integration, which authenticates through Home Assistant's own
config entry rather than through a token of its own.

`docker/ha-config/` is gitignored *wholesale*. That directory is where the
container writes its database, logs, registries and the config onboarding
generates, and the reason the ignore is the whole directory rather than a list
of filenames is the one the `.gitignore` states: so a container run can never
commit a token or a device registry. Our own authored configuration lives in
this repository — `custom_components/`, `packs/`, `engine/` — and is never
written into that directory. The same care reaches the compose file, which mounts
`ha_adapter/`, `engine/`, `schemas/`, `catalog/`, `openhouse/`, `tools/`,
`packs/`, `registry/` and the built panel read-only, so a run cannot diverge from
the repository without anyone noticing.

## The licence boundary

Two directories are gitignored because committing them would be a licence
problem and, in the second case, a security one as well.

`/ressources/` holds the four third-party repositories the corpus is derived
from. They are separate clones with their own history and their own licences, and
two of them (`fwartner`, `johnkoht`) grant nothing at all — no licence file, so
no licence attaches to their code or their prose. They are read as input and
never committed; committing them would relicense nothing and would put four more
`.git` trees inside this one.

`.local/` holds the same records verbatim, including the aliases, display names,
comments and YAML blocks of the two repos that grant nothing — the material a
licence withholds rather than what it lets us keep. It exists so the local
provenance gate can compare a shipped string against its source, and it sits
outside `catalog/` because the committed `catalog/` holds only facts — paths,
ids, classes, vocabulary terms, entity identifiers — and no field that can hold
authored text. The `catalog/` data files must validate against a schema; this
store is the one file the corpus validator is required to reject.

## Reporting a vulnerability

This project has no private security contact, no security mailing list and no
PGP key. There is no address to send a report to, and inventing one here would be
worse than saying so. What the project *does* have is honest about why: making
contact is an outward-facing action, and the two upstream repositories that grant
nothing have authors who were never contacted at all —
[`docs/reference/author-contact.md`](reference/author-contact.md) records both as
`not_attempted`, with a `null` date rather than a plausible-looking one, because
a filled-in date on an unattempted ask is a record that lies in the direction of
looking finished. A project that has not opened a single private channel to the
people whose work it draws on has not established one for the people who might
report a defect in its own.

So: **open an issue on the repository.** Report what you found, name the file and
the command or pack that reached it, and say what an attacker gains. If the
finding is one you would rather not state in public, open an issue that says only
that you have a report and would like a private channel, and one can be arranged
from there. Until then there is no way to send a report privately, and that is a
gap this section states rather than papers over.

## What is not protected

The honest list, in the order the pieces are met.

**The broker is anonymous.** `docker/mosquitto.conf` sets `allow_anonymous true`
and `persistence false` on listener 1883. Anything that can reach port 1883 can
publish and subscribe without credentials, and `docker/docker-compose.yml`
publishes that port. The design intent is a broker for one machine's tests that
holds nothing worth keeping between runs, and a broker with no persistence means
a retained discovery config cannot survive as a device no fixture holds — but the
same two settings mean any process on the network with access to the port can
publish to the house's MQTT integration. The compose file does not put the broker
behind any network isolation.

**The container is privileged and publishes 8123.** The Home Assistant service
runs with `privileged: true` and `ports: - "8123:8123"`, so the instance's
web UI and API are reachable from the host's network and the container has the
host's capabilities. This is described in the compose file as the development and
E2E container rather than a hardened deployment, but nothing in the tree
downgrades it, and a token minted inside that instance reaches whatever
`privileged: true` reaches.

**The panel bundle is served unauthenticated, and deliberately not cached.**
`/config/www/`, which the build mounts, is served by Home Assistant at `/local/`
with a `max-age` of 31 days; the panel does not use that path. `PanelBundleView`
in `custom_components/open_house/__init__.py` reads the built bundle from the
same file on each request and answers `Cache-Control: no-store`, because a
browser holding last month's bundle runs last month's panel against this
integration — controls that no longer exist, commands that are never sent, and
nothing on the screen to say so. The view is reached without a session: the
bundle is client-side code the browser must be able to load before anything can
be authorised, and it carries no secret — the commands it calls are what is
guarded, each one admin-only on the websocket.

**The pinned digest and the revocation list are not enforced on the live install
path.** This is stated in full above and repeated here because it is the one gap
that reads like a guarantee and is not one: `registry/index.json` carries a
`sha256` for every pack and `registry/revocations.yaml` carries a refusal for
every withdrawn one, the panel's install command reads the index, and neither
value is checked before the pack is installed. The store that *does* check both
is reached only by the CLI and the tests. Until that changes, install-time
integrity is a property of the tools, not of the product.

**The sandbox is a validation-time boundary, and it is the only one.** It is the
project's strongest claim and it is worth being exact about what it does not
cover: it does not check a binding's domain against its slot, it does not guard
at runtime (it does not need to, because the interpreter cannot express the
excess), and it decides only whether a command names an entity the pack's own
slots put in its reach — not whether that entity is the right *kind* of thing for
the command.
