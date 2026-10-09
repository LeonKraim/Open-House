/**
 * The typed face over the websocket API.
 *
 * Every screen talks to the server through one of these methods and never to
 * `hass` directly. The point is not layering for its own sake: a command name
 * typed as a string in ten components is ten places a rename has to reach, and
 * the request and response shapes here are the contract the integration is
 * asked to implement (`protocol.ts`). One method per command makes that
 * contract readable in a single file.
 *
 * The client is stateless. Caching belongs to the element that renders a screen
 * and knows when its data is stale; a client that cached would have to be told
 * when to forget, which is the same information, given to the wrong owner.
 */

import {
  PanelError,
  asPanelError,
  sendMessage,
  type HassLike,
  type UnsubscribeFunc,
} from "./connection.ts";
import { COMMANDS, REFUSALS } from "./protocol.ts";
import type {
  ActivityStreamEvent,
  BindingSuggestion,
  Capabilities,
  DecisionLogEntry,
  DevSource,
  HealthIssue,
  HostedModule,
  HouseOverview,
  HouseScope,
  InstalledModule,
  ModuleBinding,
  ModuleInstallReply,
  ModuleOffer,
  ModuleOfferRow,
  ModuleReadReply,
  ModuleSlotRuleKind,
  ModuleUninstallReply,
  ProfileRef,
  PublishedRow,
  RoomDetail,
  RoomSummary,
  StoreCommentRow,
  StoreStatus,
} from "./models.ts";

/** The response envelope Home Assistant's websocket commands answer with. */
interface ListResponse<T> {
  [key: string]: T[] | undefined;
}

/**
 * How long to keep asking while a reload takes the house away, in delay-per-gap.
 *
 * **Measured, not guessed, and widened once already.** The first list stopped
 * after 2.75s in total -- "the total is under three seconds, which is inside the
 * time a person reads a screen", said the comment above it -- which is shorter
 * than the thing it was waiting for: one integration reload of this house took
 * 8s (a part rename at 15:05:29, the house back at 15:05:37), so a page that
 * wrote and then read was spending its whole budget inside a single window and
 * drawing the reload as a failure -- which is the stale page, seen from
 * underneath. These five gaps cover ~15s, which is longer than a reload of this
 * integration has been observed to take and still bounded, because the window is
 * a reload and not an absence.
 *
 * The cost is real and is the trade being made: a house that is *never* coming
 * back now takes 15s to say so. That case is `not_setup` -- no entry at all --
 * or a refusal about the request itself, and neither is retried, so what waits
 * is only ever the gap a reload leaves.
 */
const RETRY_DELAYS_MS = [250, 750, 2000, 4000, 8000];

/**
 * `revision`, when the caller has one, as a payload key to spread.
 *
 * Absent is not zero, and the server reads the two differently: a write with no
 * `revision` is a write from a caller that is not rendering a page (a script, or
 * a command issued before the read that would have carried one), and it is let
 * through. A write *with* one is a page saying which house it was looking at,
 * and is refused when that house has moved. So the key is omitted rather than
 * defaulted, and this is the one place that decides which.
 */
function withRevision(revision: number | undefined): { revision?: number } {
  return revision === undefined ? {} : { revision };
}

export class OpenHouseClient {
  private readonly hass: HassLike;

  constructor(hass: HassLike) {
    this.hass = hass;
  }

  /** A client bound to the `hass` object a `panel_custom` element was given. */
  static fromHass(hass: HassLike): OpenHouseClient {
    return new OpenHouseClient(hass);
  }

  /**
   * One command, with the reload window retried rather than reported.
   *
   * The retry is here, under every screen, instead of in each screen that might
   * be open when a reload lands -- `rooms` after adding a room, `room` when that
   * list then opens the new room's settings, `capabilities` on the next poll.
   * A screen that read the answer once and stopped rendered it as fact: the
   * settings page of a room that had just been created said "Room not found --
   * This room is no longer in the house", and the panel header said the house had
   * never been set up. Neither was true; both were a reload being read as an
   * absence.
   *
   * Only `not_ready` is retried. `not_found`, `unauthorized` and `invalid_format`
   * are answers about the request, and asking the same question again would only
   * delay telling the person what is actually wrong.
   */
  private async call<T>(
    type: string,
    payload: Record<string, unknown> = {},
  ): Promise<T> {
    for (let attempt = 0; ; attempt += 1) {
      try {
        return await sendMessage<T>(this.hass, { type, ...payload });
      } catch (error) {
        const refusal = asPanelError(error);
        const delay: number | undefined = RETRY_DELAYS_MS[attempt];
        if (refusal.code !== REFUSALS.notReady || delay === undefined) {
          throw refusal;
        }
        await new Promise((resolve) => setTimeout(resolve, delay));
      }
    }
  }

  // -- identity ------------------------------------------------------------

  capabilities(): Promise<Capabilities> {
    return this.call<Capabilities>(COMMANDS.capabilities);
  }

