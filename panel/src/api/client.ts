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
  HealthIssue,
  HouseOverview,
  HouseScope,
  ImportPreview,
  InstalledModule,
  ModuleInstallReply,
  ModuleOffer,
  ModuleUninstallReply,
  ProfileRef,
  RoomDetail,
  RoomSummary,
  StoreEntry,
} from "./models.ts";
import type { ExportDocument } from "../types/generated.ts";

/** The response envelope Home Assistant's websocket commands answer with. */
interface ListResponse<T> {
  [key: string]: T[] | undefined;
}

/**
 * How long to wait before asking again, in order, with no entry for the last
 * attempt. Spread rather than fixed because the reload that causes this is
 * short in the common case and occasionally not short at all; the total is
 * under three seconds, which is inside the time a person reads a screen.
 */
const RETRY_DELAYS_MS = [250, 700, 1800];

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

  bind(roomId: string, slot: string, entityId: string): Promise<RoomDetail> {
    return this.call<RoomDetail>(COMMANDS.roomBind, {
      room_id: roomId,
      slot,
      entity_id: entityId,
    });
  }

  replace(roomId: string, slot: string, entityId: string): Promise<RoomDetail> {
    return this.call<RoomDetail>(COMMANDS.roomReplace, {
      room_id: roomId,
      slot,
      entity_id: entityId,
    });
  }

  unbind(roomId: string, slot: string): Promise<RoomDetail> {
    return this.call<RoomDetail>(COMMANDS.roomUnbind, {
      room_id: roomId,
      slot,
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
  ): Promise<{ schema: unknown; values: Record<string, unknown> }> {
    return this.call(COMMANDS.roomOptionsSet, { room_id: roomId, values });
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
  ): Promise<{ schema: unknown; values: Record<string, unknown> }> {
    return this.call(COMMANDS.roomOptionsSet, { room_id: "", values });
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

  // -- store ---------------------------------------------------------------

  storeIndex(): Promise<{
    entries: StoreEntry[];
    generated_at: string | null;
    cached: boolean;
  }> {
    return this.call(COMMANDS.storeIndex);
  }

  storeInstall(
    pack: string,
    tier: string,
  ): Promise<{ installed: InstalledModule }> {
    return this.call(COMMANDS.storeInstall, { pack, tier });
  }

  // -- activity and health -------------------------------------------------

  async activity(limit = 100): Promise<DecisionLogEntry[]> {
    const response = await this.call<ListResponse<DecisionLogEntry>>(
      COMMANDS.activityList,
      { limit },
    );
    return response.entries ?? [];
  }

  /**
   * Stream new decision-log entries.
   *
   * The one command answered by a subscription rather than a reply. It requires
   * a `connection` and cannot go through `callWS`, which is why it is the one
   * method that does not use `call`. The caller owns the returned unsubscribe
   * and must call it when the element disconnects, or the connection keeps a
   * callback pointing at a removed element.
   */
  subscribeActivity(
    onEvent: (event: ActivityStreamEvent) => void,
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

  // -- import / export -----------------------------------------------------

  exportDocument(): Promise<ExportDocument> {
    return this.call<ExportDocument>(COMMANDS.exportDocument);
  }

  previewImport(document: unknown): Promise<ImportPreview> {
    return this.call<ImportPreview>(COMMANDS.importPreview, { document });
  }

  applyImport(document: unknown): Promise<{
    applied: boolean;
    snapshot_id: string;
    diff: ImportPreview["diff"];
  }> {
    return this.call(COMMANDS.importApply, { document, snapshot: true });
  }

  generateDashboard(
    roomId: string,
  ): Promise<{ created: boolean; url_path: string }> {
    return this.call(COMMANDS.dashboardGenerate, { room_id: roomId });
  }
}
