/**
 * Node-RED's own editor, in the page, on the flow that belongs to this input.
 *
 * **The editor is Node-RED's and is not reimplemented here.** It is a whole
 * application -- a palette, a canvas, a deploy button, a debug sidebar -- and a
 * second one drawn in this panel would be a different program that agreed with
 * Node-RED only until it did not. So this is an `<iframe>` of the real thing, at
 * the address the integration is configured with, opened on the flow's own tab
 * when there is one.
 *
 * **Embedded rather than linked, and that is the whole point of it.** A link is
 * a tab the person has to find again, and the trip back is where the work loses
 * the thread: what they are building is *this* module's input, and the editor
 * that builds it belongs beside the row that names it. The same editor is one
 * click away in its own tab as well, for a person who wants the room -- the
 * address is the iframe's own `src`, so the link cannot drift from it.
 *
 * **What it shows depends on when it is drawn.** A flow is pushed when the
 * module is *saved*, and its tab id is Node-RED's to mint at that moment, so a
 * row only just switched to this cast has no id to open on and gets the editor
 * at its landing page. After a save the row holds the id and the iframe opens on
 * that tab.
 *
 * A house with no Node-RED address set gets the sentence saying where to set
 * one, because an iframe pointed at no address is a blank rectangle that
 * explains nothing.
 */

import { html, type TemplateResult } from "lit";
import { OpenHouseElement } from "../base.ts";

export class NodeRedEditor extends OpenHouseElement {
  static override properties = {
    ...OpenHouseElement.properties,
    // The address a **browser** opens Node-RED at. **Unset asks for it** from
    // the integration's capabilities; an empty string is a caller that already
    // asked and was told there is none, and is not asked again.
    url: { attribute: false },
    // Node-RED's id for the flow's tab, or empty for the editor's landing page.
    flow: { attribute: false },
    // What the block is called above the embed.
    label: { attribute: false },
    // Bumped by *Reload*, and the only reason the iframe ever re-navigates. A
    // reactive property rather than a plain field because a button is what
    // changes it, and a click that assigns a plain field schedules no render.
    reloads: { state: true },
  };

  /** Left undefined by a caller that has no address to pass, which asks for one. */
  declare url?: string;
  declare flow: string;
  declare label: string;

  private reloads = 0;

  /**
   * Whether a capabilities read is in flight, so a reconnect does not start a
   * second one.
   *
   * Not reactive: nothing is drawn differently while the address is being
   * fetched -- the element is blank either way -- and a property that only
   * exists to stop a duplicate would be a render for no reader.
   */
  private asking = false;

  constructor() {
    super();
    this.flow = "";
    this.label = "";
  }

  override connectedCallback(): void {
    super.connectedCallback();
    // Nothing to fetch when the caller already knows, either way: an address is
    // used as given, and an empty string is a screen that has asked and been told
    // there is none.
    if (this.url === undefined && !this.asking) void this.loadUrl();
  }

  /**
   * Ask the integration where Node-RED is, once.
   *
   * Read here rather than passed down from every screen that draws a flow: the
   * address is one value for the whole panel, and threading it through the cards
   * and the tabs would be the same fact restated at every level between this
   * element and the panel -- each restatement a place it could go missing.
   */
  private async loadUrl(): Promise<void> {
    this.asking = true;
    try {
      const capabilities = await this.requireClient().capabilities();
      this.url = capabilities.node_red_url ?? "";
    } catch {
      // A panel that cannot read its own capabilities has no address to embed
      // and nothing worth an error banner over: the row above still says what
      // the flow writes, and the editor is missing rather than broken.
      this.url = "";
    } finally {
      this.asking = false;
      this.requestUpdate();
    }
  }

  /**
   * The address the iframe loads: the editor, on the flow's tab, on the reload's
   * nonce.
   *
   * The nonce is a query parameter Node-RED ignores. It is there because
   * assigning an iframe the `src` it already has does nothing -- the browser
   * reads it as the same navigation and keeps the document it holds -- so a
   * reload that re-set the same string would be a button that did nothing on the
   * one occasion it is pressed: right after a save pushed a flow that was not
   * there when the iframe loaded.
   */
  private get source(): string {
    if (!this.url) return "";
    const base = this.url.replace(/\/+$/, "");
    const tab = this.flow ? `#flow/${this.flow}` : "";
    return `${base}/?oh=${this.reloads}${tab}`;
  }

  /** Bump the nonce, which is what makes the browser navigate again. */
  private reload(): void {
    this.reloads += 1;
  }

  override render(): TemplateResult {
    if (!this.url) {
      return html`<p class="help" data-node-red>
        Set the Node-RED address in this integration's options and its editor
        is embedded here.
      </p>`;
    }
    return html`<div class="embed" data-node-red>
      <div class="embed-bar">
        <span class="label">${this.label || "Node-RED"}</span>
        <span class="embed-actions">
          <button type="button" @click=${() => this.reload()}>Reload</button>
          <a href=${this.source} target="_blank" rel="noreferrer"
            >Open its own tab</a
          >
        </span>
      </div>
      <iframe
        title=${this.label || "Node-RED"}
        src=${this.source}
        referrerpolicy="no-referrer"
      ></iframe>
    </div>`;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "open-house-node-red": NodeRedEditor;
  }
}

if (!customElements.get("open-house-node-red")) {
  customElements.define("open-house-node-red", NodeRedEditor);
}