  overview(): Promise<HouseOverview> {
    return this.call<HouseOverview>(COMMANDS.overview);
  }

  // -- rooms ---------------------------------------------------------------

  async rooms(): Promise<RoomSummary[]> {
    const response = await this.call<ListResponse<RoomSummary>>(
      COMMANDS.roomsList,
    );
    return response.rooms ?? [];
  }

  room(roomId: string): Promise<RoomDetail> {
    return this.call<RoomDetail>(COMMANDS.roomGet, { room_id: roomId });
  }

  createRoom(name: string, roomType: string): Promise<RoomDetail> {
    // `room_type`, not `type`: `type` is the websocket discriminator and the
    // server's schema reserves it. See the schema in `websocket_api.py`.
    return this.call<RoomDetail>(COMMANDS.roomCreate, {
      name,
      room_type: roomType,
    });
  }

  updateRoom(roomId: string, name: string): Promise<RoomDetail> {
    return this.call<RoomDetail>(COMMANDS.roomUpdate, {
      room_id: roomId,
      name,
    });
  }

  deleteRoom(roomId: string): Promise<{ room_id: string }> {
    return this.call<{ room_id: string }>(COMMANDS.roomDelete, {
      room_id: roomId,
    });
  }

  bind(
    roomId: string,
    slot: string,
    entityId: string,
    revision?: number,
  ): Promise<RoomDetail> {
    return this.call<RoomDetail>(COMMANDS.roomBind, {
      room_id: roomId,
      slot,
      entity_id: entityId,
      ...withRevision(revision),
    });
  }

  replace(
    roomId: string,
    slot: string,
    entityId: string,
    revision?: number,
  ): Promise<RoomDetail> {
    return this.call<RoomDetail>(COMMANDS.roomReplace, {
      room_id: roomId,
      slot,
      entity_id: entityId,
      ...withRevision(revision),
    });
  }

  unbind(roomId: string, slot: string, revision?: number): Promise<RoomDetail> {
    return this.call<RoomDetail>(COMMANDS.roomUnbind, {
      room_id: roomId,
      slot,
      ...withRevision(revision),
    });
  }

  async candidates(
    roomId: string,
    slot: string,
    query?: string,
  ): Promise<BindingSuggestion[]> {
    const response = await this.call<ListResponse<BindingSuggestion>>(
      COMMANDS.roomCandidates,
      query === undefined
        ? { room_id: roomId, slot }
        : { room_id: roomId, slot, query },
    );
    return response.candidates ?? [];
  }

  roomOptions(
    roomId: string,
  ): Promise<{ schema: unknown; values: Record<string, unknown> }> {
    return this.call(COMMANDS.roomOptionsGet, { room_id: roomId });
  }

  setRoomOptions(
    roomId: string,
    values: Record<string, unknown>,
    revision?: number,
  ): Promise<{ schema: unknown; values: Record<string, unknown> }> {
    return this.call(COMMANDS.roomOptionsSet, {
      room_id: roomId,
      values,
      ...withRevision(revision),
    });
  }

  async availableModules(roomId: string): Promise<ModuleOffer[]> {
    const response = await this.call<ListResponse<ModuleOffer>>(
      COMMANDS.roomAvailableModules,
      { room_id: roomId },
    );
    return response.offers ?? [];
  }

  /**
   * Install a pack into a room, or into the whole house.
   *
   * `roomId` is the placement: a room id, or `""` for the house. The reply
   * carries the page the module landed on -- `room` for a room, `house` for the
   * house -- so a caller re-renders the screen it acted on.
   */
  installModule(roomId: string, pack: string): Promise<ModuleInstallReply> {
    return this.call<ModuleInstallReply>(COMMANDS.moduleInstall, {
      room_id: roomId,
      pack,
    });
  }

  /** Remove a pack, answering with the room's page or the house's. */
  uninstallModule(
    roomId: string,
    pack: string,
  ): Promise<ModuleUninstallReply> {
    return this.call<ModuleUninstallReply>(COMMANDS.moduleUninstall, {
      room_id: roomId,
      pack,
    });
  }

  setModuleEnabled(
    roomId: string,
    pack: string,
    enabled: boolean,
  ): Promise<InstalledModule> {
    return this.call<InstalledModule>(COMMANDS.moduleSetEnabled, {
      room_id: roomId,
      pack,
      enabled,
    });
  }

  /**
   * Turn one behaviour of a pack on or off in one room.
   *
   * `behaviour` is the `InstalledModule.behaviours[].id` from the listing --
   * pack-qualified, e.g. `bedtime.lights_off`. The answer is the whole module,
   * because turning one atom off makes the pack's own chip read "not fully on"
   * and the caller should not have to recompute that rule.
   */
  setModuleBehaviourEnabled(
    roomId: string,
    pack: string,
    behaviour: string,
    enabled: boolean,
  ): Promise<InstalledModule> {
    return this.call<InstalledModule>(COMMANDS.moduleSetBehaviourEnabled, {
      room_id: roomId,
      pack,
      behaviour,
      enabled,
    });
  }

