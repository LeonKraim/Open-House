/**
 * The websocket command registry the panel speaks.
 *
 * This file *is* the requested API shape. `custom_components/open_house` is
 * built in parallel, so nothing here is read from it; instead the panel names
 * exactly the commands it needs, in one place, with the request and response
 * types for each -- and the integration implements this registry. Every command
 * is namespaced `open_house/` and, unless noted, is answered by the
 * integration's own `websocket_api` handlers.
 *
 * Two rules the integration has to honour for the panel to work:
 *
 *   1. Every command except `capabilities` requires an admin user and answers
 *      with an error code of `unauthorized` otherwise. The panel hides the
 *      admin-only screens from a non-admin rather than letting the calls fail,
 *      but the server is the boundary, not the panel.
 *   2. All CRUD in Phase 5 goes through this API. The panel writes no YAML and
 *      reads no files; the exit criterion "the whole journey runs with no YAML"
 *      is met only if the server can create, bind, install and export over
 *      these commands alone.
 */

export const COMMANDS = {
  /** `{}` -> `Capabilities`. Answers for any authenticated user. */
  capabilities: "open_house/capabilities",

  /** `{}` -> `HouseOverview`. */
  overview: "open_house/overview",

  /** `{}` -> `{ rooms: RoomSummary[] }`. */
  roomsList: "open_house/rooms/list",

  /** `{ room_id }` -> `RoomDetail`. */
  roomGet: "open_house/rooms/get",

  /** `{ name, room_type }` -> `RoomDetail`. */
  roomCreate: "open_house/rooms/create",

  /** `{ room_id, name? }` -> `RoomDetail`. */
  roomUpdate: "open_house/rooms/update",

  /** `{ room_id }` -> `{ room_id }`. */
  roomDelete: "open_house/rooms/delete",

  /** `{ room_id, slot, entity_id }` -> `RoomDetail`. */
  roomBind: "open_house/rooms/bind",

  /**
   * `{ room_id, slot, entity_id }` -> `RoomDetail`.
   *
   * Replace is bind with the intent made explicit: the server records the
   * device it took the slot from in the decision log, which is what makes
   * "replaced the kitchen light" answerable later. A rebind that overwrote a
   * binding silently would be indistinguishable from an initial bind.
   */
  roomReplace: "open_house/rooms/replace",

  /** `{ room_id, slot }` -> `RoomDetail`. Leaves the slot unbound. */
  roomUnbind: "open_house/rooms/unbind",

  /** `{ room_id, slot, query?, limit? }` -> `{ candidates: BindingSuggestion[] }`. */
  roomCandidates: "open_house/rooms/candidates",

  /** `{ room_id }` -> `{ schema, values }`. The high-level options, from the pack schemas. */
  roomOptionsGet: "open_house/rooms/options/get",

  /** `{ room_id, values }` -> `{ schema, values }`. */
  roomOptionsSet: "open_house/rooms/options/set",

  /**
   * `{ room_id }` -> `{ offers: ModuleOffer[] }`.
   *
   * Every offer is returned, each already carrying its satisfiability verdict
   * (`satisfiable`, `missing_slots`) and its conflict check -- the panel renders
   * "add module to room" from this one answer and performs no satisfiability or
   * conflict reasoning of its own.
   */
  roomAvailableModules: "open_house/rooms/available_modules",

  /** `{ room_id, pack }` -> `{ installed: InstalledModule, room: RoomDetail }`. */
  moduleInstall: "open_house/modules/install",

  /** `{ room_id, pack }` -> `RoomDetail`. */
  moduleUninstall: "open_house/modules/uninstall",

  /** `{ room_id, pack, enabled }` -> `InstalledModule`. */
  moduleSetEnabled: "open_house/modules/set_enabled",

  /**
   * `{ room_id, pack, behaviour, enabled }` -> `InstalledModule`.
   *
   * The atom's own switch, and the reason the modules screen lists behaviours at
   * all. A pack is a bag of atoms -- "bedtime" is lights off, plus the
   * thermostat, plus the locks -- and a person who wants the lights and not the
   * locks turns one of them off here rather than not installing the pack.
   * `behaviour` is the `InstalledModule.behaviours[].id` the listing already
   * carries, which is pack-qualified (`bedtime.lights_off`); the declared bare
   * name is accepted too.
   *
   * It answers the whole `InstalledModule` rather than the one behaviour,
   * because the pack's own `enabled` chip is derived from its behaviours -- turn
   * one atom off and the pack is no longer fully on -- and a caller that had to
   * recompute that from a partial answer would be re-implementing the server's
   * rule.
   */
  moduleSetBehaviourEnabled: "open_house/modules/set_behaviour_enabled",

  /**
   * `{ room_id, pack, behaviour, scope }` -> `InstalledModule`.
   *
   * The atom's reach, beside the atom's switch. `scope: "room"` runs a
   * house-wide atom in the one room its module was installed into; `scope:
   * "house"` runs a room's atom for every room. The manifest's declared scope
   * is what a house that has never been asked gets, so this is a preference and
   * not a required choice.
   *
   * It answers the whole `InstalledModule`, for the reason
   * `moduleSetBehaviourEnabled` does: the module's own `scope` chip is derived
   * from its behaviours' scopes, and the atom rows carry `widenable` -- the
   * server's verdict on whether "house" is available to that atom at all.
   */
  moduleSetBehaviourScope: "open_house/modules/set_behaviour_scope",

  /**
   * `{ room_id, pack, behaviour, priority }` -> `InstalledModule`.
   *
   * The atom's rank: the number arbitration sorts by when two behaviours propose
   * for one device in one tick, so a house can say which of two rival modules it
   * would rather have win. The pack declares a rank per behaviour (that is the
   * manifest's clause, and `catalog/pack-policy.yaml` publishes the default), and
   * this is the per-installation answer on top of it.
   *
   * Resolved at *house* scope wherever the module sits, because an entity can be
   * named by units evaluated in different rooms and a rank that varied per room
   * would order the same two proposals differently depending on which room was
   * asked. `priority` is a whole number; `default_priority` on the atom row is
   * what the pack declared, and setting it back there removes the setting rather
   * than recording the declared number.
   */
  moduleSetBehaviourPriority: "open_house/modules/set_behaviour_priority",

  /**
   * `{ room_id, pack, slot, entity_id, label }` -> `InstalledModule`.
   *
   * The per-module, per-slot override: which device *this* module acts on, and
   * what this module calls it. The room's binding is left alone, so every other
   * module keeps acting on the device the room bound and only this one moves --
   * the answer for a person who wants their lamps shut by one pack and the room's
   * group by another, or who wants a pack's own device rather than the shared one.
   *
   * `entity_id` must name an entity the house holds, and `None` clears the
   * override back to the room's binding. `label` is display-only -- the engine
   * never reads it -- and `None` (or a blank) clears it back to the slot's own
   * name. The reply is the module, so the card redraws from what the server
   * actually recorded rather than from what the panel hoped.
   */
  moduleSetSlot: "open_house/modules/set_slot",

  /**
   * `{ slot, action, name, new_name }` -> `{ slot, parts: { name }[] }`.
   *
   * Split a slot into parts, rename one, or take one away. A part is a role's
   * half and it is *still the same slot*: `light_group` split into `a` and `b` is
   * one role with one name, and two modules naming one part act on the one device
   * bound for it -- which is what makes sharing a part a fact and not a promise.
   *
   * `action` is `add`, `rename` or `remove`. Renaming moves every module that was
   * on the part onto the new name, because a part's name *is* the binding key a
   * module names. Removing is refused while a module still names the part, and the
   * refusal names the modules: they all act on the one device the part is, so
   * taking it away would move them all without saying so.
   *
   * **This edits the house's vocabulary, not just a setting.** A part binds under
   * a key of its own (`light_group__a`) and the binding layer refuses a name the
   * vocabulary does not carry, so adding one is a change to what the house can
   * hold -- which is why the server writes it through the session and rebuilds.
   */
  slotSetParts: "open_house/slots/set_parts",

  /**
   * `{ room_id, pack, slot, kind, value, when, device }` -> `InstalledModule`.
   *
   * The "Set it to" half of a slot row: logic on the slot instead of a device a
   * person picked. `kind` is one of `condition`, `template`, `flow`, `script`,
   * and `""` takes the rule back off the row -- the absence of a kind is what "no
   * rule" means, so clearing is the same one write with nothing in it. `value` is
   * the payload in the shape that kind stores: a template's text, a condition's
   * builder config, a flow's own entity id, a script's id.
   *
   * `when` names the entities that should *start* a script rule -- a script runs
   * only when it is called, and nothing can work out from the script alone what
   * should call it. It is empty for the other three kinds, which find their own
   * events: a template is watched by what it reads, a flow by the entity it
   * writes, a condition by the entities it names.
   *
   * `device` is the device a *condition* gates, and is required for that kind
   * alone: a condition answers yes or no and never an entity, so it decides
   * *whether* the slot uses that device rather than *what* the slot is. While the
   * condition holds the slot is that device; while it does not, the slot falls
   * back to the room's binding.
   *
   * **The rule is recorded here and decided by the server.** A template renders,
   * a script is called and a condition is evaluated by the integration's own
   * watcher as the world moves, and the device it works out is written into the
   * row -- so a card shows a device something can move, and the row's rule
   * sentence says what is moving it.
   */
  moduleSetSlotRule: "open_house/modules/set_slot_rule",

  /** `{}` -> `{ modules: InstalledModule[] }`. */
  modulesList: "open_house/modules/list",

  /**
   * `{}` -> `HouseScope`.
   *
   * The house's own slots -- every light, every door, every thermostat, as the
   * rooms' bindings collected in room order -- and the modules that act at
   * house scope. Read-only: a house slot is not bound, it is collected, so the
   * only edit to "all the lights" is a light in a room.
   */
  houseScope: "open_house/house/scope",

  /** `{}` -> `{ profiles: ProfileRef[] }`. */
  profilesList: "open_house/profiles/list",

  /** `{ room_id, axis, profile }` -> `RoomDetail`. */
  profileActivate: "open_house/profiles/activate",

  /**
   * `{ profile }` -> `{ profiles: ProfileRef[] }`.
   *
   * The house profile's own door. A house profile is not selected *in* a room --
   * it is the thing that selects a profile for each room at once -- so it does
   * not share `profileActivate`, which would have to invent a room to be sent
   * one. The answer is the profile list rather than a room, because there is no
   * one room the house just changed.
   */
  profileActivateHouse: "open_house/profiles/activate_house",

  /**
   * `{}` -> `{ profiles: ProfileRef[] }`.
   *
   * Take the house off its house profile. The room selections the profile set
   * stay where they are: a house profile is a bundle, and unbundling it is not
   * the same act as remembering what every room was on before.
   */
  profileDeactivateHouse: "open_house/profiles/deactivate_house",

  /**
   * `{ name }` -> `{ profiles: ProfileRef[] }`.
   *
   * Take a profile *from* the house: what it is on, what it is set to, the packs
   * installed into it, every module it hosts with the configuration that module
   * is on, and its rooms -- all named `name`. The house then goes on it, which
   * for a profile taken from that house is no change at all -- the act is naming
   * what the house is, so that going away and coming back is a selection rather
   * than a rebuild by hand. `name` is slugged by the server the way a module's
   * title is, because it is the identifier every selection is made by.
   */
  profileCapture: "open_house/profiles/capture",

  /**
   * `{ profile, to }` -> `{ profiles: ProfileRef[] }`.
   *
   * Rename a profile. Every room that is on it stays on it: a rename is the
   * profile moving under a new name and not a second profile beside it, which is
   * what a remove-and-add would produce -- and a removal takes every selection
   * that named the profile off with it. `to` is slugged exactly as `name` is.
   */
  profileRename: "open_house/profiles/rename",

  /**
   * `{ profile }` -> `{ profiles: ProfileRef[] }`.
   *
   * Drop a profile, and take everything that named it off with it -- the
   * engine's rule, because a selection names a profile and a selection left
   * pointing at a name the house no longer holds is a failure rather than an
   * answer.
   */
  profileRemove: "open_house/profiles/remove",

  /**
   * `{ profile? }` -> `{ document }`.
   *
   * A profile's export, and every profile's: with `profile` the document is one
   * profile (the frozen `schemas/profile/` shape), and without it a set of them
   * (`{ profiles: [...] }`). Export is a read -- nothing is written and no file
   * is produced on the server side; the panel downloads what it is given.
   */
  profileExport: "open_house/profiles/export",

  /**
   * `{ document, replace? }` -> `{ imported, replaced, profiles }`.
   *
   * The other direction, reading either form `profileExport` writes. A profile
   * this house already holds is refused unless `replace` is set, and every
   * profile in the file is validated before any of them is applied, so a refused
   * import leaves the set exactly as it was.
   */
  profileImport: "open_house/profiles/import",

  /** `{}` -> `{ entries: StoreEntry[], generated_at, cached }`. */
  storeIndex: "open_house/store/index",

  /** `{ pack, tier }` -> `{ installed: InstalledModule }`. */
  storeInstall: "open_house/store/install",

  /** `{ limit?, before? }` -> `{ entries: DecisionLogEntry[] }`. */
  activityList: "open_house/activity/list",

  /**
   * `{}` -> stream of `DecisionLogEntry`.
   *
   * Delivered by `subscribeMessage`, not `callWS`: it is the one command that
   * pushes rather than answers.
   */
  activitySubscribe: "open_house/activity/subscribe",

  /** `{}` -> `{ issues: HealthIssue[] }`. */
  healthList: "open_house/health/list",

  /** `{ room_id }` -> `{ created: boolean, url_path }`. */
  dashboardGenerate: "open_house/dashboard/generate",

  // -- Dev: what may be imported -------------------------------------------
  //
  // `dev/sources` lists what a person may import, so the tab renders a source
  // list before anything has been read. It is the one command here that needs no
  // house: a person can look at what their automations would become before they
  // have finished setting the house up, which is the moment the question is worth
  // answering.

  /**
   * `{}` -> `{ automations: DevSource[], blueprints: DevSource[] }`.
   *
   * `automations` and `blueprints` are what may be imported, read from Home
   * Assistant rather than off disk -- what a person sees here is the automation
   * editor's own document, not a re-parse of `automations.yaml`.
   */
  devSources: "open_house/dev/sources",

  /**
   * `{}` -> `{ modules: HostedModule[] }`.
   *
   * Every module this house hosts, with what it publishes and what each output
   * last read. `value` is `null` for an output that has never been published,
   * which is a different answer from a published zero -- the panel shows the row
   * and says it is unknown rather than hiding it.
   */
  modulesHosted: "open_house/modules/hosted",

  /**
   * `{ kind, key?, text?, bindings?, module?, casts?, flows?, scripts? }` ->
   * `ModuleReadReply`.
   *
   * The import screen's one read: what the source asks for (`inputs`), what it
   * could publish (`candidates`), and what the house already has to bind from
   * (`hosted`). It reads every source the same way, and nothing that can be
   * hosted is refused -- the screen hosts the document as it is rather than
   * translating it.
   *
   * Naming a `module` reads a module this house already runs, which is the same
   * screen opened as an *edit*: the document is the module's own rather than
   * anything sent, and the reply carries it back (`text`) along with every
   * answer that was given about it (`editing`).
   *
   * `casts`, `flows` and `scripts` are the *inputs* the screen has answered with
   * logic -- names, not payloads, because the read is only asking which rows hold
   * a worked-out value rather than a typed one. They matter because a cast row is
   * one more thing a module may publish, so the candidate list is built from the
   * answers the screen has made and not only from the ones the document carries.
   * A **template** cast is not among them: it travels in `bindings`, which is
   * what it is.
   */
  modulesRead: "open_house/modules/read",

  /**
   * `{ kind, key?, text?, title, bindings?, outputs?, settings?, casts?,
   *    flows?, scripts? }` -> `{ module: string, modules: HostedModule[] }`.
   *
   * The automation is created in Home Assistant and only reported once it is
   * really running, so a failure leaves no module to be believed. `outputs` is
   * the ticked candidates, each as `{ name, key }`; `settings` is the inputs to
   * keep settable; `module` is the slug the house now calls it, which is what
   * its outputs' entity ids carry. `casts` is the conditions, `flows` the names
   * of the inputs answered by a flow, and `scripts` the inputs answered by a
   * script, each as `{ input: script_id }`.
   */
  modulesHost: "open_house/modules/host",

  /**
   * `{ module, bindings?, settings?, casts?, flows?, scripts? }` ->
   * `{ module, modules: HostedModule[] }`.
   *
   * A module's settings changed: the automation is built again from the same
   * document with the new answers, under the same id, so it replaces rather than
   * adding a second automation and the outputs keep their entities. `bindings`
   * is merged over what the module already answers, not swapped for it, so a
   * form showing only the exposed settings cannot clear the rest; `casts` and
   * `scripts` are merged the same way, and an *empty* entry in either is how a
   * cast comes back off.
   */
  modulesSettings: "open_house/modules/settings",

  /**
   * `{ module, kind, key?, text?, title, description?, author?, version?,
   *    licence?, bindings?, outputs?, settings?, casts?, flows?, scripts? }` ->
   * `{ module, modules, store }`.
   *
   * **Edit the module, not the copy of it.** `module` is the name the house
   * hosts it under -- what the card knows -- and the edit follows that back to
   * the module behind it: the store row is rewritten and every room running it
   * is built again. An answer a room moved is the room's and stays; one it never
   * moved follows the module.
   *
   * `flows` is a list of input *names* and `scripts` a map of input name to
   * script id, and the difference is the same one `ModuleDefinition` makes: the
   * flow id is Node-RED's to assign per installation, so the installing house
   * pushes the flow and records what it got back, while a script is a thing this
   * Home Assistant already has and is named rather than made.
   */
  modulesEdit: "open_house/modules/edit",

  /**
   * Publish one row's logic as an entity anything may read, or stop publishing
   * it -- the switch beside a cast.
   *
   * `setting` is the *input* whose row the switch sits on, not an output key: the
   * key is what the person called the value, and the input is what finds the
   * thing being toggled. The key is the row's own name, slugged, because a switch
   * has nowhere to type one. On, the value lands at
   * `sensor.open_house_<module>_<key>`, which is an entity like any other and the
   * whole point: the rest of Home Assistant can use it without knowing this
   * integration exists. Off takes the entity away again.
   */
  modulesPublish: "open_house/modules/publish",

  /**
   * `{}` -> `{ store: ModuleOfferRow[], rooms: { id, name }[] }`.
   *
   * The modules this house *offers*: imports that were saved as definitions
   * rather than installed into one room. Each row carries where it is installed
   * -- matched by the definition name every installation kept -- because "have I
   * used this, and where" is one question on one row.
   */
  modulesStore: "open_house/modules/store",

  /**
   * `{ kind, key?, text?, title, description?, author?, version?, licence?,
   *    bindings?, outputs?, settings?, casts?, flows?, scripts?, replace? }` ->
   * `{ module, store }`.
   *
   * Save an imported source as a module this house offers. The last step of the
   * import screen, and it starts nothing: no room, no automation. `module` is the
   * name the definition is stored under, which is also the file's name.
   */
  modulesDefine: "open_house/modules/define",

  /**
   * `{ module, room_id?, bindings?, settings?, casts?, flows?, scripts? }` ->
   * `{ module, modules, store }`.
   *
   * Install a definition into a room, or into the house. Each room gets its own
   * installation, named after the definition and the room, so one definition can
   * run in five rooms without them sharing a name, an automation or a set of
   * outputs. The answers sent are laid over the definition's own.
   */
  modulesDeploy: "open_house/modules/deploy",

  /**
   * `{ module }` -> `{ removed, installed, store }`.
   *
   * Stop offering a definition. Nothing is uninstalled: an installation keeps its
   * own copy of the document, so the rooms running it keep running it, and
   * `installed` says how many that is.
   */
  modulesRemove: "open_house/modules/remove",

  /**
   * `{ module }` -> `{ module, modules }`.
   *
   * Take one installation out of a room -- the other half of `modulesDeploy`,
   * and a different act from `modulesRemove`: that one stops the house offering a
   * definition and leaves every room running it, this one stops one room running
   * its copy and touches neither the offer nor any other room.
   */
  modulesUnhost: "open_house/modules/unhost",

  /**
   * `{ module, input, title?, room_id?, trigger?, revision? }` ->
   * `{ module, title, room_id, key, watched, source, modules: HostedModule[] }`.
   *
   * Detach: the logic one row of a module is answered with becomes a module of
   * its own, and the row comes to point at what that module publishes.
   *
   * `module` is the module the row is on and `input` the row, because the logic
   * is read from the *record*: a cast written into a form and not yet saved is a
   * cast the house does not hold, so it can only ever be detached once the
   * module has it. `room_id` is where the new module sits -- the house, or a
   * room -- and is the same placement every other way of adding a module asks
   * for. `trigger` is what should start the new module where the cast cannot say
   * (a template, a script): without one those two would be modules that never
   * run.
   *
   * The answer names the new module, the output `key` the row should now read,
   * and `watched` -- the entities that will start it, which for a condition is
   * derived from the condition and is otherwise something nobody typed.
   */
  modulesDetach: "open_house/modules/detach",

  /**
   * `{ module }` -> `{ document }`.
   *
   * One definition as a file, blueprint included, for a person to send somebody
   * else -- the same document shape `profiles/export` answers with, so the panel
   * turns it into a download the same way.
   */
  modulesExport: "open_house/modules/export",

  /**
   * `{ document, replace? }` -> `{ imported, replaced, store }`.
   *
   * Take a module file from somebody else into this house's store. `replaced` is
   * whether a module of that name was here already, because "imported" and
   * "imported over the one you had" are different things to have done.
   */
  modulesImport: "open_house/modules/import",

  /**
   * `{ module, config }` -> `{ module, modules: HostedModule[] }`.
   *
   * One of a module's configurations becomes the one it is running. A
   * configuration *is* the person's answers, so this is the settings command by
   * another name: the module is built again from the named answers, into the
   * same automation and the same output entities. Nothing is stopped or started
   * beside anything -- there is one module, answering differently.
   *
   * What the module is holding before the switch is written back to the
   * configuration it was holding it in, so a value edited on the card and not
   * yet saved is not thrown away by switching away from it.
   */
  modulesConfigSwitch: "open_house/modules/configs/switch",

  /**
   * `{ module, config }` -> `{ module, modules: HostedModule[] }`.
   *
   * A new configuration, copied from the one the module is running, and made the
   * running one. A copy rather than a blank: the way to a second configuration
   * is to change one value of the first, and a blank one would be a module with
   * every input unanswered -- which is what the settings rows are for, not the
   * switcher.
   */
  modulesConfigAdd: "open_house/modules/configs/add",

  /**
   * `{ module, config, to }` -> `{ module, modules: HostedModule[] }`.
   *
   * A configuration renamed. Nothing about the module moves -- same answers,
   * same automation, same entities -- so this changes only what the switcher
   * shows and what the other three commands are asked for.
   */
  modulesConfigRename: "open_house/modules/configs/rename",

  /**
   * `{ module, config }` -> `{ module, modules: HostedModule[] }`.
   *
   * A configuration dropped, and any Node-RED flow only it answered with. The
   * last one is refused: a placed module is always running *some* set of
   * answers, and the command that means "stop running it" is `modulesUnhost`.
   * Dropping the running one leaves another running, the way a switch does.
   */
  modulesConfigRemove: "open_house/modules/configs/remove",

  // -- The published Store ---------------------------------------------------
  //
  // `modules/*` above is this *house's* store: the modules it offers, which is
  // where an import lands. What follows is the other one -- the Store other
  // people publish to and this house installs from. Two namespaces rather than
  // one because they are two things: a module this house offers exists whether
  // or not any Store is configured, and everything below is answered from a
  // server this house has to know the address of.

  /**
   * `{}` -> `{ url: string, name: string }`.
   *
   * Whether this house has a published Store to talk to at all.
   *
   * `url` is `""` only when the address is defined *nowhere* -- neither in the
   * integration's settings nor in the default this build of Open House ships
   * with -- and **that is the one state the panel has to ask about**: every other
   * command below refuses politely while there is no address, and nothing here
   * opens a connection until there is one. `name` is this install's publisher
   * name -- `""` until it has claimed one -- and it is what makes "your name,
   * once" visible on the screen rather than only in a file.
   */
  publishedStatus: "open_house/published/status",

  /**
   * `{ url }` -> `{ url, name }`, the new status.
   *
   * Point this house at a Store, or away from one with an empty address.
   *
   * The address is an option of the integration either way, and this is its
   * second writer beside the Configure screen -- the panel writes it so that
   * pressing Publish on a module you have never published can *be* how a house
   * gets a Store, rather than a button that sends you somewhere else first.
   *
   * **A changed address forgets the publisher name claimed on the old one**, for
   * the reason the name is unique in the first place, and the new status says so
   * by answering with the name it now has: `""`. So a caller sets an address and
   * claims a name in that order, and the order is not a formality.
   */
  publishedConfigure: "open_house/published/configure",

  /**
   * `{ name }` -> `{ name }`.
   *
   * Claim this install's publisher name, once, on the Store.
   *
   * **A name belongs to whoever claimed it first.** The Store enforces that with
   * a unique index, so the refusal a second person gets is the backend's own,
   * and it is a refusal with a sentence attached: the name is theirs, pick
   * another. A name refused on its merits -- a space, a capital, a name the
   * Store keeps for itself -- is refused here, before the request, and answered
   * with the same field (`invalid_format`) and the same kind of sentence, so the
   * screen has one thing to render either way.
   *
   * The password is generated by the integration and never shown; a person
   * claims a name, not an account, and there is no email to give.
   */
  publisherClaim: "open_house/published/claim",

  /**
   * `{ search? }` -> `{ installed: PublishedRow[], not_installed: PublishedRow[] }`.
   *
   * The published Store, in the two lists the tab draws: the modules this house
   * already has, and the ones it does not.
   *
   * **Split by the server, not by the screen.** "Do I have this" is a question
   * about this house's own store of definitions, which the server holds and the
   * screen does not -- and the two are matched on the module's *slug* rather
   * than on the Store's record id, so a module whose publisher corrected a typo
   * in its summary is still the module this house has.
   */
  publishedBrowse: "open_house/published/browse",

  /**
   * `{ module, summary? }` -> `{ published: PublishedRow, store: ModuleOfferRow[] }`.
   *
   * Publish one of this house's own modules to the Store, by its name.
   *
   * What is published is the *module definition* -- the same document an export
   * writes and an import reads -- so what a person installs from the Store is
   * exactly what they published, with no second format in between to disagree
   * with the first.
   */
  publishedPublish: "open_house/published/publish",

  /**
   * `{ id, replace? }` -> `{ module, replaced, store }`.
   *
   * Install a module somebody else published: it is taken into this house's own
   * store, where it is a definition like any other and installs into a room the
   * same way. Nothing runs yet.
   *
   * **`id` is the Store's record, not the slug.** Two publishers may offer a
   * module of one name, and a person picked the row they picked.
   */
  publishedInstall: "open_house/published/install",

  /**
   * `{ id, stars }` -> `{ rating: number, stars_count: number }`.
   *
   * Rate a published module from one to five whole stars.
   *
   * A second rating of the same module is the same rating changed rather than a
   * second opinion -- the Store keeps one per person -- and a publisher rating
   * their own module is refused by the Store itself, because an average that
   * included its subject's own vote is a number nobody said.
   */
  publishedRate: "open_house/published/rate",

  /**
   * `{ id }` -> `{ comments: StoreCommentRow[] }`.
   *
   * What people have said about one published module.
   */
  publishedComments: "open_house/published/comments",

  /**
   * `{ id, body }` -> `{ comments: StoreCommentRow[] }`.
   *
   * Say something about one published module. Answers with the list the comment
   * landed in, so the screen does not have to ask twice.
   */
  publishedComment: "open_house/published/comment",
} as const;

