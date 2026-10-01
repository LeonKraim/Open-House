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
import { COMMANDS } from "./protocol.ts";
import type {
  ActivityStreamEvent,
  BindingSuggestion,
  Capabilities,
  DecisionLogEntry,
  HealthIssue,
  HouseOverview,
  ImportPreview,
  InstalledModule,
  ModuleOffer,
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

export class OpenHouseClient {
  private readonly hass: HassLike;

  constructor(hass: HassLike) {
    this.hass = hass;
  }

  /** A client bound to the `hass` object a `panel_custom` element was given. */
  static fromHass(hass: HassLike): OpenHouseClient {
    return new OpenHouseClient(hass);
  }

  private async call<T>(
    type: string,
    payload: Record<string, unknown> = {},
  ): Promise<T> {
    try {
      return await sendMessage<T>(this.hass, { type, ...payload });
    } catch (error) {
      throw asPanelError(error);
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

  createRoom(name: string, type: string): Promise<RoomDetail> {
    return this.call<RoomDetail>(COMMANDS.roomCreate, { name, type });
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

  installModule(
    roomId: string,
    pack: string,
  ): Promise<{ installed: InstalledModule; room: RoomDetail }> {
    return this.call(COMMANDS.moduleInstall, { room_id: roomId, pack });
  }

  uninstallModule(roomId: string, pack: string): Promise<RoomDetail> {
    return this.call<RoomDetail>(COMMANDS.moduleUninstall, {
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