  /**
   * Choose which rooms one behaviour of a pack runs for.
   *
   * `"room"` narrows a house-wide atom to the room its module sits in, `"house"`
   * widens a room's atom to every room. The answer is the whole module, for the
   * reason `setModuleBehaviourEnabled` gives: the module's own scope chip is
   * derived from its behaviours'.
   */
  setModuleBehaviourScope(
    roomId: string,
    pack: string,
    behaviour: string,
    scope: "room" | "house",
  ): Promise<InstalledModule> {
    return this.call<InstalledModule>(COMMANDS.moduleSetBehaviourScope, {
      room_id: roomId,
      pack,
      behaviour,
      scope,
    });
  }

  /**
   * Rank one behaviour of a pack, so two rival modules can be settled.
   *
   * The number arbitration sorts by when two behaviours propose for one device
   * in one tick. `priority` is a whole number; the atom row carries
   * `default_priority`, and sending that back removes the setting rather than
   * recording the declared rank. The answer is the whole module, for the reason
   * `setModuleBehaviourEnabled` gives.
   */
  setModuleBehaviourPriority(
    roomId: string,
    pack: string,
    behaviour: string,
    priority: number,
  ): Promise<InstalledModule> {
    return this.call<InstalledModule>(COMMANDS.moduleSetBehaviourPriority, {
      room_id: roomId,
      pack,
      behaviour,
      priority,
    });
  }

  /**
   * Point one of a module's slots at a device of its own, and name it.
   *
   * The per-module override: the room's binding is untouched, so every other
   * module keeps acting on the device the room bound and only this one moves.
   * `entityId` must name an entity the house holds; `null` clears the override
   * back to the room's binding. `label` is display-only and `null` (or a blank)
   * clears it back to the slot's own name. `part` is which part of a split slot
   * this module is on -- `""` for the slot itself, and `null` for **not touched**,
   * so a reset of the device does not also take the module off its part. The
   * answer is the whole module, so the card redraws from what the server
   * recorded.
   */
  setModuleSlot(
    roomId: string,
    pack: string,
    slot: string,
    entityId: string | null,
    label: string | null,
    part: string | null = null,
  ): Promise<InstalledModule> {
    return this.call<InstalledModule>(COMMANDS.moduleSetSlot, {
      room_id: roomId,
      pack,
      slot,
      entity_id: entityId,
      label,
      part,
    });
  }

  /**
   * Split a slot into parts, rename one, or take one away.
   *
   * A part is a role's half -- `light_group` split into `a` and `b` -- and it is
   * *still the same slot*: two modules naming one part act on the one device the
   * room (or the house) bound for it. `action` is `add`, `rename` or `remove`;
   * `name` is the part, and `new_name` the new one for a rename.
   *
   * Renaming moves every module that was on the part onto the new name, because
   * a part's name *is* the key it binds under and a module names that key.
   * Removing is refused while a module still names the part, and the refusal
   * names the modules -- they all act on the one device the part is, so taking it
   * away would move them without saying so.
   */
  setSlotParts(
    slot: string,
    action: "add" | "rename" | "remove",
    name: string,
    newName = "",
  ): Promise<{ slot: string; parts: { name: string }[] }> {
    return this.call(COMMANDS.slotSetParts, {
      slot,
      action,
      name,
      new_name: newName,
    });
  }

  /**
   * Set one of a module's slots to logic instead of a device ("Set it to").
   *
   * The same four kinds the module input rows offer, held by the slot rather than
   * by an input: `template` and `flow` are self-running, `script` needs a `when`
   * list of entities to call it on, and `condition` needs the `device` it gates.
   * A `kind` of `""` takes the rule back off the slot.
   *
   * Nothing here is decided in the panel. The server records the rule and its own
   * watcher renders the template, calls the script or evaluates the condition as
   * the world moves, writing the slot's entity as it goes -- so the answer is the
   * whole module, redrawn from what the server worked out. `room_id` is which
   * module: the rule belongs to the module, not to the room's binding, and two
   * modules in one room may hold different rules on the same slot.
   */
  setModuleSlotRule(
    roomId: string,
    pack: string,
    slot: string,
    rule: {
      kind: ModuleSlotRuleKind | "";
      value?: unknown;
      when?: string[];
      device?: string | null;
    },
  ): Promise<InstalledModule> {
    return this.call<InstalledModule>(COMMANDS.moduleSetSlotRule, {
      room_id: roomId,
      pack,
      slot,
      kind: rule.kind,
      value: rule.value ?? null,
      when: rule.when ?? [],
      device: rule.device ?? null,
    });
  }

  /**
   * The house's own page: its collected slots and its house-scoped modules.
   *
   * Not filtered by room, because there is no room to filter by -- "all the
   * lights" is every room's, and the members carry the room each one is in.
   */
  houseScope(): Promise<HouseScope> {
    return this.call<HouseScope>(COMMANDS.houseScope, {});
  }

