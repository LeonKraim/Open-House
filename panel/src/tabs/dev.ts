/**
 * The Dev tab: an automation becomes a module, and a module becomes automations.
 *
 * **Why it is a tab and not a script.** Converting between Home Assistant's
 * automations and the engine's modules is a *reading* before it is a
 * translation: a person looks at what a document does, says which entity is
 * which slot, says which number is a setting somebody should be able to change,
 * and says which calls to keep. Every one of those is a judgement about their
 * house, and the only place a judgement about their house can be made is in
 * front of their house.
 *
 * **Home Assistant's own editors do the editing.** The controls here are Home
 * Assistant's controls, not lookalikes: `ha-form` renders the settings and the
 * slot choices through `ha-selector`, which is what the automation and blueprint
 * editors themselves use, so a number input knows about minimums and units and a
 * choice field renders the same way it renders in the editor a person already
 * knows. `ha-code-editor` in YAML mode is the same component the YAML view of an
 * automation uses, so pasting a blueprint and reading the module it became are
 * both done in the editor the source came from.
 *
 * The one thing the panel *does* own is the shape of the plan: which rows exist,
 * what a decision may be, and in what order the sections are met. That shape is
 * `ha_adapter.pack_authoring`'s reading turned inside out -- `dev/read` returns
 * one row per thing found in the document, and this screen renders one control
 * per row. A row the server did not report cannot be decided about here, which is
 * the property that keeps the manifest on disk a restatement of the document the
 * person looked at rather than of a document the screen invented.
 *
 * **Field names are positional, not semantic.** `ha-form` addresses its data by
 * the `name` on a schema item, and an entity's reading key is `input:lux_sensor`.
 * Those become `entity_0`, `value_3` here and are looked back up by index, because
 * a `ha-form` field name is a data path and a colon inside one is a path segment
 * nobody has promised to keep whole.
 */

import { html, nothing, type TemplateResult } from "lit";
import { OpenHouseElement } from "../base.ts";
import "./host-module.ts";
import type {
  DevAnalysis,
  DevEntityRow,
  DevPlan,
  DevReadReply,
  DevSaved,
  DevServiceRow,
  DevSource,
  DevValueRow,
  InstalledModule,
  RoomSummary,
} from "../api/models.ts";

/** Which of the three ways a source arrives. */
type SourceKind = "automation" | "blueprint" | "text";

/**
 * The room an installed module was put in, or `undefined` for an unknown one.
 *
 * `""` is the whole house: `InstalledModule.room_id` spells the house that way
 * and `house` says so in as many words, so the empty string is returned as the
 * found answer rather than folded into "unknown" -- a module installed into the
 * house has a placement, and it is the house.
 *
 * Out here rather than on the element because it is a rule about the data and
 * not about the screen, and a rule about the data is one a test can hold.
 */
export function placementRoom(
  modules: readonly InstalledModule[],
  pack: string,
): string | undefined {
  return modules.find((module) => module.pack === pack)?.room_id;
}

/**
 * The value kinds a behaviour may wait out.
 *
 * A wait is a length of time in seconds (`engine/behaviours/declared.py`, the
 * `for` clause, and `pack_authoring._check_settings_are_read`, which refuses a
 * wait whose default is not a number). A blueprint's booleans and text inputs
 * are value rows like any other, so offering every row here would offer waits
 * the save refuses -- and the refusal would arrive as a message about an option
 * rather than about the field the person chose. The rows that can carry a
 * number are the rows that can be waited for.
 */
const WAITABLE_KINDS = ["duration", "number", "integer"] as const;

/**
 * The unit words that name a length of time, which is what a wait has to be.
 *
 * A blueprint's `number` input may carry any unit at all -- the MarqBarq
 * blueprint this tab is walked with has `%` for its brightness bounds and `K`
 * for its colour temperatures -- and a wait is a number of seconds and nothing
 * else, because the engine reads a behaviour's `for` in seconds and the
 * manifest's own `unit` is a label it never parses. Waiting on "Max Brightness"
 * at 100 would wait a hundred seconds, so those rows are not offered.
 */
const SECONDS_PER_UNIT: Record<string, number> = {
  s: 1,
  sec: 1,
  secs: 1,
  second: 1,
  seconds: 1,
  m: 60,
  min: 60,
  mins: 60,
  minute: 60,
  minutes: 60,
  h: 3600,
  hr: 3600,
  hrs: 3600,
  hour: 3600,
  hours: 3600,
};

/**
 * How many seconds one of a row's own unit words is, or `null` for a unit that
 * is not a length of time at all.
 *
 * No unit is a length of time in the manifest's own unit, because a `duration`
 * *is* a whole number of seconds (`schemas/pack-manifest/1.4.0.json`).
 */
export function secondsPerUnit(unit: string | null): number | null {
  if (unit === null || unit.trim() === "") return 1;
  return SECONDS_PER_UNIT[unit.trim().toLowerCase()] ?? null;
}

/** Whether a value row measures a length of time, and so can be waited for. */
function measuresTime(row: DevValueRow): boolean {
  return (
    (WAITABLE_KINDS as readonly string[]).includes(row.kind) &&
    secondsPerUnit(row.unit) !== null
  );
}

/** A title that names the unit its value used to be in -- "Trigger (minutes)". */
const UNIT_WORDS = /\s*\((?:seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h)\)\s*$/i;

/**
 * The value row as a wait reads it: a whole number of seconds.
 *
 * The engine reads a behaviour's `for` in seconds and the manifest's `unit` is
 * a label it never parses, so a row the source measured in minutes is converted
 * here rather than left for the module to explain -- the number a person sets on
 * this screen is then the number the module carries, instead of one that grows
 * sixtyfold between the two screens. The title loses the unit word it named for
 * the same reason: a control whose number is seconds and whose words are minutes
 * is a control that lies.
 */
export function waitedRow(row: DevValueRow): DevValueRow {
  const factor = secondsPerUnit(row.unit);
  if (factor === null || factor === 1) return row;
  return {
    ...row,
    label: row.label.replace(UNIT_WORDS, "").trim() || row.label,
    unit: "seconds",
    default: typeof row.default === "number" ? row.default * factor : row.default,
    minimum: row.minimum === null ? null : row.minimum * factor,
    maximum: row.maximum === null ? null : row.maximum * factor,
  };
}

/**
 * The value rows a behaviour may be told to wait for, each with its own index.
 *
 * The index comes along because a setting's key is derived from the row *and*
 * its position (`settingKey`), so a list that renumbered what it offered would
 * name a different setting than the plan writes -- the select's value and the
 * manifest's option key would disagree, and the disagreement would be a wait
 * the save refuses.
 */
export function waitableValues(
  values: readonly DevValueRow[],
): { value: DevValueRow; index: number }[] {
  return values
    .map((value, index) => ({ value, index }))
    .filter(({ value }) => measuresTime(value));
}

/** The licenses a pack may declare, as `schemas/pack-manifest` names them. */
const LICENSES = [
  "mit",
  "apache_2_0",
  "public_domain",
  "cc_by_nc_sa",
  "no_licence",
] as const;

