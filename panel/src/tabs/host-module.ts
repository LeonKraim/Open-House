/**
 * Importing a source as a module: the one screen that makes a blueprint run.
 *
 * **Nothing is translated here.** The document a person picks stays what it is
 * -- a Home Assistant automation, built from a blueprint with the choices made
 * below -- and Home Assistant runs it. Everything the blueprint can do survives
 * because nothing rewrites it: `choose`, `repeat`, waits, templates, device
 * actions and the rest run exactly as their author wrote them. That is the whole
 * difference between this screen and the pack workbench beside it, and it is why
 * this one has no "this part does not import" banner: a source that Home
 * Assistant accepts is a module this hosts.
 *
 * **Two decisions, and both are the person's.** What fills each input the source
 * asks for, and which of the source's own internals become outputs other modules
 * can read. The second is the one that has no precedent in Home Assistant's own
 * editors: a blueprint's `variables:` and `response_variable:`s are private to a
 * run, and picking one here is what makes it a value that outlives the run --
 * published to `sensor.open_house_<module>_<key>`, which any automation,
 * dashboard or other module may then read.
 *
 * **An output is made, not rewritten.** The publishing step is *appended* to the
 * automation's actions (`ha_adapter.module_host.publish_actions`); the actions
 * the blueprint wrote are untouched, so a person who picks an output gets the
 * blueprint plus one more step, never the blueprint minus something.
 *
 * **The screen is the server's reading turned inside out.** `modules/read`
 * answers with one row per input and one row per candidate, and this renders one
 * control per row. A row the server did not report cannot be decided about here,
 * which is what keeps the module on disk a restatement of the document the
 * person looked at rather than of a document the screen invented.
 *
 * **Field names are positional**, for the reason `dev.ts` gives: a `ha-form`
 * field name is a data path, and a blueprint input's name is `lux_sensor` or
 * worse. The rows carry the real names; the form addresses them by index.
 */

import { html, nothing, type TemplateResult } from "lit";
import { OpenHouseElement } from "../base.ts";
import {
  bySelector,
  castModeSelector,
  entityIds,
  isTemplate,
  scriptIdOf,
  writtenCondition,
  type CastMode,
} from "../components/hosted-module.ts";
// Re-exported so a caller has one place to reach for the cast's vocabulary,
// whichever screen it is looking at: the rule is the components module's, and
// the import screen and the card share it rather than each writing their own.
// `scriptIdOf` lives in `casts.ts` beside that vocabulary because the card needs
// it too -- both screens show the same script picker and read its answer the
// same way -- and it is re-exported from here because it has been this module's
// public name for a caller since before the card had a script field at all.
export {
  castModeSelector,
  scriptIdOf,
  writtenCondition,
  type CastMode,
} from "../components/hosted-module.ts";
// Registered by the import: a row cast to a flow draws `<open-house-node-red>`,
// and this file only has to have the element defined.
import "../components/node-red-editor.ts";
import type {
  DevSource,
  HostedModule,
  ModuleBinding,
  ModuleCandidate,
  ModuleEditSeed,
  ModuleInputRow,
  ModuleReadReply,
  ModuleSlotWord,
} from "../api/models.ts";

/** Which of the three ways a source arrives. */
type SourceKind = "automation" | "blueprint" | "text";

/**
 * How one input is filled, as the row's own control offers it.
 *
 * `slot` and `global_slot` are one *kind* of answer with two scopes, and they
 * are separate here because the scopes answer different questions: a slot is
 * "whatever this module's room binds", which is what makes the same module watch
 * the kitchen's sensor in the kitchen, and a **global slot** is "the house's own
 * device, in every room" -- the house's binding and only that, even in a room
 * that bound the role itself. A person choosing between them is choosing where
 * the answer is looked up, which is why the choice is on this dropdown and not a
 * second one.
 */
export type How =
  | "default"
  | "literal"
  | "entity"
  | "output"
  | "slot"
  | "global_slot"
  | "template";

/** One input's decision, kept beside the row the server sent. */
export interface InputDecision {
  input: ModuleInputRow;
  how: How;
  /** The control's own value: text, an entity id, or a `"<module>/<key>"`. */
  value: unknown;
  /**
   * A cast written *over* whatever the row picked, empty for none.
   *
   * The answer to "I have chosen the thing this input reads, and now I want it
   * cast": logic written beside the choice rather than instead of it, in the
   * same row and at the moment of choosing. It wins over the choice when it is
   * there, and the choice is what it wins over rather than being replaced by --
   * clearing the cast gives the choice back, which is what makes it something
   * reached for on demand.
   */
  cast?: string;
  /**
   * Which of the two cast editors this row is showing, if either.
   *
   * A cast may be written two ways and they are different things, so the row
   * asks which rather than offering one and implying the other:
   *
   * - `template` is Home Assistant's template box -- an expression, rendered
   *   where the input lands.
   * - `condition` is Home Assistant's **condition editor**, the one automations
   *   and blueprints carry: a builder rather than an expression, and the same
   *   answer a person gives an automation's `condition:`.
   *
   * A condition is not written into the module at all. It is handed to the
   * server, which makes it into an entity of its own and binds the input to that
   * -- because a trigger's `entity_id` is matched against real entities and
   * never rendered, so a condition written there would be text that matched
   * nothing and an automation that installed and never fired.
   */
  castMode?: CastMode;
  /**
   * The condition itself, when `castMode` is `condition`: Home Assistant's own
   * condition config, as its editor built it.
   */
  condition?: unknown;
  /**
   * The script this input is answered by, when `castMode` is `script`: the id of
   * the person's own script, without its domain.
   *
   * The odd one out among the casts, and the difference is what a script *is*: a
   * template is text this screen holds and a condition is a config it holds, but
   * a script is a thing that already exists in Home Assistant -- a sequence with
   * a name and an editor and its own page -- and Open House never writes one. So
   * what is kept here is a *reference*, and the editor beside it opens the real
   * thing rather than drawing a second one.
   */
  script?: string;
  /**
   * Whether this input stays settable on the module after it is hosted.
   *
   * Off unless the person says otherwise, which is the same rule the rest of
   * the house follows: a module arrives doing one thing well, and every dial it
   * offers is one somebody chose to keep.
   */
  expose: boolean;
}

/** One candidate's decision: whether it is an output, and what it is called. */
interface OutputDecision {
  candidate: ModuleCandidate;
  picked: boolean;
  key: string;
}

/** One schema item of a `ha-form`, which is what Home Assistant's forms take. */
interface FormItem {
  name: string;
  selector: Record<string, unknown>;
}

/** The separator an output binding's one control spells with. */
const OUTPUT_SEPARATOR = "/";

/** The option value meaning "leave this to the blueprint's own default". */
const LEAVE = "default";

export class HostModuleScreen extends OpenHouseElement {
  static override properties = {
    ...OpenHouseElement.properties,
    editing: { state: true },
    automations: { state: true },
    blueprints: { state: true },
    kind: { state: true },
    automationKey: { state: true },
    blueprintKey: { state: true },
    pasted: { state: true },
    reading: { state: true },
    decisions: { state: true },
    outputs: { state: true },
    moduleTitle: { state: true },
    moduleDescription: { state: true },
    moduleAuthor: { state: true },
    moduleVersion: { state: true },
    moduleLicence: { state: true },
    moduleReplace: { state: true },
    slots: { state: true },
    rooms: { state: true },
    hosted: { state: true },
    isLoading: { state: true },
    busy: { state: true },
    drafts: { state: true },
    saving: { state: true },
    error: { state: true },
    notice: { state: true },
  };

  private automations: DevSource[] = [];
  private blueprints: DevSource[] = [];
  private hosted: HostedModule[] = [];

  /**
   * The slug of the module being **edited**, empty for a fresh import.
   *
   * The same screen either way, because the two are the same questions: what
   * fills each input, and which of the source's internals become outputs. What
   * an edit adds is that the questions are already answered, which is what the
   * reading hands back as `editing` -- so all this carries is *which* module,
   * and everything else about it is read rather than passed in.
   *
   * A slug and not a definition, because a document hosted directly has no store
   * row and is still a thing a person has to be able to change.
   */
  private editing = "";

  /** The module's own document and answers, once the reading has brought them. */
  private seed: ModuleEditSeed | null = null;

  private kind: SourceKind = "blueprint";
  private automationKey = "";
  private blueprintKey = "";
  private pasted = "";

  private reading: ModuleReadReply | null = null;
  private decisions: InputDecision[] = [];
  private outputs: OutputDecision[] = [];

  /** What the module will be called. Defaults to the source's own title. */
  private moduleTitle = "";

