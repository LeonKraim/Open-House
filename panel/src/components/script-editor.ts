/**
 * Home Assistant's own script editor, in the page, behind one button.
 *
 * **The editor is Home Assistant's and is not reimplemented here.** A script is a
 * sequence -- an if, a loop, a wait, a call to something else and a value handed
 * back at the end -- and the editor that builds one is a whole page: a header, a
 * sidebar, a trace viewer. A second one drawn in this panel would be a different
 * program that agreed with Home Assistant only until it did not.
 *
 * **Embedded rather than linked, and that is the whole point of it.** A link is a
 * tab the person has to find their way back from, and the trip back is where the
 * work loses the thread: what they are building is *this* module's input, and the
 * script that answers it belongs beside the row that names it. The same editor is
 * one click away in its own tab as well, for a person who wants the room.
 *
 * **In a dialog, unlike Node-RED, and the difference is the size of the page.**
 * Node-RED's editor is a canvas that fills whatever box it is given, so 68vh of
 * it inside the row is a usable editor. Home Assistant's script editor is a whole
 * frontend page with its own chrome, and a page that size crammed into a settings
 * row is a page nobody can work in -- so it opens over the screen instead, which
 * is still *in* the screen and not a tab somewhere else.
 *
 * **A new script learns its own name here.** Home Assistant's editor creates a
 * script the moment its Save is pressed, and the id is Home Assistant's to mint
 * -- so a person who starts from `/config/script/edit/new` has no id to hand
 * back, and the only place it exists is the address the editor navigated itself
 * to. Reading that address is the whole of how a just-created script is learned:
 * same origin, so the frame's own `location` may be read, and a frame that cannot
 * be read (an error page, a browser that refuses) leaves the answer empty rather
 * than throwing.
 */

import { html, nothing, type TemplateResult } from "lit";
import { OpenHouseElement } from "../base.ts";

/** What one row's script choice is told when the editor closes. */
export interface ScriptChosenDetail {
  /** The script's id -- `script.<id>` without the domain -- or `""`. */
  script_id: string;
}

export class ScriptEditor extends OpenHouseElement {
  static override properties = {
    ...OpenHouseElement.properties,
    // The script this row answers with, or empty for a row that has picked none
    // -- which opens the editor on a *new* one rather than on nothing.
    script: { attribute: false },
    // What the block is called above the embed.
    label: { attribute: false },
    // Whether the editor is open over the screen. A button is what changes it,
    // so it is reactive: a click that assigned a plain field would draw nothing.
    open: { state: true },
  };

  declare script: string;
  declare label: string;
  open = false;

  constructor() {
    super();
    this.script = "";
    this.label = "";
  }

  /**
   * The address the iframe loads: the script's own page, or a new script's.
   *
   * `new` is Home Assistant's own spelling for "the editor, with nothing in it
   * yet" -- the same route, and the page behind it creates the script when its
   * Save is pressed. That is why an empty `script` is not a refusal to open: a
   * row where nothing has been picked yet is exactly where a person needs the
   * editor most.
   */
  private get source(): string {
    return `/config/script/edit/${this.script || "new"}`;
  }

  /** Open, or close and tell the row which script the editor ended up on. */
  private toggle(): void {
    if (this.open) {
      this.close();
      return;
    }
    this.open = true;
  }

  /**
   * Close the editor, and say which script it was left on.
   *
   * Read *before* the frame goes, because the address is the frame's -- a new
   * script's id exists nowhere else, and a closed dialog that read it afterwards
   * would be reading an element that is no longer there. Dispatched whether or
   * not it changed: a person may have opened the editor, renamed the script and
   * typed in it, and the row saving the same id again is the same answer.
   */
  private close(): void {
    const script_id = this.frameScript();
    this.open = false;
    if (!script_id) return;
    this.dispatchEvent(
      new CustomEvent<ScriptChosenDetail>("script-chosen", {
        detail: { script_id },
        bubbles: true,
        composed: true,
      }),
    );
  }

  /**
   * The script the frame is showing, read off its own address.
   *
   * `""` for the editor still on `new` -- a person who opened it and typed but
   * never pressed Save has no script yet, and naming one would be naming a
   * script nothing holds. Reading another origin throws, and a browser that
   * refuses the read is a frame this cannot learn anything from; both are `""`
   * rather than an error, because the row is still perfectly answerable by hand.
   */
  private frameScript(): string {
    try {
      const frame = this.querySelector("iframe");
      const path = frame?.contentWindow?.location?.pathname ?? "";
      const found = /^\/config\/script\/edit\/([^/?#]+)/.exec(path);
      const id = found?.[1] ?? "";
      return id === "new" ? "" : id;
    } catch {
      return "";
    }
  }

  /** Reload the frame by taking it away and putting it back. */
  private async reload(): Promise<void> {
    this.open = false;
    await this.updateComplete;
    this.open = true;
  }

  override render(): TemplateResult {
    return html`<div class="embed" data-script-editor>
        <div class="embed-bar">
          <span class="label">${this.label || "The script"}</span>
          <span class="embed-actions">
            <span class="muted"
              >${this.script ? `script.${this.script}` : "no script yet"}</span
            >
            <button type="button" @click=${() => this.toggle()}>
              ${this.open
                ? "Close the editor"
                : this.script
                  ? "Edit the script"
                  : "Write a script"}
            </button>
          </span>
        </div>
      </div>
      ${this.open
        ? html`<div class="embed-modal" role="dialog" aria-label=${this.label || "The script"}>
            <div class="sheet">
              <div class="embed-bar">
                <span class="label">${this.label || "The script"}</span>
                <span class="embed-actions">
                  <a href=${this.source} target="_blank" rel="noreferrer"
                    >Open its own tab</a
                  >
                  <button type="button" @click=${() => void this.reload()}>
                    Reload
                  </button>
                  <button type="button" @click=${() => this.close()}>Done</button>
                </span>
              </div>
              <iframe
                title=${this.label || "Home Assistant's script editor"}
                src=${this.source}
                referrerpolicy="no-referrer"
              ></iframe>
            </div>
          </div>`
        : nothing}
      <p class="help">
        Home Assistant's own script editor, embedded. Whatever the script hands
        back with <code>stop</code> or its last step is the value this input is
        built from -- so it is worked out afresh every time the module runs.
      </p>`;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "open-house-script": ScriptEditor;
  }
}

if (!customElements.get("open-house-script")) {
  customElements.define("open-house-script", ScriptEditor);
}