  /**
   * Write the house's own high-level options.
   *
   * The same command a room's form uses, with the house as the placement
   * (`room_id: ""`): the house is a target like a room, so its options are set
   * through the same door and the two forms cannot diverge.
   */
  setHouseOptions(
    values: Record<string, unknown>,
    revision?: number,
  ): Promise<{ schema: unknown; values: Record<string, unknown> }> {
    return this.call(COMMANDS.roomOptionsSet, {
      room_id: "",
      values,
      ...withRevision(revision),
    });
  }

  async modules(): Promise<InstalledModule[]> {
    const response = await this.call<ListResponse<InstalledModule>>(
      COMMANDS.modulesList,
    );
    return response.modules ?? [];
  }

  // -- profiles ------------------------------------------------------------

  async profiles(): Promise<ProfileRef[]> {
    const response = await this.call<ListResponse<ProfileRef>>(
      COMMANDS.profilesList,
    );
    return response.profiles ?? [];
  }

  activateProfile(
    roomId: string,
    axis: string,
    profile: string,
  ): Promise<RoomDetail> {
    return this.call<RoomDetail>(COMMANDS.profileActivate, {
      room_id: roomId,
      axis,
      profile,
    });
  }

  /**
   * Put the house on a house profile.
   *
   * A separate method from `activateProfile` because it is a separate command:
   * a house profile is not selected in a room, and sending it a room id would be
   * inventing one. The answer is the profile list rather than a room detail,
   * which is what a caller renders the new "active" chips from.
   */
  async activateHouseProfile(profile: string): Promise<ProfileRef[]> {
    const response = await this.call<ListResponse<ProfileRef>>(
      COMMANDS.profileActivateHouse,
      { profile },
    );
    return response.profiles ?? [];
  }

  /** Take the house off its house profile. The room selections it set stay. */
  async deactivateHouseProfile(): Promise<ProfileRef[]> {
    const response = await this.call<ListResponse<ProfileRef>>(
      COMMANDS.profileDeactivateHouse,
    );
    return response.profiles ?? [];
  }

  /**
   * Take a profile from the house: what it is on, and what it is set to.
   *
   * `name` is the words a person typed and not an identifier: the server slugs
   * it the way it slugs a module's title, because the name is what every
   * selection of the profile is made by. The house goes on the new profile as
   * part of taking it, so the answer is the profile list with it marked active.
   */
  async captureHouseProfile(name: string): Promise<ProfileRef[]> {
    const response = await this.call<ListResponse<ProfileRef>>(
      COMMANDS.profileCapture,
      { name },
    );
    return response.profiles ?? [];
  }

  /**
   * Rename a profile, keeping every room that is on it on it.
   *
   * `to` is the words a person typed, slugged by the server exactly as `name` is
   * at capture: the name is what a selection is made by, so it is the same rule
   * in both places rather than a second one that could be laxer.
   */
  async renameProfile(profile: string, to: string): Promise<ProfileRef[]> {
    const response = await this.call<ListResponse<ProfileRef>>(
      COMMANDS.profileRename,
      { profile, to },
    );
    return response.profiles ?? [];
  }

  /** Drop a profile, and take everything that named it off with it. */
  async removeProfile(profile: string): Promise<ProfileRef[]> {
    const response = await this.call<ListResponse<ProfileRef>>(
      COMMANDS.profileRemove,
      { profile },
    );
    return response.profiles ?? [];
  }

  /**
   * A profile's export, or every profile's, as the document to keep.
   *
   * One method for both halves because both answer the same thing -- a profile
   * document -- and a second method would be a second place deciding what that
   * is. Without a name the server sends the set form (`{ profiles: [...] }`).
   */
  exportProfile(profile?: string): Promise<Record<string, unknown>> {
    return this.call<{ document: Record<string, unknown> }>(
      COMMANDS.profileExport,
      profile === undefined ? {} : { profile },
    ).then((response) => response.document);
  }

  /**
   * Read a profile document back, answering what was added and what replaced.
   *
   * `replace` is the caller's decision and not a guess: an import that quietly
   * overwrote a profile a person had tuned would be the one write on this screen
   * they cannot see coming.
   */
  importProfiles(
    document: unknown,
    replace = false,
  ): Promise<{ imported: string[]; replaced: string[]; profiles: ProfileRef[] }> {
    return this.call(COMMANDS.profileImport, { document, replace });
  }

  // -- activity and health -------------------------------------------------

  /**
   * The decision log, newest first, narrowed by the tab's filters.
   *
   * The filters are asked of the *server*, not applied to an already-read page,
   * and that is the whole point of them here: the engine records one row per
   * behaviour per scope per tick and a large house reaches thousands of them, so
   * a page of a thousand is a sub-slice of a single tick. Filtering on the
   * server narrows the log *before* the window, so a filtered page fills with
   * matching rows instead of returning the few that happen to fall inside one
   * tick. `limit` bounds how many matching rows come back.
   */
  async activity(
    filters: { outcome?: string; room?: string } = {},
    limit = 1000,
  ): Promise<DecisionLogEntry[]> {
    const response = await this.call<ListResponse<DecisionLogEntry>>(
      COMMANDS.activityList,
      {
        limit,
        ...(filters.outcome ? { outcome: filters.outcome } : {}),
        ...(filters.room ? { room: filters.room } : {}),
      },
    );
    return response.entries ?? [];
  }