  /**
   * What the store row says about the module besides its name.
   *
   * None of it is decoration: a module is a thing a person may hand to somebody
   * else, and a file with no author, no version and no licence is a file nobody
   * can decide what to do with. All four start at the obvious answer -- the
   * blueprint's own description, an empty author, version 1.0.0 and no licence --
   * so saving a module for one's own house is still one press.
   */
  private moduleDescription = "";
  private moduleAuthor = "";
  private moduleVersion = "1.0.0";
  private moduleLicence = "no_licence";

  /**
   * Whether to overwrite a module this house already offers under this name.
   *
   * Unticked, so the server refuses a clash with a sentence naming the module
   * that is already there. Re-importing a blueprint somebody has since edited is
   * an update and wants this ticked; overwriting a module by accident is the one
   * way this screen can lose work, which is why it is asked for.
   */
  private moduleReplace = false;

  /** Every slot this house carries, which is what the slot menu offers. */
  private slots: ModuleSlotWord[] = [];

  /**
   * The address a **browser** opens Node-RED at, or empty when none is set.
   *
   * Read from `capabilities` rather than derived: the address Home Assistant
   * pushes flows to is a compose service name and resolves nowhere a person's
   * browser is, so the only address worth embedding is the configured one. A row
   * cast to a flow embeds the editor under its own field, which is why this
   * screen needs it at all -- and it is handed down rather than read again by
   * every row's frame, so a screen with ten of them asks once.
   */
  private nodeRedUrl = "";

  private isLoading = true;
  private busy = false;
  private error: ReturnType<OpenHouseElement["toError"]> | null = null;
  private notice: string | null = null;

  override connectedCallback(): void {
    super.connectedCallback();
    void this.load();
  }

  // -- loading -------------------------------------------------------------

  /**
   * The sources to pick from, and the modules already hosted.
   *
   * The hosted list is read here as well as offered by `modules/read`, because
   * the screen shows it before any source has been read: "what does my house
   * run, and what does each of them publish" is a question worth answering on
   * its own, and it is the one a person comes back to.
   */
  private async load(): Promise<void> {
    this.isLoading = true;
    this.error = null;
    try {
      const client = this.requireClient();
      const [sources, modules, capabilities] = await Promise.all([
        client.devSources(),
        client.modulesHosted(),
        client.capabilities(),
      ]);
      this.automations = sources.automations;
      this.blueprints = sources.blueprints;
      this.hosted = modules.modules;
      this.nodeRedUrl = capabilities.node_red_url ?? "";
      this.automationKey ||= this.automations[0]?.key ?? "";
      this.blueprintKey ||= this.blueprints[0]?.key ?? "";
      // **An edit reads itself in.** The source is not chosen here -- it is the
      // module's own document -- so there is nothing to wait for before reading,
      // and going straight at it is what makes the screen arrive filled rather
      // than arriving as an empty import with an Edit button pressed.
      if (this.editing) await this.read();
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.isLoading = false;
      this.requestUpdate();
    }
  }

  private get handle(): { key?: string; text?: string } {
    if (this.kind === "automation") return { key: this.automationKey };
    if (this.kind === "blueprint") return { key: this.blueprintKey };
    return { text: this.pasted };
  }

  private get canRead(): boolean {
    if (this.kind === "automation") return Boolean(this.automationKey);
    if (this.kind === "blueprint") return Boolean(this.blueprintKey);
    return this.pasted.trim().length > 0;
  }

  // -- reading -------------------------------------------------------------

  /**
   * Read the source, and offer every decision it allows.
   *
   * The bindings go *with* the reading rather than after it, because they change
   * the answer: an entity input's reading is only offered as a candidate once
   * the person has said which device it is, and a screen that read before a
   * choice was made would offer a reading of nothing. Reading again is therefore
   * also how a binding is confirmed -- which is why the value controls re-read
   * on `change` rather than on every keystroke.
   */
  private async read(keepDecisions = false): Promise<void> {
    this.busy = true;
    this.error = null;
    this.notice = null;
    const before = new Map(
      this.decisions.map((decision) => [decision.input.name, decision]),
    );
    try {
      const reply = await this.requireClient().modulesRead(
        this.editing ? "text" : this.kind,
        this.editing ? {} : this.handle,
        this.bindings,
        this.editing,
        // **Nothing at all until the screen has read once**, and its own rows
        // from then on -- including none, which is what a person who has just
        // switched their only cast row back to a value means. Before that read
        // there are no rows to have an opinion about, and the server answers from
        // the module's own record instead: a row it is already answered with is
        // offered as something to publish even before this screen has drawn it.
        this.decisions.length > 0 ? castRowNames(this.decisions) : {},
      );
      this.reading = reply;
      this.hosted = reply.hosted;
      this.slots = reply.slots;
      // The seed and the reading are the same fact read two ways -- what the
      // module's own document asks for, and what the module answers -- and only
      // an edit is given both. A fresh import has no answers yet, so every row
      // starts on the source's default, which is what the `??` below spells.
      const seed = reply.editing ?? null;
      if (seed) this.seed = seed;
      this.decisions = reply.inputs.map(
        (input) =>
          (keepDecisions ? before.get(input.name) : undefined) ??
          (this.seed
            ? editDecision(input, this.seed)
            : {
                input,
                // **A device input starts on a slot**, because a slot is the only
                // answer it is offered: it names a device, and a slot is the one
                // answer that follows the room the module is put in. *Which* slot
                // is the person's answer and not one this screen can guess, so the
                // value is left empty and the row says it still needs answering.
                how: isDeviceInput(input)
                  ? "slot"
                  : input.bound && input.satisfied
                    ? "literal"
                    : LEAVE,
                value: isDeviceInput(input) || !input.bound ? undefined : input.value,
                // **Kept unless it is unticked.** A blueprint's inputs are the
                // things a person would want to change about the module it
                // becomes, and hiding every one of them until each was ticked
                // left "what can I set on this thing I just imported" with the
                // answer "nothing". Keeping one costs nothing: the value given
                // here is what it starts at (`modules._async_build` builds from
                // the same answers), and an input the person wants frozen is one
                // untick.
                // A device input is the one exception: what it would be kept as
                // is a device, which is the answer it may no longer be given.
                expose: !isDeviceInput(input),
                castMode: "none" as CastMode,
              }),
      );
      this.outputs = reply.candidates.map((candidate) => {
        const row = this.outputs.find(
          (kept) => kept.candidate.name === candidate.name,
        );
        const picked = this.seed?.picks.find(
          (pick) => pick.name === candidate.name,
        );
        return {
          candidate,
          picked: row?.picked ?? Boolean(picked),
          key: row?.key || picked?.key || candidate.suggested_key,
        };
      });
      // **Seeded once and not on every reading.** A value control re-reads when
      // it changes (`applyInput`), and this runs again then: laying the module's
      // own metadata back over the fields each time would undo whatever the
      // person had just typed into them.
      if (this.seed && !keepDecisions) this.seedMetadata(this.seed);
      this.moduleTitle ||= reply.source.title;
    } catch (error) {
      this.error = this.toError(error);
      this.reading = null;
    } finally {
      this.busy = false;
      this.requestUpdate();
    }
  }

  /**
   * The module's own words, into the fields that already hold some.
   *
   * All five are the module's rather than the blueprint's -- a module the house
   * offers keeps the name it was stored under, and a document hosted directly
   * keeps the one its card reads from -- so an edit that did not put them back
   * would rename the module to its blueprint on the first press.
   *
   * A licence the store does not know falls back rather than clearing the menu:
   * the field is a `<select>`, and a value with no option is a menu showing
   * something the person cannot re-choose.
   */
  private seedMetadata(seed: ModuleEditSeed): void {
    this.moduleTitle = seed.title;
    this.moduleDescription = seed.description;
    this.moduleAuthor = seed.author;
    if (seed.version) this.moduleVersion = seed.version;
    if (this.licences.includes(seed.licence)) this.moduleLicence = seed.licence;
  }

  /** The licences the store offers, from the reading, with the current one back. */
  private get licences(): string[] {
    return this.reading?.licences ?? [this.moduleLicence];
  }

  /** What is filled, as the server reads a binding, keyed by input name. */
  private get bindings(): Record<string, ModuleBinding> {
    const found: Record<string, ModuleBinding> = {};
    for (const decision of this.decisions) {
      const binding = bindingFor(decision);
      if (binding) found[decision.input.name] = binding;
    }
    return found;
  }