/**
 * The triggers a behaviour may declare, as `schemas/behavior-vocabulary` lists
 * them.
 *
 * Spelled here rather than fetched because the vocabulary is frozen and the
 * panel is built against it: a trigger offered here and refused by the schema
 * would be a save that fails on a value the screen suggested, which is worse
 * than a list that is a release behind.
 */
const TRIGGERS = [
  "state",
  "numeric_state",
  "time",
  "time_pattern",
  "sun",
  "event",
  "homeassistant",
  "device",
  "zone",
  "tag",
  "template",
  "calendar",
  "conversation",
  "mqtt",
  "webhook",
] as const;

/**
 * The conditions a behaviour may declare.
 *
 * `template` is listed because the vocabulary has it and a person may need to
 * see it named; `catalog/pack-policy.yaml` forbids it, so a save that keeps one
 * is refused by the sandbox with a reason rather than by this list with silence.
 */
const CONDITIONS = [
  "",
  "state",
  "numeric_state",
  "time",
  "sun",
  "zone",
  "trigger",
  "and",
  "or",
  "not",
  "screen",
  "template",
] as const;

/** One schema item of a `ha-form`, which is what Home Assistant's forms take. */
interface FormItem {
  name: string;
  selector: Record<string, unknown>;
  required?: boolean;
}

/**
 * Which of the tab's two jobs is showing.
 *
 * `module` is the default because it is the one that keeps Home Assistant doing
 * the work: a source is hosted as the automation it already is, and nothing is
 * translated. `pack` is the other job -- turning a source into the engine's own
 * declarative module -- and it stays because a pack is a thing a person may
 * write by hand or derive and keep, not because importing needs it.
 */
type DevJob = "module" | "pack";

export class DevTab extends OpenHouseElement {
  static override properties = {
    ...OpenHouseElement.properties,
    job: { state: true },
    automations: { state: true },
    blueprints: { state: true },
    saved: { state: true },
    rooms: { state: true },
    kind: { state: true },
    automationKey: { state: true },
    blueprintKey: { state: true },
    pasted: { state: true },
    reading: { state: true },
    slotData: { state: true },
    settingData: { state: true },
    meta: { state: true },
    behaviours: { state: true },
    savedYaml: { state: true },
    exportPack: { state: true },
    exportRoom: { state: true },
    exportYaml: { state: true },
    installRoom: { state: true },
    isLoading: { state: true },
    busy: { state: true },
    error: { state: true },
    notice: { state: true },
  };

  private job: DevJob = "module";

  private automations: DevSource[] = [];
  private blueprints: DevSource[] = [];
  private saved: DevSaved[] = [];
  private rooms: RoomSummary[] = [];

  private kind: SourceKind = "automation";
  private automationKey = "";
  private blueprintKey = "";
  private pasted = "";

  private reading: DevReadReply | null = null;

  /**
   * Which document the reading and the decisions above belong to.
   *
   * A short identity -- the kind and the key, or the pasted text -- compared on
   * each read so that the decision state is reset when a *different* source is
   * read and preserved when the same one is read again.
   */
  private readFor = "";

  /** The `ha-form` data for the slot decisions, keyed `entity_<index>`. */
  private slotData: Record<string, string> = {};
  /** The `ha-form` data for the settings, keyed `value_<index>`. */
  private settingData: Record<string, unknown> = {};

  private meta = {
    name: "",
    title: "",
    description: "",
    version: "1.0.0",
    license: "mit" as string,
  };

  /** One decision per service row, keyed by the row's reading key. */
  private behaviours = new Map<string, BehaviourDecision>();

  /**
   * The modules the house currently holds, as the Modules tab draws them.
   *
   * The whole module rather than its name, because the export needs one fact
   * the name does not carry: *where* it was placed. Read once with the sources
   * rather than polled: what may be exported changes when a module is installed
   * or removed, and those are both things this screen did, so it re-reads after
   * each one instead of asking on every render.
   */
  private installed: InstalledModule[] = [];

  /** The names of those modules, which is all most of this screen asks. */
  private get installedNames(): string[] {
    return this.installed.map((module) => module.pack);
  }

  /**
   * Point the export's room at where `pack` actually is.
   *
   * An export resolves the module's slots in the room it is asked about, so
   * asking about the house for a module in the kitchen names no entity and
   * writes an automation that would do nothing -- silently, because a `target`
   * naming nothing is exactly what the export must write when a slot resolves
   * to nothing (`pack_authoring.automation_documents`). Opening on the room the
   * module is in is what makes the export's first answer a real one; a person
   * who wants the house scope picks it, and then the empty target is their
   * answer rather than the screen's default.
   */
  private followPlacement(pack: string): void {
    const room = placementRoom(this.installed, pack);
    if (room !== undefined) this.exportRoom = room;
  }

  private savedYaml = "";
  private exportPack = "";
  private exportRoom = "";
  private exportYaml = "";
  private installRoom = "";

  private isLoading = false;
  private busy = false;
  private error: ReturnType<OpenHouseElement["toError"]> | null = null;
  private notice: string | null = null;

  override connectedCallback(): void {
    super.connectedCallback();
    void this.load();
  }

  // -- loading -------------------------------------------------------------

  private async load(): Promise<void> {
    this.isLoading = true;
    this.error = null;
    try {
      const client = this.requireClient();
      const [sources, rooms, modules] = await Promise.all([
        client.devSources(),
        client.rooms(),
        client.modules(),
      ]);
      this.automations = sources.automations;
      this.blueprints = sources.blueprints;
      this.saved = sources.saved;
      this.rooms = rooms;
      this.installed = modules;
      this.automationKey ||= this.automations[0]?.key ?? "";
      this.blueprintKey ||= this.blueprints[0]?.key ?? "";
      // The first *installed* module, because that is what the export offers:
      // defaulting to a saved-but-uninstalled one selected nothing the select
      // could show and left the button pointing at a module the server refuses.
      this.exportPack ||= this.installedNames[0] ?? "";
      this.followPlacement(this.exportPack);
      this.installRoom ||= this.rooms[0]?.id ?? "";
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.isLoading = false;
      this.requestUpdate();
    }
  }

  private get sourceHandle(): { key?: string; text?: string } {
    if (this.kind === "automation") return { key: this.automationKey };
    if (this.kind === "blueprint") return { key: this.blueprintKey };
    return { text: this.pasted };
  }

  // -- reading -------------------------------------------------------------

  private async read(): Promise<void> {
    this.busy = true;
    this.error = null;
    this.notice = null;
    this.reading = null;
    this.savedYaml = "";
    try {
      const handle = this.sourceHandle;
      // The row keys are derived from the source's own names -- `input:lights`
      // is in a great many blueprints -- so the previous document's decisions
      // would arrive as decisions about this one: a slot already required, a
      // behaviour already waiting for a value this source does not have. Cleared
      // when the *document* changes and kept when it does not, so re-reading the
      // source on screen does not throw away a person's work.
      const identity = `${this.kind}:${handle.key ?? handle.text ?? ""}`;
      if (identity !== this.readFor) {
        this.readFor = identity;
        this.required.clear();
      }
      const reading = await this.requireClient().devRead(this.kind, handle);
      this.reading = reading;
      this.adoptReading(reading.analysis);
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.busy = false;
      this.requestUpdate();
    }
  }