  /**
   * Stream new decision-log entries, narrowed by the tab's filters.
   *
   * The one command answered by a subscription rather than a reply. It requires
   * a `connection` and cannot go through `callWS`, which is why it is the one
   * method that does not use `call`. The caller owns the returned unsubscribe
   * and must call it when the element disconnects, or the connection keeps a
   * callback pointing at a removed element.
   *
   * The filters are sent with the subscription so the server drops non-matching
   * records before pushing them: a tick can append thousands, and pushing them
   * all for the panel to discard is the traffic this avoids.
   */
  subscribeActivity(
    onEvent: (event: ActivityStreamEvent) => void,
    filters: { outcome?: string; room?: string } = {},
  ): Promise<UnsubscribeFunc> {
    const connection = this.hass.connection;
    if (!connection) {
      return Promise.reject(
        new PanelError(
          "unavailable",
          "Live activity needs a websocket connection, which this panel does " +
            "not have.",
        ),
      );
    }
    return connection
      .subscribeMessage<ActivityStreamEvent>(onEvent, {
        type: COMMANDS.activitySubscribe,
        ...(filters.outcome ? { outcome: filters.outcome } : {}),
        ...(filters.room ? { room: filters.room } : {}),
      })
      .catch((error: unknown) => {
        throw asPanelError(error);
      });
  }

  async health(): Promise<HealthIssue[]> {
    const response = await this.call<ListResponse<HealthIssue>>(
      COMMANDS.healthList,
    );
    return response.issues ?? [];
  }

  // -- dashboard -----------------------------------------------------------

  generateDashboard(
    roomId: string,
  ): Promise<{ created: boolean; url_path: string }> {
    return this.call(COMMANDS.dashboardGenerate, { room_id: roomId });
  }

  // -- dev: what may be imported -------------------------------------------

  /**
   * What may be imported: this instance's automations and blueprints.
   *
   * The one Dev command answerable with no house, so the tab renders a source
   * list before setup as well as after.
   */
  devSources(): Promise<{
    automations: DevSource[];
    blueprints: DevSource[];
  }> {
    return this.call(COMMANDS.devSources);
  }

  // -- Hosted modules ------------------------------------------------------

  /** Every module this house hosts, with what each output last read. */
  modulesHosted(): Promise<{ modules: HostedModule[] }> {
    return this.call(COMMANDS.modulesHosted);
  }

  /**
   * Read one source as something to host: its inputs, its candidates, the house.
   *
   * `bindings` is the person's choices so far, and it changes the answer: an
   * entity input is only offered as a readable candidate once it has been bound
   * to a device, so a screen that asked before a choice was made would be offered
   * a reading of nothing.
   *
   * `castRows` is the same thing for the rows answered with *logic*: the input
   * names a condition, a flow or a script is behind, because such a row is one
   * more thing the module may publish and the candidate list is built from the
   * screen's own answers as well as the document's. Omitted entirely when the
   * screen has not read yet -- the server then answers from the module's own
   * record, which is the only truth there is about a screen nobody has touched.
   */
  modulesRead(
    kind: "automation" | "blueprint" | "text",
    handle: { key?: string; text?: string },
    bindings: Record<string, ModuleBinding> = {},
    module = "",
    castRows: { casts?: string[]; flows?: string[]; automations?: string[] } = {},
  ): Promise<ModuleReadReply> {
    return this.call(COMMANDS.modulesRead, {
      kind,
      ...handle,
      bindings,
      // Omitted rather than sent empty when this is a fresh import: the two are
      // the same reading of a source and a different reading of a *module*,
      // and `""` is not a module name.
      ...(module ? { module } : {}),
      ...castRows,
    });
  }

  /**
   * Host one source as a module, and answer with the house it landed in.
   *
   * `outputs` is the ticked candidates, each as `{ name, key }` -- the
   * candidate's own name and the key the person called the output. Both are
   * sent because the candidate's name is the blueprint's (`input:lux_sensor` is
   * not an output name) and the key is what the entity is called.
   */
  modulesHost(
    kind: "automation" | "blueprint" | "text",
    handle: { key?: string; text?: string },
    title: string,
    bindings: Record<string, ModuleBinding>,
    outputs: { name: string; key: string }[],
    settings: string[] = [],
    roomId = "",
    flows: string[] = [],
    automations: string[] = [],
  ): Promise<{ module: string; modules: HostedModule[] }> {
    return this.call(COMMANDS.modulesHost, {
      kind,
      ...handle,
      title,
      bindings,
      outputs,
      settings,
      room_id: roomId,
      flows,
      automations,
    });
  }

