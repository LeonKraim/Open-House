/**
 * Detach: the logic one row is answered with becomes a module of its own.
 *
 * **The button sits beside the cast, and the dialog is where the two questions
 * a detach cannot answer for itself are asked.** A cast is a small piece of
 * logic attached to a row -- a condition, a template, a flow, or a script -- and
 * detaching it writes that same logic into a document of its own, hosts it as a
 * module, and points the row at what the module publishes. Three things travel
 * with the logic and two of them are not the person's to choose: the logic
 * itself, and *when it runs* and *where it lives*, which are new questions
 * because a cast rides whatever run its host already has and a module has none
 * of its own. So the dialog asks exactly those two, and the server decides the
 * rest.
 *
 * **"When it runs" is only asked where the cast cannot answer it.** A condition
 * names the entities it decides about and a flow writes an entity Open House
 * made, so both bring their own trigger and the field is a way to add to it. A
 * template and a script say nothing about when they should run -- nothing in
 * either can be read as "watch this" -- so for those two the field is the
 * difference between a module and a module that never runs, and the server
 * refuses one that is left empty rather than hosting something that would sit
 * there doing nothing.
 *
 * **The form is not the authority on whether it has enough.** What is enough is
 * a judgement about the cast (`cast_document.detached_document` makes it, where
 * the cast is in hand), so the dialog sends what it has and shows the refusal
 * the server gives back, in the server's own words. A screen that decided for
 * itself would be a second spelling of a rule that is already written once, and
 * the two would disagree in the case that matters -- a condition that names no
 * entity, which is a perfectly ordinary condition and one nothing can be
 * derived from.
 */

import { html, nothing, type TemplateResult } from "lit";
import { OpenHouseElement } from "../base.ts";

/** What a finished detach tells the row it was pressed on. */
export interface DetachedDetail {
  /** The module that now holds the logic. */
  module: string;
  title: string;
  room_id: string;
  /** The output key the row should now read. */
  key: string;
  /** What will start the new module, so the row can say so. */
  watched: string[];
}

/** The cast kinds that bring their own trigger, and so need none from a person. */
const SELF_STARTING = ["condition", "flow"];

export class DetachCast extends OpenHouseElement {
  static override properties = {
    ...OpenHouseElement.properties,
    // The module whose row the cast is on, by the name the house hosts it under.
    // The logic is read from that module's *record*, which is why the module's
    // name is what this needs and not the cast itself: a cast written into a
    // form and not yet saved is a cast the house does not hold, and there is
    // nothing to detach until it does.
    module: { attribute: false },
    moduleTitle: { attribute: false },
    // The row, by input name, and what to call it in the sentences. **One of the
    // two rows, and which is a fact about where the button was**: an input's cast
    // is kept on the module's record and a slot's rule in the session's settings,
    // so the server reads them through two different paths -- and the dialog
    // says which one it was opened on rather than making the server guess.
    input: { attribute: false },
    slot: { attribute: false },
    inputTitle: { attribute: false },
    // The cast the row is answered with. Read to decide what to ask: a condition
    // and a flow start themselves, a template and a script need telling.
    cast: { attribute: false },
    // The module's room, for the "this room" placement -- and empty for a
    // module of the house's, which has no room for a copy of it to go in.
    roomId: { attribute: false },
    roomName: { attribute: false },
    // Open, busy and failed are the screen's own state: a click changes them and
    // nothing else does.
    open: { state: true },
    busy: { state: true },
    failed: { state: true },
    title: { state: true },
    where: { state: true },
    trigger: { state: true },
    // What the detach did, once it has. The dialog stays up to say so rather
    // than closing on success, and this is why: the report is the one thing
    // here a person cannot check for themselves from the row they pressed the
    // button on -- what the new module watches is derived from the cast, so for
    // a condition it is something nobody typed anywhere.
    done: { state: true },
  };

  declare module: string;
  declare moduleTitle: string;
  declare input: string;
  declare slot: string;
  declare inputTitle: string;
  declare cast: string;
  declare roomId: string;
  declare roomName: string;
  declare open: boolean;
  declare busy: boolean;
  declare failed: string;
  declare title: string;
  declare where: "house" | "room";
  declare trigger: string[];
  declare done: DetachedDetail | null;