  /**
   * What is answered with a condition, keyed by input name.
   *
   * Sent beside the bindings rather than as one, because it is not a value: the
   * server makes each of these into an entity of its own and binds the input to
   * *that*, and the entity id is the server's to spell (`module_host.derived_entity_id`).
   */
  private get casts(): Record<string, unknown> {
    return castAnswers(this.decisions);
  }

  /**
   * The inputs the module keeps as options that nothing has a value for.
   *
   * Answered "Blueprint default" and given no default by the blueprint, so
   * there is nothing to build the automation from yet -- the module is saved,
   * it is installed wherever the person puts it, and it does not run until
   * somebody sets the option. Named here so the screen says which ones, because
   * the alternative reading of a module that has not run is that the import
   * failed. What it is *not* is a reason to hold Save off: what the person is
   * making is a thing with an option they have not filled in, which is a module
   * with a job still to do rather than a module that cannot exist.
   */
  private get unfilled(): string[] {
    return this.decisions
      .filter(
        (decision) =>
          !bindingFor(decision) &&
          // A condition is an answer, so a row that has one is not waiting for
          // anything -- the server builds its entity and the input is filled.
          castCondition(decision) === null &&
          // And a script is an answer too, when there is one behind it: a row
          // showing the script mode with nothing picked yet *is* waiting, and it
          // says so below in its own sentence rather than as one of these.
          (castModeOf(decision) !== "script" || !(decision.script ?? "").trim()) &&
          !decision.input.has_default &&
          decision.how !== "literal",
      )
      .map((decision) => decision.input.title || decision.input.name);
  }

  /** The inputs the module keeps settable, by name, in the order they were asked. */
  private get kept(): string[] {
    return this.decisions
      // A device input keeps nothing, whichever way a decision was seeded:
      // `renderInput` does not draw the switch for one, and this is the same rule
      // read where the payload is built rather than where it is drawn.
      .filter((decision) => decision.expose && !isDeviceInput(decision.input))
      .map((decision) => decision.input.name);
  }

  /**
   * Two outputs the person has given one name.
   *
   * Caught here rather than at the server, because the server has to refuse it
   * -- two outputs sharing a key share one entity and one overwrites the other
   * -- and a refusal that arrives after the whole form is filled is a person
   * re-reading everything to find which two rows agree. Empty is the happy case.
   */
  private get clashing(): string[] {
    const keys = this.outputs
      .filter((output) => output.picked && output.key.trim())
      .map((output) => output.key.trim());
    return [...new Set(keys.filter((key, at) => keys.indexOf(key) !== at))];
  }

  /**
   * Whether the module names devices from this house rather than roles.
   *
   * An input answered with a device is one house's device: the module installs
   * here and nowhere else. One answered with a **slot** is a promise about
   * whatever room it lands in, which is what makes a module shareable -- so this
   * is the sentence the screen says before a person sends the file on.
   */
  private get pinned(): boolean {
    return this.decisions.some((decision) => decision.how === "entity");
  }

  private get canSave(): boolean {
    return (
      this.reading !== null &&
      !this.busy &&
      this.moduleTitle.trim().length > 0 &&
      this.clashing.length === 0
    );
  }

  private async save(): Promise<void> {
    this.busy = true;
    this.error = null;
    this.notice = null;
    // **The same answers either way**, because an import and an edit are the
    // same decision about the same five things -- what fills each input, which
    // of those stay settable, which internals become outputs, what the module is
    // called. What differs is only what the server does with them.
    //
    // **`replace` is not one of the answers, and only an import is asked it.**
    // It says what to do about a module of the same *name* already in the store,
    // which is a question an import has and an edit does not -- an edit is
    // addressed to the module itself. The edit command refuses it rather than
    // ignoring it, which is how sending it anyway was found.
    const definition = {
      title: this.moduleTitle.trim(),
      description: this.moduleDescription.trim(),
      author: this.moduleAuthor.trim(),
      version: this.moduleVersion.trim(),
      licence: this.moduleLicence,
      bindings: this.bindings,
      outputs: this.outputs
        .filter((output) => output.picked && output.key.trim())
        .map((output) => ({
          name: output.candidate.name,
          key: output.key.trim(),
        })),
      settings: this.kept,
      casts: this.casts,
      flows: castFlowAnswers(this.decisions),
      scripts: castScriptAnswers(this.decisions),
    };
    try {
      const seed = this.seed;
      if (seed) {
        await this.requireClient().modulesEdit(
          seed.module,
          "text",
          // The module's own document, sent back untouched: what is being edited
          // is how it is set up, and swapping the document underneath is a
          // different thing -- every answer on this screen names an input only
          // that document declares.
          { text: seed.text },
          definition,
        );
        this.notice = saved(seed.installs);
        // **The card this was opened from is now wrong about the module**, and
        // so is every other card of it: the edit changed the module rather than
        // this room's copy, and each of those screens re-reads on this event.
        this.dispatchEvent(
          new CustomEvent("module-changed", { bubbles: true, composed: true }),
        );
      } else {
        const reply = await this.requireClient().modulesDefine(
          this.kind,
          this.handle,
          { ...definition, replace: this.moduleReplace },
        );
        // **Nothing is installed by this, and the message has to say so.** A
        // person who has just pressed the last button on an import screen reads
        // the next sentence as "what did I just do": a module this house now
        // offers, and where to go to put it somewhere.
        this.moduleReplace = false;
        this.notice =
          `Saved as ${reply.module}. It is in the Store tab, where you can ` +
          "install it into any room, or download the file to give to somebody else.";
      }
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.busy = false;
      this.requestUpdate();
    }
  }

  // -- render --------------------------------------------------------------

  override render(): TemplateResult {
    return html`
      ${this.errorBanner(this.error)}
      ${this.notice ? html`<div class="banner info">${this.notice}</div>` : null}
      ${this.isLoading
        ? this.loading(
            this.editing ? "Reading the module..." : "Reading your automations...",
          )
        : nothing}
      ${this.editing ? this.renderProvenance() : this.renderSource()}
      ${this.reading ? this.renderInputs() : nothing}
      ${this.reading ? this.renderOutputs() : nothing}
      ${this.reading ? this.renderHost() : nothing}
    `;
  }

  /**
   * Section 1 with the module already chosen: what it is, and what it runs.
   *
   * **The source is not swappable here, and the screen says so rather than
   * hiding it.** Every answer below names an input that *this* document
   * declares, so reading a different one under them would either drop answers or
   * bind them to inputs that are no longer there. Making a module from a
   * different document is an import, which is the other tab.
   *
   * The document is still shown, in full, because it is the thing every answer
   * below is about -- somebody deciding what an input should be filled with is
   * owed the automation it lands in, without leaving the screen to go and find
   * it in Home Assistant.
   */
  private renderProvenance(): TemplateResult | typeof nothing {
    const seed = this.seed;
    // Before the reading arrives there is nothing true to say about the module,
    // and the loading line above is already saying it.
    if (!seed) return nothing;
    return html`<section class="card">
      <h2>1. What this module is</h2>
      <p class="help">
        This is <strong>${seed.title}</strong>, which your house already
        offers${seed.blueprint
          ? html` and which came from <code>${seed.blueprint}</code>`
          : nothing}. What you change below are the answers it was built with,
        and every one of them names an input this document declares -- so the
        document itself is not chosen here. Making a module out of a different
        one is an import.
      </p>
      <details class="nested">
        <summary>The document it runs</summary>
        <pre class="expression">${seed.text}</pre>
      </details>
    </section>`;
  }