  /**
   * Change a module's settings, and answer with the house as it now is.
   *
   * The automation is built again under the same id, so this replaces the module
   * rather than making a second one; `bindings` is merged over what the module
   * already answers, so sending only the exposed settings is what the screen is
   * meant to do.
   *
   * `casts`, `flows` and `automations` are the three kinds of *logic* a setting
   * may be answered with rather than by a value, and each travels in its own
   * shape for the reason `protocol.ts` gives: a condition is a config the server
   * turns into an entity, and a flow and an automation are each a set of *names*
   * because the id is Node-RED's or the server's to assign when the module is
   * built.
   */
  modulesSettings(
    module: string,
    bindings: Record<string, ModuleBinding> = {},
    settings?: string[],
    casts?: Record<string, unknown>,
    flows?: string[],
    automations?: string[],
    revision?: number,
  ): Promise<{ module: string; modules: HostedModule[] }> {
    return this.call(COMMANDS.modulesSettings, {
      module,
      bindings,
      settings,
      casts,
      flows,
      automations,
      ...withRevision(revision),
    });
  }

  /**
   * Edit the module a room is running, and every other room running it.
   *
   * **The one module command whose subject is the module rather than a copy of
   * it.** `module` is the name the house hosts it under -- what the card knows --
   * and the server follows that back to the store row behind it, rewrites it, and
   * builds every installation again. Which answers each room ends up with is the
   * server's rule and not this screen's: an answer a room moved is the room's and
   * stays, one it never moved follows the module.
   */
  modulesEdit(
    module: string,
    kind: "automation" | "blueprint" | "text",
    handle: { key?: string; text?: string },
    definition: {
      title: string;
      description?: string;
      author?: string;
      version?: string;
      licence?: string;
      /**
       * **Required, and the six below are the only fields here that are.**
       *
       * They are the module's own answers, and there is no reading of an absent
       * one that is not a reading of an empty one: the server takes a missing
       * `bindings` as "this module now answers nothing", rebuilds every room
       * running it that way and says nothing. The server's schema makes them
       * `vol.Required` for exactly that reason, and this type used to say
       * "optional" -- so a caller who left one out sent nothing at all and got
       * back a `invalid_format` refusal naming a field the type had told them
       * they did not owe. The title above is different: a title nobody sent is a
       * title the module already has.
       */
      bindings: Record<string, ModuleBinding>;
      outputs: { name: string; key: string }[];
      settings: string[];
      casts: Record<string, unknown>;
      flows: string[];
      automations: string[];
    },
  ): Promise<{
    module: string;
    modules: HostedModule[];
    store: ModuleOfferRow[];
  }> {
    return this.call(COMMANDS.modulesEdit, {
      module,
      kind,
      ...handle,
      ...definition,
    });
  }

  /**
   * Publish one row's logic as an entity anything may read, or stop.
   *
   * **The whole of "expose it to the rest of the house", as one switch.** A row
   * answered with logic -- a template, a condition, a flow, a script -- already
   * holds a value, and what this does is put that value at
   * `sensor.open_house_<module>_<key>`, which is an entity like any other: the
   * rest of Home Assistant and its automations can use it without knowing this
   * integration exists. Off takes the entity away again.
   *
   * `setting` names the *input* rather than the output key, because the key is
   * the person's to choose and the input is what the row is. The server slugs the
   * input's own name for the key. It is the module's value and not one room's, so
   * every installation follows -- which is why this answers with the whole house.
   */
  modulesPublish(
    module: string,
    setting: string,
    publish: boolean,
    revision?: number,
  ): Promise<{
    module: string;
    modules: HostedModule[];
    store: ModuleOfferRow[];
  }> {
    return this.call(COMMANDS.modulesPublish, {
      module,
      setting,
      publish,
      ...withRevision(revision),
    });
  }

  // -- configurations: several answer-sets for one placed module ------------

  /**
   * Make one of a module's configurations the one it is running.
   *
   * A configuration *is* the person's answers, so this is `modulesSettings` by
   * another name: the module is built again from the named answers, into the
   * same automation and the same output entities. Everything the card was
   * showing is that configuration's, so the caller refetches -- which is what
   * the reply's module list is for.
   */
  modulesConfigSwitch(
    module: string,
    config: string,
    revision?: number,
  ): Promise<{ module: string; modules: HostedModule[] }> {
    return this.call(COMMANDS.modulesConfigSwitch, {
      module,
      config,
      ...withRevision(revision),
    });
  }

  /** Start a new configuration from the running one, and switch the module to it. */
  modulesConfigAdd(
    module: string,
    config: string,
  ): Promise<{ module: string; modules: HostedModule[] }> {
    return this.call(COMMANDS.modulesConfigAdd, { module, config });
  }

