/**
 * Home Assistant's own automation editor, in the page, behind one button.
 *
 * **The editor is Home Assistant's and is not reimplemented here.** An automation
 * is a list of triggers, conditions and actions, each built from a whole catalogue
 * of building blocks -- the editor that builds one is a page: a header, a sidebar,
 * a trace viewer, a picker per step. A second one drawn in this panel would be a
 * different program that agreed with Home Assistant only until it did not.
 *
 * **Embedded rather than linked, and that is the whole point of it.** A link is a
 * tab the person has to find their way back from, and the trip back is where the
 * work loses the thread: what they are building is *this* module's input, and the
 * automation that answers it belongs beside the row that names it. The same editor
 * is one click away in its own tab as well, for a person who wants the room.
 *
 * **In a dialog, and the difference is the size of the page** -- the reason
 * `node-red-editor.ts` gives, and it is the same page: crammed into a settings row
 * it is a page nobody can work in, so it opens over the screen instead, which is
 * still *in* the screen and not a tab somewhere else.
 *
 * **The automation is Open House's to make, and this element only opens it.**
 * The id, the alias and the action that sets the helper are written when the
 * module is built (`modules._async_seed_automations`), so there is nothing here
 * for the person to name and nothing here that can mint one: the id is handed in
 * and the editor opens on *that* automation. That is also why this element
 * dispatches no event -- a script's id is the person's to choose, and a row that
 * changed it would be naming an automation the module's own seeding does not
 * cover; an automation's id is a fact about the module.
 *
 * A row that has no id yet -- an input answered with this cast on the import
 * screen, where the module does not exist and so neither does the automation --
 * draws the sentence that says what saving will make, rather than an editor
 * opened on nothing.
 */

import { html, nothing, type TemplateResult } from "lit";
import { OpenHouseElement } from "../base.ts";

export class AutomationEditor extends OpenHouseElement {
  static override properties = {
    ...OpenHouseElement.properties,
    // The automation this row answers with: Open House's own id, minted when the
    // module was built, and empty for a row whose module has never been built.
    automation: { attribute: false },
    // The helper the input is bound to, so the person can see which entity their
    // automation is the one that writes.
    entity: { attribute: false },
    // What the block is called above the embed.
    label: { attribute: false },
    // Whether the editor is open over the screen. A button is what changes it, so
    // it is reactive: a click that assigned a plain field would draw nothing.
    open: { state: true },
  };

  declare automation: string;
  declare entity: string;
  declare label: string;
  open = false;

  /**
   * Bumped by everything that opens or closes the editor, and read back after the
   * await in `reload` -- the same stamp, and the same reason, as the script
   * editor's: a reload takes two renders, and a person's finger can close the
   * editor inside the gap between them.
   */
  private generation = 0;

  constructor() {
    super();
    this.automation = "";
    this.entity = "";
    this.label = "";
  }

  /**
   * The address the iframe loads: the automation's own page.
   *
   * **Not `new`, unlike the script editor.** A new page would let the person
   * create a *second* automation answering this input -- and the one Open House
   * seeded, carrying the alias and the action that sets the helper, would be the
   * one nobody opened. So there is no editor until there is an id, and the id is
   * the seeded one.
   */
  private get source(): string {
    return `/config/automation/edit/${this.automation}`;
  }

  private toggle(): void {
    if (this.open) {
      this.open = false;
      return;
    }
    this.generation += 1;
    this.open = true;
  }

  /**
   * Reload the frame by taking it away and putting it back.
   *
   * The stamp is read before the frame goes and checked after it is back: what the
   * await is waiting for is the render that removed it, and anything that opened
   * or closed the editor in between has answered this already.
   */
  private async reload(): Promise<void> {
    const generation = ++this.generation;
    this.open = false;
    await this.updateComplete;
    if (this.generation !== generation) return;
    this.open = true;
  }

  /** What the bar says the row is looking at: the helper, or the id alone. */
  private get bound(): string {
    return this.entity ? `writes ${this.entity}` : `automation.${this.automation}`;
  }

  override render(): TemplateResult {
    // **No automation yet, and that is a state rather than an error.** The module
    // does not exist until it is saved and the automation is made with it -- so
    // there is nothing to open, and the sentence says what saving will make
    // instead of an editor opened onto nothing.
    if (!this.automation) {
      return html`<p class="help" data-automation-pending>
        Open House makes this automation when the module is saved, with the action
        that sets the helper already in it -- so the row reads a real value from
        the first moment. Save the module, open it again, and this row writes the
        trigger beside the action that is already there.
      </p>`;
    }
    return html`<div class="embed" data-automation-editor>
        <div class="embed-bar">
          <span class="label">${this.label || "The automation"}</span>
          <span class="embed-actions">
            <span class="muted">${this.bound}</span>
            <button type="button" @click=${() => this.toggle()}>
              ${this.open ? "Close the editor" : "Edit the automation"}
            </button>
          </span>
        </div>
      </div>
      ${this.open
        ? html`<div
            class="embed-modal"
            role="dialog"
            aria-label=${this.label || "The automation"}
          >
            <div class="sheet">
              <div class="embed-bar">
                <span class="label">${this.label || "The automation"}</span>
                <span class="embed-actions">
                  <a href=${this.source} target="_blank" rel="noreferrer"
                    >Open its own tab</a
                  >
                  <button type="button" @click=${() => void this.reload()}>
                    Reload
                  </button>
                  <button type="button" @click=${() => this.toggle()}>Done</button>
                </span>
              </div>
              <iframe
                title=${this.label || "Home Assistant's automation editor"}
                src=${this.source}
                referrerpolicy="no-referrer"
              ></iframe>
            </div>
          </div>`
        : nothing}
      <p class="help">
        Home Assistant's own automation editor, embedded. The action that sets
        <code>${this.entity || "the helper"}</code> is already in it -- what it
        needs is a trigger, so that it runs whenever the house says. Whatever it
        writes is the value this input is built from.
      </p>`;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "open-house-automation": AutomationEditor;
  }
}

if (!customElements.get("open-house-automation")) {
  customElements.define("open-house-automation", AutomationEditor);
}