  /**
   * Fill every decision with the reading's own suggestion.
   *
   * A person opens this screen to change two things, not to fill in twenty. So
   * the defaults are the ones the reading would have chosen -- every entity
   * bound to the slot its domain usually means, every value a setting under its
   * own name, every service the engine can perform kept -- and the screen is
   * where those are overridden rather than where they are entered.
   *
   * A service the engine's closed list does not have is *not* kept: it is a call
   * the sandbox would refuse, and defaulting it on would be a save that fails on
   * a decision the screen made.
   */
  private adoptReading(analysis: DevAnalysis): void {
    // A slot is one device, so the second input that wants an already-taken
    // slot is left out rather than bound to it. The Dynamic Lighting blueprint
    // is the case: "Lights to Control" and "Bypass Light" are both lights, both
    // suggest `light_group`, and the catalog has no second role for a light --
    // so the bypass, which the module does not act through, defaults to not
    // being part of the module. Pre-filling a plan the server then refuses would
    // make a person read an error to learn what the form already knew.
    const slots: Record<string, string> = {};
    const taken = new Set<string>();
    for (const [index, row] of analysis.entities.entries()) {
      const wanted = row.suggested_slot;
      const chosen = wanted && !taken.has(wanted) ? wanted : IGNORE;
      if (chosen !== IGNORE) taken.add(chosen);
      slots[`entity_${index}`] = chosen;
    }
    this.slotData = slots;

    const settings: Record<string, unknown> = {};
    for (const [index, row] of analysis.values.entries()) {
      // The row as a wait reads it, so the number this field starts at is the
      // number the module will carry -- a value the source counted in minutes
      // opens on its own default in seconds, not on a fifth of it.
      settings[`value_${index}`] = defaultFor(waitedRow(row));
    }
    this.settingData = settings;

    const names = behaviourNames(analysis.services);
    this.behaviours = new Map(
      analysis.services.map((row, index) => [
        row.key,
        {
          key: row.key,
          keep: row.supported === true,
          name: names[index] ?? "behaviour",
          slot: this.slotForEntityKey(row.acts_on),
          watched: [],
          trigger: firstTrigger(analysis.triggers),
          condition: "",
          for: "",
          scope: "room",
          priority: 10,
        },
      ]),
    );

    this.meta = {
      name: packName(analysis.title),
      title: analysis.title,
      description: analysis.description,
      version: "1.0.0",
      license: "mit",
    };
  }

  /** The slot a behaviour's target resolved to, from the current decisions. */
  private slotForEntityKey(keys: string[]): string {
    const entities = this.reading?.analysis.entities ?? [];
    for (const key of keys) {
      const index = entities.findIndex((row) => row.key === key);
      if (index < 0) continue;
      const chosen = this.slotData[`entity_${index}`];
      if (chosen && chosen !== IGNORE) return chosen;
    }
    return "";
  }

  /** The slots a person has bound, in the reading's order. */
  private get boundSlots(): string[] {
    const entities = this.reading?.analysis.entities ?? [];
    const found: string[] = [];
    for (const [index] of entities.entries()) {
      const chosen = this.slotData[`entity_${index}`];
      if (chosen && chosen !== IGNORE && !found.includes(chosen)) found.push(chosen);
    }
    return found;
  }

  // -- the plan ------------------------------------------------------------

  private get plan(): DevPlan {
    const analysis = this.reading?.analysis;
    const entities: DevPlan["entities"] = {};
    const values: DevPlan["values"] = {};

    for (const [index, row] of (analysis?.entities ?? []).entries()) {
      const chosen = this.slotData[`entity_${index}`] ?? IGNORE;
      entities[row.key] =
        chosen === IGNORE
          ? { decision: "ignore" }
          : {
              decision: "slot",
              slot: chosen,
              required: row.optional ? this.required.has(row.key) : true,
            };
    }

    const held = this.heldSettings();
    for (const [index, row] of (analysis?.values ?? []).entries()) {
      // A value a behaviour waits on is a setting; every other value is not part
      // of the module at all, and saying so is the honest answer -- the engine
      // reads a `duration` through a behaviour's `for` and nothing else, so a
      // declared option nothing waits on is a control a person can move with no
      // effect anywhere (`schemas/pack-manifest/1.4.0.json`, `options`).
      if (!held.has(settingKey(row, index))) {
        values[row.key] = { decision: "ignore" };
        continue;
      }
      // What the behaviour waits for is a number of seconds, so the setting is
      // written as the wait reads it -- the same row the control in section 4
      // drew, unit and bounds and all.
      const waited = waitedRow(row);
      values[row.key] = {
        decision: "setting",
        key: settingKey(row, index),
        type: optionType(row),
        title: waited.label,
        description: waited.description,
        default: this.settingData[`value_${index}`],
        minimum: waited.minimum,
        maximum: waited.maximum,
        unit: waited.unit,
        enum: row.choices.length > 0 ? row.choices : undefined,
      };
    }

    return {
      name: this.meta.name,
      title: this.meta.title,
      description: this.meta.description,
      version: this.meta.version,
      license: this.meta.license,
      entities,
      values,
      behaviours: [...this.behaviours.values()].map((decision) => ({
        key: decision.key,
        keep: decision.keep,
        name: decision.name,
        slot: decision.slot,
        watched: decision.watched,
        trigger: decision.trigger,
        condition: decision.condition,
        for: decision.for,
        scope: decision.scope,
        priority: decision.priority,
      })),
    };
  }

  // -- writing -------------------------------------------------------------

  private async save(into: string | null): Promise<void> {
    this.busy = true;
    this.error = null;
    this.notice = null;
    try {
      const client = this.requireClient();
      const reply = await client.devSave(
        this.kind,
        this.sourceHandle,
        this.plan,
        into === null ? null : { into },
      );
      this.savedYaml = reply.saved.yaml;
      // `into` is `null` for "Save module" and a room id -- or the empty string
      // for the whole house -- for "Save and install there". The empty string is
      // the house and is a *destination*, so the test is against `null` and not
      // against truth: the house branch used to report a plain save, which is a
      // notice that denies the thing the person just watched happen.
      this.notice =
        into === null
          ? `Saved ${reply.saved.file}.`
          : `Saved ${reply.saved.file} and installed it.`;
      const sources = await client.devSources();
      this.saved = sources.saved;
      this.installed = reply.modules;
      // Adopted only if it actually went into the house: the export offers
      // installed modules, so selecting a saved-but-uninstalled one would leave
      // the select showing nothing and the button answering "not installed".
      if (this.installedNames.includes(reply.saved.name)) {
        this.exportPack ||= reply.saved.name;
        this.followPlacement(this.exportPack);
      }
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.busy = false;
      this.requestUpdate();
    }
  }

  private async install(name: string): Promise<void> {
    this.busy = true;
    this.error = null;
    this.notice = null;
    try {
      // The house is `""`, so the destination is passed as it stands: `||
      // undefined` turned "the whole house" into "no room named", which the
      // server places by the entity join instead.
      const reply = await this.requireClient().devInstall(name, this.installRoom);
      this.installed = reply.modules;
      this.notice = `Installed ${name}.`;
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.busy = false;
      this.requestUpdate();
    }
  }