  /** Rename a configuration. Nothing about the module itself moves. */
  modulesConfigRename(
    module: string,
    config: string,
    to: string,
  ): Promise<{ module: string; modules: HostedModule[] }> {
    return this.call(COMMANDS.modulesConfigRename, { module, config, to });
  }

  /** Drop a configuration -- refused when it is the last one the module holds. */
  modulesConfigRemove(
    module: string,
    config: string,
  ): Promise<{ module: string; modules: HostedModule[] }> {
    return this.call(COMMANDS.modulesConfigRemove, { module, config });
  }

  // -- the store: the modules this house offers -----------------------------

  /**
   * The modules this house offers, and where each one is installed.
   *
   * `roomId` decides whose verdict each row's `missing_slots` is: the house by
   * default, which is what the Store tab wants, or a room, which is what "Add
   * module to room" asks -- the same module can be missing a device in one room
   * and not in another.
   */
  modulesStore(roomId = ""): Promise<{
    store: ModuleOfferRow[];
    rooms: { id: string; name: string }[];
  }> {
    return this.call(COMMANDS.modulesStore, { room_id: roomId });
  }

  // -- the published Store --------------------------------------------------

  /**
   * Whether this house has a published Store to talk to, and under what name.
   *
   * Asked before anything else on the tab, because the answer decides what the
   * tab *is*: with no address it is the house's own store of modules, and with
   * one it is that store plus everything below. Nothing here opens a connection
   * when no address is set, so a house that has never heard of the Store is not
   * made to wait for one.
   */
  publishedStatus(): Promise<StoreStatus> {
    return this.call(COMMANDS.publishedStatus, {});
  }

  /**
   * Point this house at a Store, or away from one with an empty address.
   *
   * The second writer of the integration's `store_url` option, beside the
   * Configure screen, and it is what lets the Publish button's own setup step be
   * the way a house gets a Store. Answers with the new status, because the name
   * the caller is about to need is the one this may just have forgotten: an
   * address that changed drops the claim made against the old one.
   */
  publishedConfigure(url: string): Promise<StoreStatus> {
    return this.call(COMMANDS.publishedConfigure, { url });
  }

  /**
   * Claim this install's publisher name.
   *
   * Once: the name is this install's afterwards, and the Store refuses a second
   * person asking for it. The refusal arrives as a `PanelError` whose message is
   * the sentence to show -- "that name is taken" or "names are lower case" --
   * because the two are the same thing to a screen and different things to a
   * person.
   */
  publisherClaim(name: string): Promise<{ name: string }> {
    return this.call(COMMANDS.publisherClaim, { name });
  }

  /**
   * The published Store, split into what this house has and what it does not.
   *
   * The split is the server's, matched on the module's slug against the
   * definitions this house holds.
   */
  publishedBrowse(search = ""): Promise<{
    installed: PublishedRow[];
    not_installed: PublishedRow[];
  }> {
    return this.call(COMMANDS.publishedBrowse, { search });
  }

  /** Publish one of this house's own modules, by the name it is stored under. */
  publishedPublish(
    module: string,
    summary = "",
  ): Promise<{ published: PublishedRow }> {
    return this.call(COMMANDS.publishedPublish, { module, summary });
  }

  /**
   * Take a published module into this house's own store.
   *
   * `id` is the Store's record rather than the slug: two publishers may offer a
   * module of one name, and a person picked the row they picked. `replace` is
   * asked for only when a module of that name is already here.
   *
   * The wire name is `module_id` and not `id` on purpose. A Home Assistant
   * websocket message is one flat object, and `id` in it is already the
   * *message's* own number -- what the reply is matched to. A payload key of the
   * same name does not travel beside it; it overwrites it, the reply is matched
   * to nothing, and the call hangs until the tab is closed. Every other command
   * here passes `room_id`, `slug` or `pack` for the same reason.
   */
  publishedInstall(
    id: string,
    replace = false,
  ): Promise<{ module: string; replaced: boolean; store: ModuleOfferRow[] }> {
    return this.call(COMMANDS.publishedInstall, { module_id: id, replace });
  }

  /** Rate a published module, one to five whole stars. */
  publishedRate(
    id: string,
    stars: number,
  ): Promise<{ rating: number; stars_count: number }> {
    return this.call(COMMANDS.publishedRate, { module_id: id, stars });
  }

  /** What people have said about one published module. */
  publishedComments(id: string): Promise<{ comments: StoreCommentRow[] }> {
    return this.call(COMMANDS.publishedComments, { module_id: id });
  }

  /** Say something about one published module, and get the list back. */
  publishedComment(id: string, body: string): Promise<{ comments: StoreCommentRow[] }> {
    return this.call(COMMANDS.publishedComment, { module_id: id, body });
  }