  constructor() {
    super();
    this.module = "";
    this.moduleTitle = "";
    this.input = "";
    this.slot = "";
    this.inputTitle = "";
    this.cast = "";
    this.roomId = "";
    this.roomName = "";
    this.open = false;
    this.busy = false;
    this.failed = "";
    this.title = "";
    this.where = "room";
    this.trigger = [];
    this.done = null;
  }

  /** The name the new module is offered: where the logic came from, and the row. */
  private get suggested(): string {
    return `${this.moduleTitle}: ${this.inputTitle || this.row}`;
  }

  /**
   * The row this dialog is about, in the words the sentences use.
   *
   * An input's *title* and a slot's *name* are the two things a person
   * recognises, and which one it is is which field the button was drawn from:
   * a slot row carries no `input` and an input row carries no `slot`.
   */
  private get row(): string {
    return this.input || this.slot;
  }

  /** Whether this cast needs a person to say what should run it. */
  private get needsAStart(): boolean {
    return !SELF_STARTING.includes(this.cast);
  }

  private begin(): void {
    this.title = this.suggested;
    // "The same room" is the placement a person means most of the time -- the
    // logic is answering a row of a module that lives somewhere -- so it is the
    // one the dialog opens on. A module of the house's own has no room to put a
    // copy in, and the choice is not offered at all rather than offered and
    // refused.
    this.where = this.roomId ? "room" : "house";
    this.trigger = [];
    this.failed = "";
    this.done = null;
    this.open = true;
  }

  /**
   * Close, and tell the row what happened if anything did.
   *
   * **The event is dispatched here rather than when the server answered**, so
   * that the report has been read first. The card answers it by dropping the
   * cast it was holding and asking the page for the house again -- which
   * replaces the card, and with it this dialog -- so a detach that told the row
   * the moment it succeeded would take its own sentence off the screen before
   * anybody read it.
   */
  private finish(): void {
    const done = this.done;
    this.open = false;
    this.done = null;
    if (!done) return;
    this.dispatchEvent(
      new CustomEvent<DetachedDetail>("detach-done", {
        detail: done,
        bubbles: true,
        composed: true,
      }),
    );
  }

  /**
   * Send the detach, and tell the row what came back.
   *
   * The server's refusal is shown rather than replaced: it is the sentence that
   * says *why* -- which entity a condition does not name, or that a script needs
   * something to start it -- and a message this screen made up would lose
   * exactly that.
   */
  private async detach(): Promise<void> {
    if (this.busy) return;
    this.busy = true;
    this.failed = "";
    try {
      const answer = await this.requireClient().modulesDetach(
        this.module,
        // The row, in whichever of the two spellings this dialog was opened on:
        // an input's cast and a slot's rule are the same act on two different
        // rows, and which one it is is a fact about the button that was pressed.
        this.input ? { input: this.input } : { slot: this.slot },
        this.title.trim() || this.suggested,
        this.where === "room" ? this.roomId : "",
        this.trigger,
      );
      // Held rather than announced: the row is told when this dialog is done,
      // and the dialog stays up to say what it did.
      this.done = {
        module: answer.module,
        title: answer.title,
        room_id: answer.room_id,
        key: answer.key,
        watched: answer.watched,
      };
    } catch (error) {
      this.failed = this.toError(error).message;
    } finally {
      this.busy = false;
    }
  }

  override render(): TemplateResult {
    return html`<div class="field">
        <div class="row wrap">
          <button
            type="button"
            data-detach=${this.row}
            ?disabled=${this.busy}
            @click=${() => this.begin()}
          >
            Detach...
          </button>
          <span class="muted"
            >Make what answers this row a module of its own.</span
          >
        </div>
      </div>
      ${this.open ? this.renderDialog() : nothing}`;
  }

  private renderDialog(): TemplateResult {
    const name = this.inputTitle || this.row;
    return html`<open-house-dialog
      .heading=${`Detach what answers ${name}`}
      .open=${true}
      @dialog-closed=${() => this.finish()}
    >
      ${this.done ? this.renderDone() : this.renderForm()}
    </open-house-dialog>`;
  }