  private async exportModule(): Promise<void> {
    if (!this.exportPack) return;
    this.busy = true;
    this.error = null;
    this.notice = null;
    this.exportYaml = "";
    try {
      // Again the house is `""`: passed through rather than collapsed to
      // `undefined`, so the room the select shows is the room asked for.
      const reply = await this.requireClient().devExport(
        this.exportPack,
        this.exportRoom,
      );
      this.exportYaml = reply.yaml;
      // Said out loud, because the YAML cannot say it: a role this room fills
      // nothing for is written as a target naming nothing -- deliberately, so
      // the automation does not become "every light in the house" -- and the
      // result is correct, complete, and would do nothing.
      this.notice =
        reply.unresolved.length === 0
          ? null
          : `Exported. Nothing in ${this.roomLabel(this.exportRoom)} fills ` +
            `${reply.unresolved.join(", ")}, so the automations that act ` +
            "through it name no entity and would run without changing " +
            "anything. Export the module from the room it is in, or bind " +
            "that role.";
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.busy = false;
      this.requestUpdate();
    }
  }

  /**
   * How a room id reads in a sentence.
   *
   * `""` is the house's own spelling everywhere in this API, and a notice
   * saying "Nothing in  fills light_group" is a notice nobody can read.
   */
  private roomLabel(roomId: string): string {
    if (roomId === "") return "the whole house";
    return this.rooms.find((room) => room.id === roomId)?.name ?? roomId;
  }

  private copyExport(): void {
    void navigator.clipboard?.writeText(this.exportYaml);
    this.notice = "Copied the automations to the clipboard.";
    this.requestUpdate();
  }