  private renderSource(): TemplateResult {
    return html`<section class="card">
      <h2>1. Choose a source</h2>
      <div class="tabs">
        ${(["automation", "blueprint", "text"] as const).map(
          (kind) => html`<button
            type="button"
            class="tab"
            aria-selected=${this.kind === kind ? "true" : "false"}
            @click=${() => {
              this.kind = kind;
              this.reading = null;
              this.decisions = [];
              this.outputs = [];
              this.moduleTitle = "";
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

  private renderAutomationPicker(): TemplateResult {
    if (this.automations.length === 0) {
      return html`<p class="muted">
        This instance has no automations. Make one in Home Assistant, or paste
        YAML, and it will be here.
      </p>`;
    }
    return html`<div class="field">
      <label class="label" for="host-automation">Automation</label>
      <select
        id="host-automation"
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
      <label class="label" for="host-blueprint">Blueprint</label>
      <select
        id="host-blueprint"
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
        blueprint file. A blueprint with no inputs at all imports too.
      </p>
    </div>`;
  }

  private renderInputs(): TemplateResult {
    const reading = this.reading!;
    if (reading.inputs.length === 0) {
      return html`<section class="card">
        <h2>2. Fill in what it asks for</h2>
        <p class="muted">
          This source asks for nothing: it runs as it was written.
        </p>
      </section>`;
    }
    return html`<section class="card">
      <h2>2. Fill in what it asks for</h2>
      ${this.seed
        ? html`<p class="muted">
            These are the <strong>module's own</strong> answers: what it does
            wherever it is installed, until a room changes one for itself. A
            ticked row stays a dial on the module card; an unticked one is
            answered once, here, and fixed inside the automation.
          </p>`
        : nothing}
      ${this.seed
        ? nothing
        : html`<p class="muted">
            Every input the blueprint declares, and every one of them stays an
            <strong>option</strong> on the module: it is settable there, wherever
            the module ends up. A row left as "Blueprint default" starts at
            the blueprint's own default where it has one -- and where it has
            none, the module is saved and waits: nothing runs until somebody sets
            that option. Answer a row here instead to give it the value it starts
            at, or untick one to answer it once and fix it inside the automation.
          </p>`}
      <p class="muted">
        Every row has a <strong>cast</strong> under it, and it is what to reach
        for when the answer is not the thing itself. Home Assistant's own
        <strong>condition</strong> editor is how one input answers a question
        about the house -- true while a helper reads <code>sleep</code>, false
        while it reads <code>awake</code> -- and Open House makes that condition
        into a real entity and points the input at it, which is the only way it
        can work where a <em>trigger</em> names the input. A
        <strong>template</strong> is the other editor: an expression, rendered
        wherever the input lands. Either one replaces the choice above while it
        is there, and clearing it gives the choice back.
      </p>
      ${this.unfilled.length > 0
        ? html`<div class="banner info">
            ${this.unfilled.join(", ")}
            ${this.unfilled.length === 1 ? "has" : "have"} no default in the
            blueprint and no answer here, so the module keeps
            ${this.unfilled.length === 1 ? "it" : "them"} as an option and does
            not run until you set ${this.unfilled.length === 1 ? "it" : "them"}.
          </div>`
        : nothing}
      <div class="stack">
        ${this.decisions.map((decision, index) => this.renderInput(decision, index))}
      </div>
    </section>`;
  }

  /**
   * One input, as a titled row.
   *
   * A row of its own, and a form of its own inside it, because of how Home
   * Assistant's own form renders a label: a dropdown's label is drawn as a small
   * caption above the box, where a switch's is drawn as a title beside it. One
   * input per form is what lets this screen write the title itself -- the input's
   * own, in the same weight as every other heading on the page -- and leave the
   * form's labels to say what each control *is*: what fills it, its value, and
   * that keeping it makes it a setting.
   */
  private renderInput(decision: InputDecision, index: number): TemplateResult {
    const input = decision.input;
    const title = input.title || input.name;
    // A device input is answered with a slot and nothing else (`howSelector`),
    // and it is never kept as a setting: what it would be kept as is a device --
    // one house's -- which is the answer this rule removes.
    const device = isDeviceInput(input);
    const schema: FormItem[] = [
      { name: `how_${index}`, selector: howSelector(input, decision.how) },
    ];
    const data: Record<string, unknown> = {
      [`how_${index}`]: decision.how,
      [`expose_${index}`]: decision.expose,
    };
    // **The cast, in the row where the choosing happens -- and in every row.**
    // Offered beside every answer, because the want it answers can arrive at any
    // of them: what the blueprint should see may be logic worked out from what
    // was chosen, or a condition of its own, or a template over a value typed in.
    // A row that offers no cast is a row a person has to leave the screen to
    // answer, which is what this replaced.
    const mode = castModeOf(decision);
    // **One answer per row, and one control drawing it.** The choice the row's
    // own field holds is not used once a cast is on -- a template is bound in
    // place of it and a condition's entity is what the input is pointed at -- so
    // the field goes and only the editor that replaced it is drawn. Two controls
    // for one answer reads as two answers, and the dead one is on top.
    const value = valueSelector(decision, this.hosted, this.slots);
    // **Every cast replaces the field, a flow included.** A flow is an answer
    // like the other two, so the field goes with it and the row says instead
    // what the flow will be given -- which device arrives wired into it, and
    // where to build the rest. The field is still what *chooses* that device,
    // and its answer is kept: the row goes on sending it while the flow is on,
    // so switching back to the field finds the device where it was left.
    if (value && mode === "none") {
      schema.push({ name: `value_${index}`, selector: value });
    }
    if (decision.value !== undefined) data[`value_${index}`] = decision.value;
    schema.push({
      name: `cast_mode_${index}`,
      selector: castModeSelector(input.in_trigger),
    });
    data[`cast_mode_${index}`] = mode;
    if (mode === "template") {
      schema.push({ name: `cast_${index}`, selector: { template: {} } });
      data[`cast_${index}`] = decision.cast ?? "";
    }
    if (mode === "condition") {
      schema.push({ name: `cast_${index}`, selector: { condition: {} } });
      if (decision.condition !== undefined) {
        data[`cast_${index}`] = decision.condition;
      }
    }
    if (mode === "script") {
      // The script is picked, not written: it is Home Assistant's own object and
      // the field is a device-style picker over its entities, so the person names
      // one that already exists -- or opens the editor below and makes one, which
      // then arrives back here through `script-chosen`.
      schema.push({
        name: `script_${index}`,
        selector: { entity: { domain: ["script"] } },
      });
      if (decision.script) data[`script_${index}`] = `script.${decision.script}`;
    }
    if (!device) schema.push({ name: `expose_${index}`, selector: { boolean: {} } });
    return html`<div class="field" data-input=${index}>
      <div class="label">${title}</div>
      ${input.description
        ? html`<p class="help">${input.description}</p>`
        : nothing}
      ${device && !decision.value
        ? html`<p class="help warn">
            Name the slot this module takes the device from. It is a device and
            not a role, so a slot is the only answer that follows the room the
            module is put in -- or the whole house's, if you pick a global one.
          </p>`
        : nothing}
      ${mode === "template" && input.in_trigger
        ? html`<p class="help warn">
            The trigger names this input, so a template here would not work:
            Home Assistant matches a trigger's entity against the entities the
            house has, and never renders what is written there -- so it would
            match nothing and the automation would install and never fire. Use a
            <strong>condition</strong> instead: Open House works it out and
            points the trigger at the answer.
          </p>`
        : nothing}
      <ha-form
        data-input=${index}
        .hass=${this.hass}
        .data=${data}
        .schema=${schema}
        .computeLabel=${(item: FormItem) => labelFor(item.name, decision.how, mode)}
        @value-changed=${(event: CustomEvent<{ value: Record<string, unknown> }>) => {
          this.applyInput(index, event.detail.value);
        }}
      ></ha-form>
      ${mode === "script" && input.in_trigger
        ? html`<p class="help warn">
            The trigger names this input, so a script here would not work: Home
            Assistant matches a trigger's entity against the entities the house
            has, and never renders what is written there -- so the automation
            would install and never fire. Use a <strong>condition</strong>
            instead.
          </p>`
        : nothing}
      ${mode === "nodered" ? this.renderFlow(decision) : nothing}
      ${mode === "script" ? this.renderScript(decision, index) : nothing}
    </div>`;
  }

  /**
   * The editor a row answered by a script opens, and what it is for.
   *
   * **The same editor Node-RED's cast gets, embedded -- with one difference that
   * is the whole reason a script cast exists.** A flow is built into an input
   * that already has a device in it: the device is wired into the flow and the
   * flow runs on its own. A script is the other way round: it is *called* when
   * the module runs and hands its answer back, so it needs nothing wired in and
   * nothing watched -- it is where a computation that is a sequence rather than
   * an expression lives, and it is written in Home Assistant's editor because
   * that is what a script is made in.
   */
  private renderScript(decision: InputDecision, index: number): TemplateResult {
    const name = decision.input.name;
    return html`<p class="help" data-script=${name}>
        Runs when the module runs and hands its answer back, which is what this
        input is built from -- so it is worked out afresh every time, and it needs
        nothing wired into it. It is your script: Open House only ever names it.
      </p>
      <open-house-script
        .client=${this.client}
        .script=${decision.script ?? ""}
        .label=${`Home Assistant -- the script for ${name}`}
        @script-chosen=${(event: CustomEvent<{ script_id: string }>) => {
          this.chooseScript(index, event.detail.script_id);
        }}
      ></open-house-script>`;
  }

  /**
   * One row's script, as the editor has just left it.
   *
   * The event carries only the id and the row it belongs to is the one that drew
   * the editor, so nothing has to be matched up: a screen with four script rows
   * has four editors, and the one that was closed is the one that spoke.
   */
  private chooseScript(index: number, scriptId: string): void {
    if (!scriptId) return;
    this.decisions = this.decisions.map((decision, at) =>
      at === index ? { ...decision, script: scriptId } : decision,
    );
  }

  /**
   * What a row answered by a flow is given, on the screen where it is chosen.
   *
   * **The editor is Node-RED's own, embedded**, so the flow is built here beside
   * the row it answers rather than in a tab the person has to find their way back
   * from.
   *
   * **The flow itself does not exist yet.** Open House pushes it when the module
   * is hosted, and the tab's id is Node-RED's to mint then -- so the editor opens
   * on its own landing page, and the sentence above says which half the module
   * will arrive with. Both halves are said because they are very different
   * amounts of work: a row holding a device arrives wired end to end, and a row
   * holding a number arrives as the output node alone.
   *
   * The entity is spelled with the module's *name* left as a hole rather than
   * resolved: the slug is derived from the title by the server
   * (`module_host.flow_entity_id`), and a second implementation of that rule
   * here is how the two spellings drift.
   */
  private renderFlow(decision: InputDecision): TemplateResult {
    const name = decision.input.name;
    const binding = bindingFor(decision);
    const wired = binding !== null && binding.kind === "entity";
    const built = wired
      ? html`The device this row holds is wired into its input node, so the flow
          runs on its own from the moment it is pushed.`
      : html`Nothing is wired into it yet: what this row holds is not a device,
          so there was nothing for a trigger to watch. Build whatever starts the
          flow in Node-RED and wire it in.`;
    return html`<p class="help" data-flow=${name}>
        Writes <code>sensor.open_house_flow_&lt;module&gt;_${name}</code>, which is
        what this setting reads. ${built} It is pushed when you save, and it opens
        below as soon as it exists.
      </p>
      <open-house-node-red
        .client=${this.client}
        .url=${this.nodeRedUrl}
        .label=${`Node-RED -- the flow for ${name}`}
      ></open-house-node-red>`;
  }

  /**
   * Take one row's form data back onto that row, and read again for candidates.
   *
   * The re-read is not decoration: an entity input's reading only becomes a
   * candidate once the person has named the device, so the list of things this
   * module could publish is a function of the answers above it. The decisions
   * themselves are kept across the read -- `read(true)` -- so answering one
   * input does not undo the others. Only the row that changed is written: a form
   * carries its own three fields and knowing nothing about its neighbours.
   */
  private applyInput(index: number, data: Record<string, unknown>): void {
    this.decisions = this.decisions.map((decision, at) => {
      if (at !== index) return decision;
      const how = (data[`how_${index}`] as How) ?? decision.how;
      const mode = (data[`cast_mode_${index}`] as CastMode) ?? castModeOf(decision);
      const written = data[`cast_${index}`];
      // The two editors write to the same field name and hold different things,
      // so only the one that is showing is taken: switching the menu to
      // Condition leaves the template text alone underneath, and switching back
      // finds it where it was left.
      return {
        input: decision.input,
        how,
        // The row's own field is off the form while a cast is on, and a form
        // reports what it drew -- so a name it did not carry keeps the answer it
        // had rather than being read as one that was cleared.
        value:
          how === LEAVE
            ? undefined
            : ((data[`value_${index}`] as unknown) ?? decision.value),
        // A device input keeps nothing: the switch is not drawn for one, and a
        // form reports what it drew.
        expose: isDeviceInput(decision.input)
          ? false
          : ((data[`expose_${index}`] as boolean) ?? decision.expose),
        castMode: mode,
        cast: mode === "template" && typeof written === "string" ? written : decision.cast,
        condition: mode === "condition" ? written : decision.condition,
        // The picker holds an entity id (`script.turn_it_on`) and the answer is
        // the id without its domain, which is what a `script.` call takes. Off
        // the form entirely when another mode is showing, so switching away from
        // the script leaves it where it was left.
        script:
          mode === "script"
            ? scriptIdOf(data[`script_${index}`]) || decision.script
            : decision.script,
      };
    });
    this.outputs = this.outputs.map((output) => ({ ...output }));
    void this.read(true);
  }

  private renderOutputs(): TemplateResult {
    const candidates = this.outputs;
    return html`<section class="card">
      <h2>3. Say what it publishes</h2>
      <p class="muted">
        An output is a value from inside this blueprint that other modules,
        automations and dashboards can read. Each one becomes an entity of its
        own -- <code>sensor.open_house_&lt;module&gt;_&lt;key&gt;</code> --
        published by one step added beside the value it reads, not by a rewrite
        of the blueprint. A row you answered with <em>logic</em> -- a condition,
        a template, a flow, a script -- is offered here too: tick it and the
        answer that row works out becomes an entity the rest of the house can
        read, which is what "expose it" means.
      </p>
      ${candidates.length === 0
        ? html`<p class="muted">
            This source carries nothing that could be published: no
            <code>variables:</code>, no entity input, no
            <code>response_variable</code>, and no service call that drives a
            device. It still hosts, and still runs; it just has nothing to say
            to the rest of the house.
          </p>`
        : html`<div class="list" data-candidates>
            ${candidates.map((row, index) => this.renderCandidate(row, index))}
          </div>`}
    </section>`;
  }

  /**
   * One candidate as a block rather than as a row.
   *
   * It was a `.list-row` -- a horizontal flex -- and a blueprint that writes its
   * values as Jinja writes them long: a candidate's expression is a whole
   * template, and in a squeezed flex child it ran off the card, over the columns
   * beside it and over whatever the row below it was. A candidate is three
   * stacked things instead: what it is, what to call it, and what it reads, each
   * on its own line and wrapping inside the card.
   */
  private renderCandidate(row: OutputDecision, index: number): TemplateResult {
    const candidate = row.candidate;
    const keyId = `candidate-key-${index}`;
    return html`<div class="candidate">
      <label class="check">
        <input
          type="checkbox"
          .checked=${row.picked}
          @change=${(event: Event) => {
            row.picked = (event.target as HTMLInputElement).checked;
            this.outputs = [...this.outputs];
          }}
        />
        <span>
          ${candidate.name}
          <span class="chip">${candidate.kind}</span>
          ${candidate.branch_only
            ? html`<span class="chip warn">only inside a branch</span>`
            : null}
        </span>
      </label>
      <div class="field">
        <label class="label" for=${keyId}>Output name</label>
        <input
          id=${keyId}
          type="text"
          placeholder="output name"
          .value=${row.key}
          ?disabled=${!row.picked}
          @input=${(event: Event) => {
            this.outputs[index] = {
              ...row,
              key: (event.target as HTMLInputElement).value,
            };
            this.outputs = [...this.outputs];
          }}
        />
      </div>
      <p class="help">What it reads:</p>
      ${candidate.expression
        ? html`<p class="expression"><code>${candidate.expression}</code></p>`
        : html`<p class="help">
            Written when the module is built, so there is nothing to show yet:
            this is a value the house answers rather than one the source already
            carries.
          </p>`}
      ${candidate.kind === "cast"
        ? html`<p class="help">
            The answer this row holds rather than a value it was given: tick this
            and the module publishes it as an entity of its own, which any
            automation, dashboard or other module in the house can read.
          </p>`
        : null}
      ${candidate.branch_only
        ? html`<p class="help warn">
            Written inside a branch, so it is only published on the runs that
            take that branch.
          </p>`
        : null}
    </div>`;
  }

  private renderHost(): TemplateResult {
    return html`<section class="card">
      <h2>${this.seed ? "4. Save the changes" : "4. Save it to your store"}</h2>
      <p class="help">
        ${this.seed
          ? html`This <strong>is the module</strong>, not this room's copy of it:
              saving changes what every room running it does. Those rooms keep
              their own answers where they have made any -- what a room has
              chosen for itself survives an edit here.`
          : html`This is a <strong>module</strong>: it is saved to your own store
              rather than put in a room, so you can install it into any room --
              or the whole house -- as many times as you like. Each room gets its
              own copy, its own automation and its own outputs. Nothing runs
              until you install it, from the Store tab.`}
      </p>
      ${this.seed && this.seed.installs.length > 0
        ? html`<div class="banner info">
            Saving rebuilds ${this.seed.installs.length}
            ${this.seed.installs.length === 1 ? "installation" : "installations"}:
            ${this.seed.installs.map((row) => row.room_name).join(", ")}.
          </div>`
        : nothing}
      <div class="field">
        <label class="label" for="host-title">Name</label>
        <input
          id="host-title"
          type="text"
          .value=${this.moduleTitle}
          @input=${(event: Event) => {
            this.moduleTitle = (event.target as HTMLInputElement).value;
          }}
        />
        <p class="help">
          What this house calls the module. It is the module's own name, the
          store row's, and the file's when you download it.
        </p>
      </div>
      <div class="field">
        <label class="label" for="host-description">What it does</label>
        <textarea
          id="host-description"
          rows="2"
          .value=${this.moduleDescription}
          @input=${(event: Event) => {
            this.moduleDescription = (event.target as HTMLTextAreaElement).value;
          }}
        ></textarea>
        <p class="help">
          Left blank, this is the blueprint's own description -- the sentence
          whoever wrote it wrote.
        </p>
      </div>
      <div class="row wrap">
        <div class="field grow">
          <label class="label" for="host-author">Who made it</label>
          <input
            id="host-author"
            type="text"
            .value=${this.moduleAuthor}
            @input=${(event: Event) => {
              this.moduleAuthor = (event.target as HTMLInputElement).value;
            }}
          />
        </div>
        <div class="field">
          <label class="label" for="host-version">Version</label>
          <input
            id="host-version"
            type="text"
            .value=${this.moduleVersion}
            @input=${(event: Event) => {
              this.moduleVersion = (event.target as HTMLInputElement).value;
            }}
          />
        </div>
        <div class="field">
          <label class="label" for="host-licence">Licence</label>
          <select
            id="host-licence"
            .value=${this.moduleLicence}
            @change=${(event: Event) => {
              this.moduleLicence = (event.target as HTMLSelectElement).value;
            }}
          >
            ${this.licences.map(
              (licence) => html`<option value=${licence}>${licence}</option>`,
            )}
          </select>
        </div>
      </div>
      <p class="help">
        These three are what somebody else reads before they install a file you
        send them. They do not change what the module does.
      </p>
      ${this.pinned
        ? html`<div class="banner warn">
            This module names devices from your house, so it is made for your
            house: it will install here, and would install somewhere else only if
            that house has the same devices. Answer an input with a
            <strong>slot</strong> instead -- "the room's lux sensor" -- and the
            module works in any room of any house.
          </div>`
        : nothing}
      ${this.clashing.length > 0
        ? html`<div class="banner warn">
            ${this.clashing.join(", ")}
            ${this.clashing.length === 1 ? "is" : "are"} the name of more than one
            output: two outputs sharing a name share one entity, and one would
            overwrite the other.
          </div>`
        : nothing}
      ${this.unfilled.length > 0
        ? html`<div class="banner info">
            ${this.unfilled.join(", ")}
            ${this.unfilled.length === 1 ? "is" : "are"} kept as an option and
            ${this.unfilled.length === 1 ? "is" : "are"} not set: the blueprint
            gives ${this.unfilled.length === 1 ? "it" : "them"} no default. The
            module saves and installs as it is, and it does not run until you
            set ${this.unfilled.length === 1 ? "it" : "them"} on the module.
          </div>`
        : nothing}
      ${this.seed
        ? nothing
        : html`<div class="field">
            <label class="check">
              <input
                type="checkbox"
                id="host-replace"
                .checked=${this.moduleReplace}
                @change=${(event: Event) => {
                  this.moduleReplace = (event.target as HTMLInputElement).checked;
                }}
              />
              Replace the module already in my store with this name, if there is
              one
            </label>
          </div>`}
      <div class="row">
        <button type="button" @click=${() => void this.save()} ?disabled=${!this.canSave}>
          ${this.busy
            ? "Saving..."
            : this.seed
              ? "Save the changes"
              : "Save this module"}
        </button>
      </div>
      <p class="help">
        ${this.seed
          ? html`The document itself is unchanged, so this is what the module is
              from now on: every room running it is built again from the answers
              above, and an output you untick stops existing in those rooms. A
              room that changed one of these answers for itself keeps its own.`
          : html`The module keeps the document it was made from, so it goes on
              working when the blueprint is edited underneath it -- importing it
              again is how you take an update. Ticked outputs become this
              module's own values: every room that installs it publishes them
              under its own name.`}
      </p>
    </section>`;
  }

}

// -- what a control holds, and what a binding is ----------------------------

/**
 * One input's decision as the server reads a binding, or `null` for a decision
 * that says nothing -- an input left to its default.
 *
 * The three shapes are the server's (`module_host._bound_value`): a literal is
 * a value, an entity is the id of a device, and an output is another module's
 * key. What an output binding is *resolved to* -- the entity id, or a template
 * reading it -- is the server's decision and not this screen's, because it
 * depends on the selector the blueprint declared.
 */
export function bindingFor(decision: InputDecision): ModuleBinding | null {
  // **A template cast is an answer in its own right**, and it is the *whole*
  // answer: a device bound beside it would be written into the automation as
  // well, and Home Assistant would take the device and ignore the cast rather
  // than the other way round. So the cast replaces the choice while it is there
  // -- clearing it gives the choice back, which is what "on demand" means here.
  const mode = castModeOf(decision);
  if (mode === "template" && decision.cast !== undefined && decision.cast.trim()) {
    return { kind: "literal", value: decision.cast };
  }
  // A **condition** cast is not a binding at all, and it is deliberately not one:
  // it travels to the server as a condition (`casts`), which makes an entity out
  // of it and binds the input to *that*. Returning a binding here as well would
  // send two answers for one input, and the one that lost would be the
  // condition -- the automation would be pointed at a device and the logic the
  // person wrote would sit in the record doing nothing.
  if (mode === "condition") return null;
  // A **flow** cast is the one cast that keeps its binding, and it travels on
  // past this: what the flow *watches* is this binding's entity, so the row's
  // own answer is the wire the flow is built from. What the input *reads* is the
  // entity the flow writes, and the server binds the input to that itself, over
  // the top of this, when it builds the automation.
  if (decision.how === LEAVE) return null;
  if (decision.value === undefined || decision.value === null) return null;
  if (decision.how === "literal") return { kind: "literal", value: decision.value };
  if (decision.how === "entity") {
    // **A device, or several.** A blueprint input that takes more than one --
    // a `target` with `multiple` set, which is how a blueprint asks for "the
    // lights" rather than "this light" -- is answered with a list. Which shape
    // the value takes is the server's to decide from the input's own selector
    // (`module_host._bound_value`), so this sends the ids and no wrapping. An
    // empty list is not an answer: a target bound to nothing reads as filled.
    const ids = entityIds(decision.value);
    if (ids.length === 0) return null;
    return { kind: "entity", value: decision.input.multiple ? ids : ids[0] };
  }
  if (decision.how === "slot" || decision.how === "global_slot") {
    // A slot is a promise, so an empty name is not a promise about anything --
    // but a name the room has not bound yet still is one, and the module waits
    // rather than being refused (`modules._async_build`).
    if (typeof decision.value !== "string" || !decision.value) return null;
    // `scope` is where the name is looked up, and it is the whole difference
    // between the two answers: absent means the module's room (with the house's
    // global binding behind it), `"house"` means the house's own and only that.
    return decision.how === "global_slot"
      ? { kind: "slot", slot: decision.value, scope: "house" }
      : { kind: "slot", slot: decision.value };
  }
  if (decision.how === "template") {
    // **A condition, on the input's own terms.** Home Assistant renders a
    // `{{ ... }}` in the field an automation reads, so a template written here
    // is a value the module is *built with* rather than one bound to a device:
    // one input can answer true for one state of the house and false for
    // another, which no single entity id can do. It travels as a literal, which
    // is what it is -- text the server writes into the document untouched -- and
    // it is what makes this work for an entity input too: Home Assistant
    // templates an `entity_id` as well, so a template naming a device is a
    // device as far as the automation is concerned.
    if (typeof decision.value !== "string" || !decision.value.trim()) return null;
    return { kind: "literal", value: decision.value };
  }
  const [module, key] = splitOutput(decision.value);
  if (!module || !key) return null;
  return { kind: "output", module, key };
}

/**
 * One row as the module being **edited** already answers it.
 *
 * **The seed is the authority on what is answered, not the reading.** The
 * reading says every input has a value, because the module's own answers were
 * laid over the document before it was read -- so a row taken from the reading
 * would arrive as a *literal* holding whatever the module had already filled in,
 * and pressing Save would freeze the module to the answers it happens to have.
 * That is the opposite of an edit: it would turn a module that follows its
 * blueprint into one that has stopped. What the module actually answered is in
 * the seed, and an input the seed says nothing about is one the module left
 * alone -- it stays on the source's own default, exactly as it was.
 *
 * The three answers that are more than a value are read before the binding,
 * because each of them *has* one: a condition is bound to the entity the server
 * made out of it, and a flow to the entity the flow writes. Reading the binding
 * first would show either as an entity nobody picked.
 */
export function editDecision(
  input: ModuleInputRow,
  seed: ModuleEditSeed,
): InputDecision {
  const name = input.name;
  const decision: InputDecision = {
    input,
    // **A device input has no "leave it to the blueprint" to come back to.** It
    // is answered with a slot or it is unanswered, so a row with nothing under it
    // opens on the slot answer with the name still to be given -- which is the
    // same row a fresh import shows, rather than a menu offering a default that
    // `declares_default` says a device input does not have.
    how: isDeviceInput(input) ? "slot" : LEAVE,
    // Filled in below when the module has an answer; a row it left alone has
    // none, which is what `how: "default"` means.
    value: undefined,
    // **What the module kept, and that is a different question from what it
    // answered.** A setting is a dial on the module; a binding is what it
    // currently reads. An edit that turned every answer into a dial would put a
    // control on the card for every input the blueprint has.
    expose: seed.settings.includes(name),
    castMode: "none",
  };
  // A script cast is answered by the script *itself*, which is a thing the store
  // row names rather than a value it holds -- so the answer comes off the row's
  // own `script_id` (`_cast_ids`) and, for a module the store defines, off the
  // seed. Both are read because the two can disagree: a definition naming an
  // input a room has not picked a script for has the name and no id.
  const script = input.script_id || seed.scripts[name] || "";
  if (seed.flows[name] !== undefined || input.flow_id) {
    // A flow is the one cast that keeps the row's own answer, because that
    // answer is the wire the flow is built from (`bindingFor`) -- so the row
    // goes on reading it below rather than returning here.
    decision.castMode = "nodered";
  } else if (script) {
    // A script is *not* that: nothing is wired into it, so the row stops here
    // rather than going on to read a binding it does not have.
    decision.castMode = "script";
    decision.script = script;
    return decision;
  } else if (seed.casts[name] !== undefined) {
    decision.castMode = "condition";
    decision.condition = seed.casts[name];
    return decision;
  }
  const bound = seed.bindings[name];
  if (!bound) return decision;
  if (bound.kind === "slot") {
    // Back the way it went out: a house-scoped slot comes back on the *global*
    // answer, because that is the answer that will be saved again -- restoring
    // one as the room's would quietly drop the scope on the next edit, which is
    // a module watching the kitchen's sensor where it was watching the house's.
    decision.how = bound.scope === "house" ? "global_slot" : "slot";
    decision.value = bound.slot;
  } else if (bound.kind === "output") {
    decision.how = "output";
    decision.value = `${bound.module}${OUTPUT_SEPARATOR}${bound.key}`;
  } else if (bound.kind === "entity") {
    decision.how = "entity";
    decision.value = bound.value;
  } else if (isTemplate(bound.value)) {
    // A value with a template in it **is** a template: Home Assistant renders
    // one wherever it lands, so the two are the same answer shown two ways, and
    // the template editor is the way that can say what it is.
    decision.how = "template";
    decision.value = bound.value;
  } else {
    decision.how = "literal";
    decision.value = bound.value;
  }
  return decision;
}

/**
 * What an edit did, in the one sentence a person needs afterwards.
 *
 * **Naming the rooms is the point of it, not decoration.** Changing a module
 * rather than the copy in front of you is a promise that the other rooms change
 * too, and somebody who is not told which ones changed has to go and look.
 */
function saved(installs: readonly { room_name: string }[]): string {
  if (installs.length === 0) {
    return "Saved. The module is updated in your store, and nothing is running it yet.";
  }
  const rooms = installs.map((row) => row.room_name || "the whole house");
  if (rooms.length === 1) {
    return `Saved. The module itself is updated, and ${rooms[0]} was rebuilt from it.`;
  }
  return (
    `Saved. The module itself is updated, and all ${rooms.length} rooms running ` +
    `it were rebuilt: ${rooms.slice(0, -1).join(", ")} and ${rooms[rooms.length - 1]}.`
  );
}

/**
 * Which cast editor a row is showing, with the old shape read as what it was.
 *
 * A decision written before the mode existed carried a bare `cast` string, and
 * that string was always a template -- so a row keeping one goes on meaning the
 * same thing rather than silently losing an answer.
 */
/**
 * The inputs answered by a **flow**, by name, as the server reads them.
 *
 * Names and not ids: a definition is a thing that moves between houses, and the
 * flow's id is Node-RED's to assign per installation -- it is the *installing*
 * side that pushes the flow and records the id it got back.
 */
export function castFlowAnswers(decisions: readonly InputDecision[]): string[] {
  return decisions
    .filter((decision) => castModeOf(decision) === "nodered")
    .map((decision) => decision.input.name);
}

/**
 * The inputs answered by a **script**, by name, and the script each names.
 *
 * A mapping rather than the list the flows are, and the difference is which half
 * the server can fill in: a flow is *pushed* at save and its id is minted then, so
 * a name is the whole of what the screen knows. A script already exists -- it is
 * the person's own and Open House never writes one -- so the id is the whole of
 * what the screen has to say, and a name with no id behind it is left out rather
 * than sent, because a call to a script nobody named is a module that cannot run.
 */
export function castScriptAnswers(
  decisions: readonly InputDecision[],
): Record<string, string> {
  const found: Record<string, string> = {};
  for (const decision of decisions) {
    if (castModeOf(decision) !== "script") continue;
    const script = (decision.script ?? "").trim();
    if (script) found[decision.input.name] = script;
  }
  return found;
}

/**
 * The rows answered with logic, as the *read* is told about them: names, by kind.
 *
 * The save sends the payloads beside these (`castAnswers`, `castFlowAnswers`,
 * `castScriptAnswers`) because it is writing them down. The read only has to know
 * *which* rows hold a worked-out value rather than a typed one -- that is what
 * decides whether the row is offered as one more thing the module publishes --
 * so the names are the whole of it, and sending conditions and script ids along
 * would be handing the server a payload it has no use for.
 *
 * A script row with no id behind it is left out of all three, because nothing
 * answers it: a row that is showing the script editor and has not picked one is
 * a person who has not decided yet.
 */
export function castRowNames(decisions: readonly InputDecision[]): {
  casts: string[];
  flows: string[];
  scripts: string[];
} {
  return {
    casts: Object.keys(castAnswers(decisions)),
    flows: castFlowAnswers(decisions),
    scripts: Object.keys(castScriptAnswers(decisions)),
  };
}

export function castModeOf(decision: InputDecision): CastMode {
  if (decision.castMode) return decision.castMode;
  return decision.cast !== undefined && decision.cast.trim() ? "template" : "none";
}

/**
 * The condition a row is answered with, or `null` for a row that has none.
 *
 * Null covers three cases that are one: the row is not showing the condition
 * editor, the editor is open and empty, or what it holds is not a condition at
 * all. "Empty" is asked of the *value* and not of its length -- Home Assistant's
 * condition editor reports an empty answer as `[]`, an untouched one as `{}`,
 * and either is a person who has opened the builder and not built anything.
 */
export function castCondition(decision: InputDecision): unknown | null {
  if (castModeOf(decision) !== "condition") return null;
  return writtenCondition(decision.condition);
}

/**
 * Every condition answer the screen holds, by input name, as the server reads it.
 *
 * Only the ones actually written: a row showing the condition editor with
 * nothing in it is a person who has not answered, and sending an empty condition
 * would replace the choice above with a question that asks nothing.
 */
export function castAnswers(decisions: readonly InputDecision[]): Record<string, unknown> {
  const found: Record<string, unknown> = {};
  for (const decision of decisions) {
    const condition = castCondition(decision);
    if (condition !== null) found[decision.input.name] = condition;
  }
  return found;
}

/**
 * The menu a row's cast offers, which is not the same menu on every row.
 *
 * `castModeSelector` is the components module's, beside the other half of the
 * cast -- what an empty condition means and what a template cast binds to -- so
 * the card and this screen offer one menu rather than two that agree today.
 */


/** `"lights_evening_scene/gate_open"` as its two halves. */
export function splitOutput(value: unknown): [string, string] {
  if (typeof value !== "string") return ["", ""];
  const at = value.indexOf(OUTPUT_SEPARATOR);
  if (at <= 0) return ["", ""];
  return [value.slice(0, at), value.slice(at + 1)];
}

/**
 * The control an input's value is typed into, or `null` for a choice that has
 * no value of its own.
 *
 * A `target` gets the entity picker, for the reason `websocket_api._selector_kind`
 * reports it as one: what a person binds is a device, and the difference is in
 * how the value is wrapped, which the server knows and this does not.
 */
export function valueSelector(
  decision: InputDecision,
  hosted: readonly HostedModule[],
  slots: readonly ModuleSlotWord[],
): Record<string, unknown> | null {
  if (decision.how === LEAVE) return null;
  if (decision.how === "entity") {
    // A `target` that names several -- the ellipse a blueprint asks a room's
    // lights with -- gets the control that takes several. One control for both
    // would either bind a list where one id belongs or offer one box for a
    // question about three lights.
    return { entity: decision.input.multiple ? { multiple: true } : {} };
  }
  if (decision.how === "slot" || decision.how === "global_slot") {
    // **A menu you can also type into.** The list is every slot this house
    // carries, so the common case is picking one; `custom_value` is the other
    // half of what was asked for -- a person who already knows the name types
    // it rather than scrolling. What typing does *not* do is invent a word: the
    // name still has to be one the house carries, or there is no slot to bind
    // and the module would wait for a device it could never be given.
    //
    // **A global slot offers only the roles the house answers.** Every slot may
    // be bound in a room, but only a house-scope role is resolved *by the house*
    // -- so naming one of the others globally would promise a resolution nothing
    // makes, and the module would wait for a device it could never be given.
    // Offering the two lists separately is what makes the choice honest.
    const offered =
      decision.how === "global_slot"
        ? slots.filter((slot) => slot.house_scope)
        : slots;
    return {
      select: {
        mode: "dropdown",
        custom_value: decision.how === "slot",
        options: offered.map((slot) => ({ value: slot.name, label: slot.label })),
      },
    };
  }
  if (decision.how === "output") {
    return {
      select: {
        mode: "dropdown",
        options: hostedOutputs(hosted).map((option) => ({
          value: option,
          label: option,
        })),
      },
    };
  }
  if (decision.how === "template") {
    // The template editor, which is Home Assistant's own: a box that knows
    // Jinja, offers the entities and states this installation has, and shows
    // what it renders to. A plain text box would take the same string and give
    // nobody a way to find out whether it works.
    return { template: {} };
  }
  return bySelector(decision.input);
}

/** Every output every hosted module publishes, spelled `"<module>/<key>"`. */
export function hostedOutputs(hosted: readonly HostedModule[]): string[] {
  const found: string[] = [];
  for (const module of hosted) {
    for (const output of module.outputs) {
      found.push(`${module.slug}${OUTPUT_SEPARATOR}${output.key}`);
    }
  }
  return found;
}

/**
 * Whether an input's own selector names a **device** rather than a value.
 *
 * A blueprint's `entity` and `target` selectors both take a device; the server
 * reports a target as an entity for the same reason (`websocket_api._selector_kind`),
 * and the two differ only in how the value is wrapped when it is written into the
 * automation (`module_host._bound_value`).
 */
export function isDeviceInput(input: ModuleInputRow): boolean {
  return input.selector === "entity" || input.selector === "target";
}

/**
 * Every answer a row may be filled with, and what each one is called.
 *
 * One table rather than a list built at each of the places that draw it: the
 * import screen draws this menu, and a row that becomes a setting is drawn again
 * on the module's card. Two lists that agree today are two lists that disagree
 * the first time one of them is edited.
 */
const HOWS: readonly { value: How; label: string }[] = [
  // **First because it is what most inputs want.** "Blueprint default" is the
  // answer that keeps an input an option of the thing being made rather than a
  // decision taken here: the blueprint's own default is where the option starts,
  // and where there is no default the module is saved and waits for somebody to
  // set it rather than being held back at import (`modules._async_build`, which
  // waits rather than refuses).
  { value: "default", label: "Blueprint default" },
  { value: "literal", label: "Type a value" },
  { value: "entity", label: "Pick a device" },
  { value: "slot", label: "A slot in the module's room" },
  // **The house's own device, in every room.** A global slot is answered by the
  // house's binding rather than the room's, so a room that lights itself with
  // something else does not shadow it -- which is the whole difference, and the
  // reason a person picks it deliberately rather than it being what a slot
  // quietly means.
  { value: "global_slot", label: "A global slot (the whole house)" },
  { value: "output", label: "Another module's output" },
  // **Anything Home Assistant can work out**, which is what a person reaches for
  // when the answer is not one device: a state read off a helper and cast to an
  // on/off, a name chosen by an if, a value parsed out of a sensor. It fills a
  // slot-answered input the same way it fills a device one -- there is one of
  // these for each, because "the room's lux sensor, or whatever that template
  // works out" is a real pair of choices and the second is not a device.
  { value: "template", label: "A condition or template I write" },
];

/**
 * The menu one row offers, which is not the same menu on every row.
 *
 * **A device input is answered with a slot and nothing else.** A blueprint that
 * asks for an entity is asking about a device, and a module's whole point is that
 * it is not one house's device: an input answered with a slot follows whatever
 * room the module lands in, and is the only answer that can be handed to somebody
 * else's house at all. So the two slot answers are what such a row offers -- no
 * value to type, no device to pick, no kept setting -- and a row that cannot say
 * which slot it means is a row that has not been answered.
 *
 * **A row already holding an answer this menu no longer offers still shows it.**
 * Modules imported before this rule hold devices, and an edit that hid the answer
 * one has would be an edit that silently rewrites it on the next save -- so the
 * option comes back beside the two slots, and the person can keep it or move it
 * to a slot themselves. Nothing here offers a *new* device answer.
 */
export function howSelector(
  input: ModuleInputRow,
  current: How,
): Record<string, unknown> {
  const offered = isDeviceInput(input)
    ? HOWS.filter((row) => row.value === "slot" || row.value === "global_slot")
    : HOWS;
  const shown =
    current === LEAVE || offered.some((row) => row.value === current)
      ? offered
      : [...offered, ...HOWS.filter((row) => row.value === current)];
  return {
    select: {
      mode: "dropdown",
      options: shown.map((row) => ({ value: row.value, label: row.label })),
    },
  };
}

/**
 * The label of the field `name` belongs to: the row's title, not the form's.
 *
 * A row is up to three fields, and each one is labelled for the question it
 * asks rather than with the input's name alone. The `how` dropdown answers "what
 * fills this", so it takes the name; the value field takes "… value"; and the
 * Keep switch says what keeping *does*, because a switch labelled only with the
 * input's name reads as a switch for the input -- "turn Lux Sensor off" -- when
 * what it does is leave the input settable on the module afterwards. The label
 * is the whole of how that is told to a person: the switch carries no other
 * text, and a section's paragraph cannot say which of eighteen switches it is
 * about.
 */
export function labelFor(name: string, how?: How, mode?: CastMode): string {
  // Each field is labelled for the question it asks, and the input's *name* is
  // not repeated here: it is the row's own title, above the form, in the same
  // weight as every other heading on the page. What is left for these labels is
  // what each control does -- the dropdown answers "what fills this", the second
  // field is the value itself, and the switch says what keeping does, because a
  // switch labelled only "Option" reads as a switch for nothing in particular.
  //
  // The second field is named for what it holds when that is not simply a
  // value: a template is a piece of logic a person writes, and "Value" over a
  // Jinja box is the wrong word for it. `how` is optional so the field name
  // alone still reads, which is all a caller that has not decided yet can give.
  if (name.startsWith("value_")) {
    // Drawn only when no cast is on, so there is nothing here to say about a
    // flow: a row cast to one shows the flow in the field's place rather than
    // this field relabelled.
    return how === "template" ? "Condition or template" : "Value";
  }
  // The cast menu asks what to cast the answer *to*, and the editor under it is
  // named for which one it is: a person looking at a numbered box under a
  // condition builder should not have to work out from the box alone that they
  // are not looking at a template.
  if (name.startsWith("cast_mode_")) return "Cast it";
  if (name.startsWith("cast_")) {
    if (mode === "condition") return "The condition";
    if (mode === "template") return "The template";
    return "The cast";
  }
  if (name.startsWith("script_")) return "The script that answers it";
  if (name.startsWith("expose_")) return "Keep as a setting";
  return "What fills it";
}

/** The index a `how_3` / `value_3` field name carries, or -1 for neither. */
export function fieldIndex(name: string): number {
  const at = name.indexOf("_");
  if (at < 0) return -1;
  const index = Number(name.slice(at + 1));
  return Number.isInteger(index) ? index : -1;
}

function capitalise(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

declare global {
  interface HTMLElementTagNameMap {
    "open-house-host-module": HostModuleScreen;
  }
}

if (!customElements.get("open-house-host-module")) {
  customElements.define("open-house-host-module", HostModuleScreen);
}