  /**
   * Save an imported source as a module this house offers.
   *
   * The import screen's last step, and the one that does not touch the house: a
   * definition is a module a person decided the shape of, and installing it is a
   * separate act with a room attached. `replace` is the caller's decision for the
   * reason an import's is: re-importing a blueprint somebody has edited is an
   * update, and overwriting a module they authored has to be asked for.
   */
  modulesDefine(
    kind: "automation" | "blueprint" | "text",
    handle: { key?: string; text?: string },
    definition: {
      title: string;
      description?: string;
      author?: string;
      version?: string;
      licence?: string;
      bindings?: Record<string, ModuleBinding>;
      outputs?: { name: string; key: string }[];
      settings?: string[];
      replace?: boolean;
      /**
       * The inputs answered with a condition rather than with a value, each as
       * Home Assistant's own condition config. Kept apart from `bindings`
       * because a condition is not a value: Open House makes it into an entity
       * of its own and binds the input to that, because a condition written
       * into an input a *trigger* names would be matched as text and never fire.
       */
      casts?: Record<string, unknown>;
      /**
       * The inputs answered with a **flow of nodes**, by input name. A
       * definition carries the *names* and not the flow ids, because the flows
       * are pushed per installation: the id Node-RED assigns belongs to the
       * house hosting the module, and a definition is a thing that moves.
       */
      flows?: string[];
      /**
       * The inputs answered with an **automation**, by input name.
       *
       * Names, like the flows: nothing carries an id here, because the helper and
       * the automation are made by whichever house installs this.
       */
      automations?: string[];
    },
  ): Promise<{ module: string; store: ModuleOfferRow[] }> {
    return this.call(COMMANDS.modulesDefine, { kind, ...handle, ...definition });
  }

  /**
   * Install a module this house offers into a room, or into the house.
   *
   * `bindings` is only what this room answers differently: everything else comes
   * from the definition, which is what defining it once bought.
   */
  modulesDeploy(
    module: string,
    roomId = "",
    bindings: Record<string, ModuleBinding> = {},
    settings?: string[],
    casts?: Record<string, unknown>,
    flows?: string[],
    automations?: string[],
  ): Promise<{ module: string; modules: HostedModule[]; store: ModuleOfferRow[] }> {
    return this.call(COMMANDS.modulesDeploy, {
      module,
      room_id: roomId,
      bindings,
      settings,
      casts,
      flows,
      automations,
    });
  }

  /**
   * Give one row's logic a module of its own, and point the row at it.
   *
   * `module` and `input` name the row: the logic is read from the module's own
   * record rather than from the screen, so a cast that has not been saved is not
   * one there is anything to detach. `roomId` is where the new module sits -- the
   * house, or a room -- and `trigger` is what should start it where the cast
   * cannot say (a template, a script; without one of those a module that never
   * runs).
   *
   * The answer carries the new module's name, the output `key` the row now
   * reads, and `watched`, which is the entities that will start it -- the one
   * part of this a person cannot see from the row they pressed the button beside.
   */
  modulesDetach(
    module: string,
    row: { input: string } | { slot: string },
    title = "",
    roomId = "",
    trigger: string[] = [],
    revision?: number,
  ): Promise<{
    module: string;
    title: string;
    room_id: string;
    key: string;
    watched: string[];
    source: string;
    modules: HostedModule[];
  }> {
    return this.call(COMMANDS.modulesDetach, {
      module,
      // The row, in whichever of its two spellings the caller named. Sent as
      // what it is rather than as a `where` beside a name, because the server
      // reads the one field it was given and a tag that could disagree with it
      // would be a second answer to "which row".
      ...row,
      title,
      room_id: roomId,
      trigger,
      ...withRevision(revision),
    });
  }

  /**
   * Take one installation out of a room, or out of the house.
   *
   * The other half of `modulesDeploy` and a different act from `modulesRemove`:
   * that stops the house offering a module, this stops one room running its copy.
   * The whole hosted list comes back, because the screen that pressed this is a
   * room's page and the list is what it draws.
   */
  modulesUnhost(
    module: string,
  ): Promise<{ module: string; modules: HostedModule[] }> {
    return this.call(COMMANDS.modulesUnhost, { module });
  }

  /**
   * Stop offering a module. What is installed from it keeps running.
   *
   * `installed` is how many installations are still running it, so the screen can
   * say so rather than leaving a person to find out room by room.
   */
  modulesRemove(
    module: string,
  ): Promise<{ removed: string; installed: number; store: ModuleOfferRow[] }> {
    return this.call(COMMANDS.modulesRemove, { module });
  }

  /** One module as the document to keep -- blueprint and answers in one file. */
  modulesExport(module: string): Promise<Record<string, unknown>> {
    return this.call<{ document: Record<string, unknown> }>(
      COMMANDS.modulesExport,
      { module },
    ).then((response) => response.document);
  }

  /**
   * Read a module document back, answering what arrived and what it replaced.
   *
   * Nothing is installed by this: what arrives is a module this house now
   * *offers*. Reading somebody else's file is not consenting to run it.
   */
  modulesImport(
    document: unknown,
    replace = false,
  ): Promise<{ imported: string; replaced: boolean; store: ModuleOfferRow[] }> {
    return this.call(COMMANDS.modulesImport, { document, replace });
  }
}