  private downloadExport(): void {
    const blob = new Blob([this.exportYaml], { type: "text/yaml" });
    const url = URL.createObjectURL(blob);
    const anchor = globalThis.document.createElement("a");
    anchor.href = url;
    anchor.download = `${this.exportPack || "open-house"}-automations.yaml`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  // -- decisions held beside the forms -------------------------------------

  /** Entity rows a person has marked required, for an optional input. */
  private required = new Set<string>();

  /**
   * The settings a kept behaviour waits out.
   *
   * This is the whole of what makes a value a setting, and it is why section 4
   * is drawn from section 5's answers rather than beside them: the engine's
   * declarative interpreter reads a number in exactly one way -- a `duration` a
   * behaviour's `for` names -- so a value nothing waits on cannot be a control
   * that does anything, and the module leaves it out instead of offering it.
   */
  private heldSettings(): Set<string> {
    const held = new Set<string>();
    for (const decision of this.behaviours.values()) {
      if (decision.keep && decision.for) held.add(decision.for);
    }
    return held;
  }


  // -- render --------------------------------------------------------------

  override render(): TemplateResult {
    return html`<div class="layout">
      <h1>Dev</h1>
      <p class="muted">
        Import a blueprint or an automation as a module the house runs, and turn
        a module back into automations.
      </p>
      ${this.renderJob()}
      ${this.job === "module"
        ? html`<open-house-host-module
            .hass=${this.hass}
            .client=${this.client}
            .admin=${this.admin}
          ></open-house-host-module>`
        : html`${this.errorBanner(this.error)}
            ${this.notice
              ? html`<div class="banner info">${this.notice}</div>`
              : null}
            ${this.isLoading
              ? this.loading("Reading your automations...")
              : nothing}
            ${this.renderImport()}
            ${this.reading ? this.renderWorkbench() : nothing}
            ${this.renderExport()}`}
    </div>`;
  }

  /**
   * The tab's two jobs, named as the two things a person may want.
   *
   * A module first, because that is the import: the source keeps being the
   * automation it is and Home Assistant runs it. A pack second, and it is a
   * *different* act rather than a better or worse one -- a pack is this engine's
   * own declarative module, written or derived, and the workbench beneath this
   * is where its rows are decided.
   */
  private renderJob(): TemplateResult {
    return html`<div class="tabs">
      ${(
        [
          ["module", "Import as a module"],
          ["pack", "Author a pack"],
        ] as const
      ).map(
        ([job, label]) => html`<button
          type="button"
          class="tab"
          aria-selected=${this.job === job ? "true" : "false"}
          @click=${() => {
            this.job = job;
          }}
        >
          ${label}
        </button>`,
      )}
    </div>`;
  }

  private renderImport(): TemplateResult {
    return html`<section class="card">
      <h2>1. Choose a source</h2>
      <div class="row">
        ${(["automation", "blueprint", "text"] as const).map(
          (kind) => html`<button
            type="button"
            class="tab"
            aria-selected=${this.kind === kind ? "true" : "false"}
            @click=${() => {
              this.kind = kind;
              this.reading = null;
            }}
          >
            ${kind === "text" ? "Paste YAML" : capitalise(kind)}
          </button>`,
        )}
      </div>
      ${this.kind === "automation" ? this.renderAutomationPicker() : nothing}
      ${this.kind === "blueprint" ? this.renderBlueprintPicker() : nothing}
      ${this.kind === "text" ? this.renderPasteBox() : nothing}
      <div class="row">
        <button
          type="button"
          @click=${() => void this.read()}
          ?disabled=${this.busy || !this.canRead}
        >
          ${this.busy ? "Reading..." : "Read this source"}
        </button>
      </div>
    </section>`;
  }

  private get canRead(): boolean {
    if (this.kind === "automation") return Boolean(this.automationKey);
    if (this.kind === "blueprint") return Boolean(this.blueprintKey);
    return this.pasted.trim().length > 0;
  }

  private renderAutomationPicker(): TemplateResult {
    if (this.automations.length === 0) {
      return html`<p class="muted">
        This instance has no automations. Make one in Home Assistant, or paste
        YAML, and it will be here.
      </p>`;
    }
    return html`<div class="field">
      <label class="label" for="dev-automation">Automation</label>
      <select
        id="dev-automation"
        .value=${this.automationKey}
        @change=${(event: Event) => {
          this.automationKey = (event.target as HTMLSelectElement).value;
        }}
      >
        ${this.automations.map(
          (row) =>
            html`<option value=${row.key} ?selected=${row.key === this.automationKey}>
              ${row.name}
            </option>`,
        )}
      </select>
      <p class="help">
        ${this.automations.find((row) => row.key === this.automationKey)
          ?.description || ""}
      </p>
    </div>`;
  }

  private renderBlueprintPicker(): TemplateResult {
    if (this.blueprints.length === 0) {
      return html`<p class="muted">
        This instance has no automation blueprints. Import one in Home
        Assistant, then come back.
      </p>`;
    }
    const chosen = this.blueprints.find((row) => row.key === this.blueprintKey);
    return html`<div class="field">
      <label class="label" for="dev-blueprint">Blueprint</label>
      <select
        id="dev-blueprint"
        .value=${this.blueprintKey}
        @change=${(event: Event) => {
          this.blueprintKey = (event.target as HTMLSelectElement).value;
        }}
      >
        ${this.blueprints.map(
          (row) =>
            html`<option value=${row.key} ?selected=${row.key === this.blueprintKey}>
              ${row.name}
            </option>`,
        )}
      </select>
      <p class="help">${chosen?.description ?? ""}</p>
      ${chosen?.source_url
        ? html`<p class="help">From ${chosen.source_url}</p>`
        : null}
    </div>`;
  }

  private renderPasteBox(): TemplateResult {
    return html`<div class="field">
      <span class="label">Paste an automation or a blueprint</span>
      <ha-code-editor
        .hass=${this.hass}
        .value=${this.pasted}
        .mode=${"yaml"}
        @value-changed=${(event: CustomEvent<{ value: string }>) => {
          this.pasted = event.detail.value ?? "";
        }}
      ></ha-code-editor>
      <p class="help">
        Whatever Home Assistant accepts: the YAML of an automation, or a whole
        blueprint file.
      </p>
    </div>`;
  }

  private renderWorkbench(): TemplateResult {
    const analysis = this.reading!.analysis;
    return html`
      ${analysis.dropped.length > 0
        ? html`<div class="banner warn">
            <strong>Some of this source cannot go into a pack.</strong>
            <ul>
              ${analysis.dropped.map((line) => html`<li>${line}</li>`)}
            </ul>
            <p class="help">
              A pack declares a policy, not a program: it may not branch, compute
              or wait. Everything else still goes in, and the parts listed above
              are simply not part of the pack.
            </p>
            <p class="help">
              None of this is lost: choose <em>Import as a module</em> above and
              the whole source is hosted as it is, every branch and wait intact.
            </p>
          </div>`
        : nothing}
      ${this.renderMeta()}
      ${analysis.entities.length > 0 ? this.renderSlots() : nothing}
      ${analysis.values.length > 0 ? this.renderSettings() : nothing}
      ${this.renderBehaviours()}
      ${this.renderSave()}
    `;
  }

  private renderMeta(): TemplateResult {
    return html`<section class="card">
      <h2>2. Name the module</h2>
      <div class="grid">
        ${this.textField("Name", this.meta.name, (value) => {
          this.meta = { ...this.meta, name: value };
        }, "The pack's own name, lower case with underscores.")}
        ${this.textField("Title", this.meta.title, (value) => {
          this.meta = { ...this.meta, title: value };
        }, "What a person sees in the module list.")}
      </div>
      ${this.textField("Description", this.meta.description, (value) => {
        this.meta = { ...this.meta, description: value };
      })}
      <div class="grid">
        ${this.textField("Version", this.meta.version, (value) => {
          this.meta = { ...this.meta, version: value };
        }, "Three numbers, as in 1.0.0.")}
        <div class="field">
          <label class="label" for="dev-license">License</label>
          <select
            id="dev-license"
            .value=${this.meta.license}
            @change=${(event: Event) => {
              this.meta = {
                ...this.meta,
                license: (event.target as HTMLSelectElement).value,
              };
            }}
          >
            ${LICENSES.map(
              (license) =>
                html`<option value=${license} ?selected=${license === this.meta.license}>
                  ${license}
                </option>`,
            )}
          </select>
        </div>
      </div>
    </section>`;
  }

  private textField(
    label: string,
    value: string,
    onInput: (value: string) => void,
    help = "",
  ): TemplateResult {
    return html`<div class="field">
      <label class="label">${label}</label>
      <input
        type="text"
        .value=${value}
        @input=${(event: Event) => {
          onInput((event.target as HTMLInputElement).value);
          this.requestUpdate();
        }}
      />
      ${help ? html`<p class="help">${help}</p>` : null}
    </div>`;
  }

  /**
   * The slot decisions, drawn by Home Assistant's own form.
   *
   * One `select` per entity the source names, offering every slot the catalog
   * declares plus "leave it out" -- because the entity a person has no slot for
   * (a weather entity, a bypass switch) is a real answer and not an omission.
   * `ha-form` renders it with `ha-selector`, the same control the automation
   * editor uses for a choice field.
   */
  private renderSlots(): TemplateResult {
    const rows = this.reading!.analysis.entities;
    const schema: FormItem[] = rows.map((row, index) => ({
      name: `entity_${index}`,
      selector: {
        select: {
          mode: "dropdown",
          options: [
            { value: IGNORE, label: `Leave ${row.label} out` },
            ...this.reading!.slots.map((slot) => ({
              value: slot.name,
              label: `${slot.name} (${slot.domains.join(", ")})`,
            })),
          ],
        },
      },
    }));
    return html`<section class="card">
      <h2>3. Say what each device is</h2>
      <p class="muted">
        A slot is a role: "the lights", "the motion sensor". The module asks the
        room for the device, and every room may answer with its own.
      </p>
      ${this.renderCollisions()}
      <ha-form
        .hass=${this.hass}
        .data=${this.slotData}
        .schema=${schema}
        .computeLabel=${(item: FormItem) => this.labelForEntity(item.name)}
        @value-changed=${(event: CustomEvent<{ value: Record<string, string> }>) => {
          this.slotData = { ...event.detail.value };
          this.refreshBehaviourSlots();
        }}
      ></ha-form>
      <div class="checks">
        ${rows.map((row, index) =>
          this.optionalToggle(row, `entity_${index}`),
        )}
      </div>
    </section>`;
  }

  /**
   * The slots two rows were both pointed at, named before a save is attempted.
   *
   * The server refuses this plan -- "a slot is one device, and a pack that named
   * it twice would act on one of them" -- and it is right to. Showing it here as
   * well is the difference between a person being told what is wrong while they
   * are looking at the control that is wrong, and being told after they press a
   * button three sections further down.
   */
  private renderCollisions(): TemplateResult {
    const counts = new Map<string, string[]>();
    const entities = this.reading?.analysis.entities ?? [];
    for (const [index, row] of entities.entries()) {
      const chosen = this.slotData[`entity_${index}`];
      if (!chosen || chosen === IGNORE) continue;
      counts.set(chosen, [...(counts.get(chosen) ?? []), row.label]);
    }
    const clashes = [...counts.entries()].filter(([, rows]) => rows.length > 1);
    if (clashes.length === 0) return html``;
    return html`<div class="banner warn">
      ${clashes.map(
        ([slot, rows]) =>
          html`<p>
            <strong>${slot}</strong> is one device, and ${rows.length} rows are
            pointed at it: ${rows.join(", ")}. Give one of them another slot, or
            leave it out of the module.
          </p>`,
      )}
    </div>`;
  }

  private labelForEntity(name: string): string {
    const index = Number(name.replace("entity_", ""));
    const row = this.reading?.analysis.entities[index];
    if (!row) return name;
    const places = row.count > 1 ? ` (named ${row.count} times)` : "";
    return `${row.label}${places}`;
  }

  /**
   * The required/optional toggle, and a real checkbox.
   *
   * A blueprint's optional input is the one place "required" is a person's
   * choice: an input with a default may be left unbound, and the engine will
   * skip the behaviour that reads it rather than refusing the install. A
   * checkbox rather than a switch because it is a decision about a row in a
   * table, and a table of switches reads as a page of settings.
   */
  private optionalToggle(row: DevEntityRow, name: string): TemplateResult {
    const chosen = this.slotData[name] ?? IGNORE;
    if (chosen === IGNORE || !row.optional) return html``;
    const isRequired = this.required.has(row.key);
    return html`<label class="check">
      <input
        type="checkbox"
        .checked=${isRequired}
        @change=${(event: Event) => {
          const on = (event.target as HTMLInputElement).checked;
          if (on) this.required.add(row.key);
          else this.required.delete(row.key);
          this.requestUpdate();
        }}
      />
      ${row.label} is required
    </label>`;
  }

  /**
   * The settings, drawn by Home Assistant's own form.
   *
   * A blueprint's inputs are already the shape of a pack's options -- a number
   * with a minimum and a unit, a switch, a time, a choice -- so each renders
   * through the same `ha-selector` an automation's own editor would use for it.
   *
   * **Only the settings a behaviour waits out.** The engine's declarative
   * interpreter reads a number in one way and no other: a `duration` a
   * behaviour's `for` names, which turns "the door is open" into "the door has
   * been open this long". A blueprint's other numbers -- a brightness, a colour
   * temperature, a lux threshold -- shape a template the engine does not
   * evaluate, so a control for one would be a control that does nothing, which
   * is the defect the manifest schema's own `options` description warns against.
   * They are listed below and left out of the module, and section 5 is where a
   * person says which value a behaviour waits for.
   */
  private renderSettings(): TemplateResult {
    const rows = this.reading!.analysis.values;
    const held = this.heldSettings();
    const schema: FormItem[] = [];
    const settings = new Set<string>();
    rows.forEach((row, index) => {
      if (!held.has(settingKey(row, index))) return;
      settings.add(row.key);
      // Drawn from the row a wait reads, so this control shows the unit and
      // the bounds the module will have: minutes become seconds here.
      schema.push({ name: `value_${index}`, selector: selectorFor(waitedRow(row)) });
    });
    const rest = rows.filter((row) => !settings.has(row.key));
    const notTime = rest.filter(
      (row) => row.unit !== null && !measuresTime(row),
    );
    return html`<section class="card">
      <h2>4. Say what a person may change</h2>
      <p class="muted">
        A setting is a length of time a behaviour waits out -- the engine's one
        way to read a number from a module. Give a behaviour something to wait
        for in section 5 and its field appears here.
      </p>
      ${schema.length > 0
        ? html`<ha-form
            .hass=${this.hass}
            .data=${this.settingData}
            .schema=${schema}
            .computeLabel=${(item: FormItem) => this.labelForValue(item.name)}
            @value-changed=${(
              event: CustomEvent<{ value: Record<string, unknown> }>,
            ) => {
              this.settingData = { ...event.detail.value };
            }}
          ></ha-form>`
        : html`<p class="muted">
            No behaviour waits for anything yet, so this module has no settings.
          </p>`}
      ${rest.length > 0
        ? html`<p class="help">
            ${rest.length} input${rest.length === 1 ? "" : "s"} of this source
            (${rest.map((row) => row.label).join(", ")})
            ${rest.length === 1 ? "is" : "are"} not part of the module: the engine
            acts on a duration a behaviour waits out and on nothing else, so a
            control for ${rest.length === 1 ? "it" : "them"} would be a control
            that does nothing.
          </p>`
        : nothing}
      ${notTime.length > 0
        ? html`<p class="help">
            ${notTime.map((row) => row.label).join(", ")}
            ${notTime.length === 1 ? "measures" : "measure"} something that is not
            a length of time, so ${notTime.length === 1 ? "it" : "they"} cannot
            be waited for -- a wait is a whole number of seconds and nothing else.
          </p>`
        : nothing}
    </section>`;
  }

  private labelForValue(name: string): string {
    const index = Number(name.replace("value_", ""));
    const row = this.reading?.analysis.values[index];
    return row ? waitedRow(row).label : name;
  }

  /**
   * The service calls, one row each, with the five decisions a behaviour needs.
   *
   * A call the engine cannot perform is shown and not kept, with the reason
   * beside it, because the alternative -- hiding it -- would make a module that
   * does less than the automation did while reading as a faithful conversion.
   */
  private renderBehaviours(): TemplateResult {
    const rows = this.reading!.analysis.services;
    if (rows.length === 0) {
      return html`<section class="card">
        <h2>5. Behaviours</h2>
        <p class="muted">
          This source calls no services, so there is nothing for the module to
          do. A module with no behaviour cannot be saved.
        </p>
      </section>`;
    }
    const slots = this.boundSlots;
    return html`<section class="card">
      <h2>5. Keep the behaviours</h2>
      <p class="muted">
        Each kept call becomes one behaviour: the module watches the slots you
        list first and acts through the last.
      </p>
      ${rows.map((row) => this.renderBehaviour(row, slots))}
    </section>`;
  }

  private renderBehaviour(
    row: DevServiceRow,
    slots: string[],
  ): TemplateResult {
    const decision = this.behaviours.get(row.key);
    if (!decision) return html``;
    const patch = (change: Partial<BehaviourDecision>) => {
      this.behaviours.set(row.key, { ...decision, ...change });
      this.requestUpdate();
    };
    const unsupported = row.supported === false;
    return html`<fieldset class="nested">
      <legend>
        ${row.service}
        ${unsupported ? html`<span class="chip warn">not in the engine</span>` : nothing}
      </legend>
      <div class="row wrap">
        <label class="check">
          <input
            type="checkbox"
            .checked=${decision.keep}
            ?disabled=${unsupported}
            @change=${(event: Event) =>
              patch({ keep: (event.target as HTMLInputElement).checked })}
          />
          keep this call
        </label>
        <span class="muted small">found at ${row.where}</span>
      </div>
      ${unsupported
        ? html`<p class="help warn">
            The engine's service list has no ${row.service}, so a pack may not
            call it. It is shown so you can see what your automation does; it
            cannot be part of the module.
          </p>`
        : nothing}
      ${decision.keep
        ? html`
            <div class="grid">
              <div class="field">
                <label class="label">Behaviour name</label>
                <input
                  type="text"
                  .value=${decision.name}
                  @input=${(event: Event) =>
                    patch({ name: (event.target as HTMLInputElement).value })}
                />
              </div>
              <div class="field">
                <label class="label">Acts on</label>
                <select
                  .value=${decision.slot}
                  @change=${(event: Event) =>
                    patch({ slot: (event.target as HTMLSelectElement).value })}
                >
                  <option value="">choose a slot</option>
                  ${slots.map(
                    (slot) =>
                      html`<option value=${slot} ?selected=${slot === decision.slot}>
                        ${slot}
                      </option>`,
                  )}
                </select>
              </div>
              <div class="field">
                <label class="label">Trigger</label>
                <select
                  .value=${decision.trigger}
                  @change=${(event: Event) =>
                    patch({ trigger: (event.target as HTMLSelectElement).value })}
                >
                  ${TRIGGERS.map(
                    (trigger) =>
                      html`<option
                        value=${trigger}
                        ?selected=${trigger === decision.trigger}
                      >
                        ${trigger}
                      </option>`,
                  )}
                </select>
              </div>
              <div class="field">
                <label class="label">Condition</label>
                <select
                  .value=${decision.condition}
                  @change=${(event: Event) =>
                    patch({ condition: (event.target as HTMLSelectElement).value })}
                >
                  ${CONDITIONS.map(
                    (condition) =>
                      html`<option
                        value=${condition}
                        ?selected=${condition === decision.condition}
                      >
                        ${condition || "none"}
                      </option>`,
                  )}
                </select>
              </div>
              <div class="field">
                <label class="label">Wait for</label>
                <select
                  .value=${decision.for}
                  @change=${(event: Event) =>
                    patch({ for: (event.target as HTMLSelectElement).value })}
                >
                  <option value="" ?selected=${decision.for === ""}>
                    act immediately
                  </option>
                  ${waitableValues(this.reading?.analysis.values ?? []).map(
                    ({ value, index }) => {
                      const key = settingKey(value, index);
                      return html`<option value=${key} ?selected=${key === decision.for}>
                        ${value.label}
                      </option>`;
                    },
                  )}
                </select>
                <p class="help">
                  Choosing one makes it a setting: the behaviour acts only once
                  the slot it watches has read something for that long. Only the
                  values that measure a length of time are offered -- a value in
                  minutes is offered and its setting is written in seconds, and
                  a value in percent or kelvin is not offered at all, because
                  waiting for a hundred of those would wait a hundred seconds.
                </p>
              </div>
              <div class="field">
                <label class="label">Scope</label>
                <select
                  .value=${decision.scope}
                  @change=${(event: Event) =>
                    patch({
                      scope: (event.target as HTMLSelectElement)
                        .value as "room" | "house",
                    })}
                >
                  <option value="room" ?selected=${decision.scope === "room"}>
                    this room
                  </option>
                  <option value="house" ?selected=${decision.scope === "house"}>
                    the whole house
                  </option>
                </select>
              </div>
              <div class="field">
                <label class="label">Priority</label>
                <input
                  type="number"
                  .value=${String(decision.priority)}
                  @input=${(event: Event) =>
                    patch({
                      priority: Number((event.target as HTMLInputElement).value) || 0,
                    })}
                />
              </div>
            </div>
            <div class="field">
              <span class="label">Watch these slots first</span>
              <div class="checks">
                ${slots
                  .filter((slot) => slot !== decision.slot)
                  .map(
                    (slot) => html`<label class="check">
                      <input
                        type="checkbox"
                        .checked=${decision.watched.includes(slot)}
                        @change=${(event: Event) => {
                          const on = (event.target as HTMLInputElement).checked;
                          patch({
                            watched: on
                              ? [...decision.watched, slot]
                              : decision.watched.filter((entry) => entry !== slot),
                          });
                        }}
                      />
                      ${slot}
                    </label>`,
                  )}
              </div>
              <p class="help">
                The behaviour reads these and writes through
                ${decision.slot || "the slot it acts on"}.
              </p>
            </div>
          `
        : nothing}
    </fieldset>`;
  }

  private refreshBehaviourSlots(): void {
    // A slot a person re-pointed takes its behaviours with it, unless they had
    // chosen a slot of their own: the acted-on slot is only defaulted, and a
    // default that stopped following the decisions it came from would be a
    // behaviour left pointing at a role nothing is bound to.
    for (const [key, decision] of this.behaviours) {
      if (decision.slot && this.boundSlots.includes(decision.slot)) continue;
      const row = this.reading?.analysis.services.find((entry) => entry.key === key);
      if (!row) continue;
      this.behaviours.set(key, {
        ...decision,
        slot: this.slotForEntityKey(row.acts_on),
      });
    }
    this.requestUpdate();
  }

  private renderSave(): TemplateResult {
    const kept = [...this.behaviours.values()].filter((entry) => entry.keep);
    return html`<section class="card">
      <h2>6. Save the module</h2>
      <p class="muted">
        ${kept.length} behaviour${kept.length === 1 ? "" : "s"} will be written,
        as <code>config/open_house/packs/${this.meta.name || "module"}.yaml</code>.
      </p>
      <div class="row wrap">
        <button
          type="button"
          @click=${() => void this.save(null)}
          ?disabled=${this.busy || kept.length === 0 || !this.meta.name}
        >
          ${this.busy ? "Saving..." : "Save module"}
        </button>
        <select
          .value=${this.installRoom}
          @change=${(event: Event) => {
            this.installRoom = (event.target as HTMLSelectElement).value;
          }}
        >
          <option value="" ?selected=${this.installRoom === ""}>
            the whole house
          </option>
          ${this.rooms.map(
            (room) =>
              html`<option value=${room.id} ?selected=${room.id === this.installRoom}>
                ${room.name}
              </option>`,
          )}
        </select>
        <button
          type="button"
          @click=${() => void this.save(this.installRoom)}
          ?disabled=${this.busy || kept.length === 0 || !this.meta.name}
        >
          Save and install there
        </button>
      </div>
      ${this.savedYaml
        ? html`<div class="field">
            <span class="label">The module that was written</span>
            <ha-code-editor
              .hass=${this.hass}
              .value=${this.savedYaml}
              .mode=${"yaml"}
              .readOnly=${true}
            ></ha-code-editor>
          </div>`
        : nothing}
    </section>`;
  }

  private renderExport(): TemplateResult {
    // Only *installed* modules: an export resolves a module's slots against a
    // room's bindings and the server refuses a module it has no placement for.
    // Offering a merely-saved module here offered an entry whose button always
    // answered "no module called X is installed".
    const installed = this.installedNames;
    const uninstalled = this.saved.filter(
      (row) => !installed.includes(row.name),
    ).length;
    return html`<section class="card">
      <h2>7. Export a module as automations</h2>
      <p class="muted">
        The other direction: an installed module's behaviours, written as
        automations Home Assistant runs on its own. The slots are resolved to the
        entities the room actually holds, so the room opens on the one the module
        is in; pick another and the export is written for it instead.
      </p>
      ${installed.length === 0
        ? html`<p class="muted">
            There are no modules to export yet.${uninstalled > 0
              ? html` Install one of the modules you saved below and it will be
                  here.`
              : nothing}
          </p>`
        : html`<div class="row wrap">
            <select
              .value=${this.exportPack}
              @change=${(event: Event) => {
                this.exportPack = (event.target as HTMLSelectElement).value;
                this.followPlacement(this.exportPack);
              }}
            >
              ${installed.map(
                (name) =>
                  html`<option value=${name} ?selected=${name === this.exportPack}>
                    ${name}
                  </option>`,
              )}
            </select>
            <select
              .value=${this.exportRoom}
              @change=${(event: Event) => {
                this.exportRoom = (event.target as HTMLSelectElement).value;
              }}
            >
              <option value="" ?selected=${this.exportRoom === ""}>
                the whole house
              </option>
              ${this.rooms.map(
                (room) =>
                  html`<option value=${room.id} ?selected=${room.id === this.exportRoom}>
                    ${room.name}
                  </option>`,
              )}
            </select>
            <button type="button" @click=${() => void this.exportModule()} ?disabled=${this.busy}>
              Export as automations
            </button>
          </div>`}
      ${this.exportYaml
        ? html`<div class="field">
            <div class="row">
              <button type="button" @click=${() => this.copyExport()}>
                Copy
              </button>
              <button type="button" @click=${() => this.downloadExport()}>
                Download
              </button>
            </div>
            <ha-code-editor
              .hass=${this.hass}
              .value=${this.exportYaml}
              .mode=${"yaml"}
              .readOnly=${true}
            ></ha-code-editor>
          </div>`
        : nothing}
      ${this.renderSaved()}
    </section>`;
  }

  /**
   * The modules a person has authored, with an install button each.
   *
   * Kept beside the export rather than in its own section because it answers the
   * same question the export does -- "what have I made" -- and a screen with one
   * more heading than it needs is a screen a person scrolls past.
   *
   * **The button says which of the two things it will do.** A module in this
   * house unlike one that is not, and the label is the only place the difference
   * is visible: "Save module" writes a file and installs nothing, so a person who
   * used it and then read "Install" on their own module would be right to think
   * the save had failed. Reinstalling is kept available rather than hidden --
   * editing the file and putting the edit into the house is the whole point of
   * the authoring loop.
   */
  private renderSaved(): TemplateResult {
    if (this.saved.length === 0) return html``;
    return html`<div class="list">
      ${this.saved.map((row) => {
        const installed = this.installedNames.includes(row.name);
        return html`<div class="list-row">
          <div>
            <strong>${row.title}</strong>
            <p class="muted small">
              ${row.name} ${row.version} -- ${row.behaviours} behaviour${row.behaviours === 1 ? "" : "s"},
              ${row.options} setting${row.options === 1 ? "" : "s"} -- ${row.file}
            </p>
          </div>
          <button
            type="button"
            @click=${() => void this.install(row.name)}
            ?disabled=${this.busy}
          >
            ${installed ? "Reinstall" : "Install"}
          </button>
        </div>`;
      })}
    </div>`;
  }

}

/** The sentinel a slot dropdown uses for "this entity is not part of the module". */
const IGNORE = "__ignore__";

/** One service row's decisions, held between renders. */
interface BehaviourDecision {
  key: string;
  keep: boolean;
  name: string;
  slot: string;
  watched: string[];
  trigger: string;
  condition: string;
  /** The value row this behaviour waits out, as a setting key, or "". */
  for: string;
  scope: "room" | "house";
  priority: number;
}

/**
 * The `ha-selector` for one value row, which is what makes the settings form
 * Home Assistant's own.
 *
 * The row's kind came from the source's own selector -- a blueprint's `number`
 * input with its minimum and its unit, its `time`, its `boolean` -- so this is
 * the same control rendering the same bounds, one screen over from where the
 * blueprint's author declared them.
 */
function selectorFor(row: DevValueRow): Record<string, unknown> {
  if (row.choices.length > 0) {
    return {
      select: {
        mode: "dropdown",
        options: row.choices.map((choice) => ({ value: choice, label: choice })),
      },
    };
  }
  if (row.kind === "boolean") return { boolean: {} };
  if (row.kind === "time") return { time: {} };
  if (row.kind === "integer") {
    return { number: { ...bounds(row), step: 1, mode: "box" } };
  }
  if (row.kind === "number") {
    return { number: { ...bounds(row), step: "any", mode: "box" } };
  }
  if (row.kind === "duration") {
    return {
      number: { ...bounds(row), step: 1, mode: "box", unit_of_measurement: "s" },
    };
  }
  return { text: {} };
}

function bounds(row: DevValueRow): Record<string, unknown> {
  return {
    ...(row.minimum === null ? {} : { min: row.minimum }),
    ...(row.maximum === null ? {} : { max: row.maximum }),
    ...(row.unit === null ? {} : { unit_of_measurement: row.unit }),
  };
}

/** The value a setting's control starts at. */
function defaultFor(row: DevValueRow): unknown {
  if (row.default !== null && row.default !== undefined) return row.default;
  if (row.kind === "boolean") return false;
  if (row.kind === "integer" || row.kind === "number") return row.minimum ?? 0;
  if (row.kind === "duration") return 0;
  if (row.kind === "time") return "00:00:00";
  return row.choices[0] ?? "";
}

/**
 * The option type a value row becomes.
 *
 * `time` is a `${pack}` option type only in the sense that the pack vocabulary
 * has no better one: a time is a string to the engine, and it is written as a
 * string so that the option validates rather than being refused for a type the
 * schema does not have.
 */
function optionType(row: DevValueRow): string {
  if (row.choices.length > 0) return "enum";
  if (row.kind === "time") return "string";
  return row.kind;
}

/**
 * A setting's key, from the source's own name for it.
 *
 * A blueprint input's key is `input:lux_threshold`; the option's key is what
 * follows the colon, so the module's settings read the way the blueprint's own
 * author named them. The index is only the fallback for a name that leaves
 * nothing usable behind -- a key of punctuation, or an empty one -- because two
 * rows must not collide on the empty string.
 */
function settingKey(row: DevValueRow, index: number): string {
  const tail = row.key.includes(":")
    ? row.key.split(":").slice(1).join("_")
    : row.key;
  const cleaned = tail
    .replace(/[^a-z0-9_]+/gi, "_")
    .toLowerCase()
    .replace(/^_+|_+$/g, "");
  return cleaned || `setting_${index}`;
}

/**
 * A behaviour's name for every service in one reading, from the service it calls.
 *
 * The service's tail alone -- `turn_on`, `turn_off` -- because that is what the
 * behaviour *is*, and a trailing index on every name would make the module's
 * units read `dynamic_lighting.turn_off_4`, where the 4 is the position of a row
 * in a list nobody sees. The index is kept for the one case that needs it: two
 * behaviours calling the same service, where one name for both would be two
 * units sharing an id. The numbering is by position among the collisions and not
 * among all services, so the names a person reads are `turn_on` and `turn_on_2`
 * rather than `turn_on_2` and `turn_on_5`.
 */
function behaviourNames(services: DevServiceRow[]): string[] {
  const tails = services.map((row) => tail(row.service));
  const counts = new Map<string, number>();
  for (const name of tails) counts.set(name, (counts.get(name) ?? 0) + 1);
  const seen = new Map<string, number>();
  return tails.map((name) => {
    if ((counts.get(name) ?? 0) === 1) return name;
    const nth = (seen.get(name) ?? 0) + 1;
    seen.set(name, nth);
    return `${name}_${nth}`;
  });
}

/** The part of a service name after the dot, cleaned to an id's alphabet. */
function tail(service: string): string {
  const cleaned = (service.split(".")[1] ?? service)
    .replace(/[^a-z0-9_]+/gi, "_")
    .toLowerCase()
    .replace(/^_+|_+$/g, "");
  return cleaned || "behaviour";
}

/** A pack name from a title, as `schemas/pack-manifest` spells one. */
function packName(title: string): string {
  const cleaned = title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
  if (!cleaned) return "";
  return /^[a-z]/.test(cleaned) ? cleaned : `m_${cleaned}`;
}

function capitalise(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

/**
 * The trigger a behaviour starts on: the source's own, when the vocabulary has
 * it, and `state` otherwise.
 *
 * A document may trigger on something a pack may not -- a device trigger, a
 * calendar -- and the reading reports it. Defaulting the dropdown to a platform
 * it does not contain would render a `<select>` with no option selected, which
 * is a form that looks filled in and is not.
 */
function firstTrigger(triggers: string[]): string {
  const found = triggers.find((trigger) =>
    (TRIGGERS as readonly string[]).includes(trigger),
  );
  return found ?? "state";
}

if (!customElements.get("open-house-tab-dev")) {
  customElements.define("open-house-tab-dev", DevTab);
}