  /**
   * What the detach did, in the dialog that did it.
   *
   * Three facts and they are the three a person cannot get from the row: what
   * the module is called, where it went, and what will start it. The last one is
   * the point of the report -- for a condition and a flow it is *derived*, from
   * entities the cast names and not from anything typed here, so the only way to
   * know it is to be told.
   */
  private renderDone(): TemplateResult {
    const done = this.done;
    if (!done) return html``;
    return html`<p class="help">
        The logic is a module of its own now, and this row reads what it
        publishes -- so it is worked out once, in one place, and anything in the
        house can use it.
      </p>
      <p data-detach-result>
        <strong>${done.title}</strong> is
        ${done.room_id ? `in ${this.roomName || done.room_id}` : "in the whole house"},
        and this row now reads <code>sensor.open_house_${done.module}_${done.key}</code>.
      </p>
      <p class="help">
        ${done.watched.length
          ? html`It runs when ${done.watched.map(
              (entity, index) => html`${index > 0 ? ", " : ""}<code>${entity}</code>`,
            )} changes -- change that on the new module, whose own "what should
              start it" field is where it lives.`
          : html`Nothing starts it yet, which cannot happen from this screen: a
              module with no trigger is refused rather than hosted.`}
      </p>
      <div class="row wrap">
        <button type="button" data-detach-done @click=${() => this.finish()}>
          Done
        </button>
      </div>`;
  }

  private renderForm(): TemplateResult {
    return html`<p class="help">
        The logic moves out of this row and becomes a module of its own, and this
        row then reads what that module publishes -- so the answer is worked out
        once, in one place, and can be used by anything in the house.
      </p>
      <div class="field">
        <div class="label-row"><span class="label">The new module's name</span></div>
        <input
          type="text"
          data-detach-title
          .value=${this.title}
          @input=${(event: Event) => {
            this.title = (event.target as HTMLInputElement).value;
          }}
        />
      </div>
      <div class="field">
        <div class="label-row"><span class="label">Where it sits</span></div>
        <div class="row wrap">
          ${this.roomId
            ? html`<button
                type="button"
                data-detach-where="room"
                aria-pressed=${this.where === "room"}
                @click=${() => {
                  this.where = "room";
                }}
              >
                ${this.roomName || "This room"}
              </button>`
            : nothing}
          <button
            type="button"
            data-detach-where="house"
            aria-pressed=${this.where === "house"}
            @click=${() => {
              this.where = "house";
            }}
          >
            The whole house
          </button>
        </div>
        <p class="help">
          A room's module answers that room's slots; the house's answers the
          house's own.
        </p>
      </div>
      ${this.renderTrigger()}
      ${this.failed ? html`<div class="banner error" role="alert">${this.failed}</div>` : nothing}
      <div class="row wrap">
        <button type="button" data-detach-confirm ?disabled=${this.busy} @click=${() => void this.detach()}>
          ${this.busy ? "Detaching..." : "Make it a module"}
        </button>
        <button
          type="button"
          ?disabled=${this.busy}
          @click=${() => this.finish()}
        >
          Cancel
        </button>
      </div>`;
  }

  /**
   * What should start the new module.
   *
   * One field for both readings, because it is one field on the wire and the
   * server decides what it is worth: for a condition or a flow it is *beside*
   * what the cast already watches, and for a template or a script it is the
   * only thing that will ever start the module. The words change with the cast
   * and the rule does not, which is why the two sentences are here and the
   * judgement is not.
   */
  private renderTrigger(): TemplateResult {
    return html`<div class="field">
      <div class="label-row">
        <span class="label"
          >${this.needsAStart
            ? "What should start it"
            : "Also watch (optional)"}</span
        >
      </div>
      <ha-form
        .hass=${this.hass}
        .data=${{ trigger: this.trigger }}
        .schema=${[
          { name: "trigger", selector: { entity: { multiple: true } } },
        ]}
        .computeLabel=${() =>
          this.needsAStart
            ? "The entities it should run on"
            : "Any others it should run on"}
        @value-changed=${(event: CustomEvent<{ value: { trigger?: string[] } }>) => {
          this.trigger = [...(event.detail.value.trigger ?? [])];
        }}
      ></ha-form>
      <p class="help">
        ${this.needsAStart
          ? html`A template and a script say nothing about when they should run,
              so a module built from one has nothing to start it without this. It
              then runs whenever one of these changes.`
          : html`What the ${
              this.cast === "condition" ? "condition" : "flow"
            } names is watched already. Anything picked here is watched as
              well, which is how a module that is also expected to run at a
              moment of its own gets one.`}
      </p>
    </div>`;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "open-house-detach": DetachCast;
  }
}

if (!customElements.get("open-house-detach")) {
  customElements.define("open-house-detach", DetachCast);
}