export type CommandName = (typeof COMMANDS)[keyof typeof COMMANDS];

/** The `open_house/` domain every error code is read against. */
export const API_DOMAIN = "open_house";

/**
 * The refusals a command answers with, spelled as the server spells them.
 *
 * `websocket_api.py` names each of these once for the same reason: a code
 * compared as a string literal in ten places is ten places a typo hides, and a
 * typo in a comparison is not a build error -- it is a branch that silently
 * never runs. They are here, beside `COMMANDS`, because both are the same
 * contract read from the same two files.
 */
export const REFUSALS = {
  /** The caller is not an administrator. */
  unauthorized: "unauthorized",
  /** No house has ever been made; the setup flow has not been run. */
  notSetup: "not_setup",
  /** A house exists but is between loads. The one worth asking again. */
  notReady: "not_ready",
  /** The room, slot, pack or profile named does not exist. */
  notFound: "not_found",
  /** The request itself is malformed, or a value is refused on its merits. */
  invalidFormat: "invalid_format",
  /**
   * The page wrote from a house it is no longer looking at.
   *
   * A write carries the `revision` the page was rendered from, and the server
   * refuses it when the house's profiles have moved since -- because the values
   * in the write were decided against a profile that is no longer in force, and
   * applying them would land them over the one that is. The page stops being the
   * live page rather than retrying: only a reload can render the house as it now
   * is.
   */
  stalePage: "stale_page",
} as const;

export type RefusalCode = (typeof REFUSALS)[keyof typeof REFUSALS];
