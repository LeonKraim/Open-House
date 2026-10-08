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
 * explains nothing. An address that is set and does *not* answer gets a sentence
 * too, for the same reason and one more: the rectangle it would otherwise draw is
 * the browser's own error page, which the panel cannot tell from a fault of its
 * own. So the editor is embedded only once the address has answered.
 */

import { html, type TemplateResult } from "lit";
import { OpenHouseElement } from "../base.ts";
import { banner } from "./suppression.ts";

/**
 * How long the address is given to answer before it is called unreachable.
 *
 * Node-RED's landing page is a local document and answers in milliseconds; this
 * is only long enough to forgive a box that is waking up, and short enough that
 * a person is not left looking at "looking for the editor" for long. It is not a
 * read of the editor -- an unreachable address that answers later is reached
 * again by pressing Reload, which is what the sentence tells them to do.
 */
const PROBE_MS = 8000;

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
    // Whether the address answered, once it has been asked: `null` while the
    // question is out. Reactive, because the answer is what decides whether an
    // iframe is drawn at all.
    reachable: { state: true },
  };

  /** Left undefined by a caller that has no address to pass, which asks for one. */
  declare url?: string;
  declare flow: string;
  declare label: string;

  private reloads = 0;
  private reachable: null | boolean = null;
  /**
   * The address an answer is already held for.
   *
   * Plain rather than reactive because nothing is drawn from it: it is the guard
   * that stops the probe running again for an address it has already been asked
   * about, which `updated` would otherwise do on every render that touched `url`.
   */
  private probedFor = "";

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
    this.reachable = null;
  }

  override connectedCallback(): void {
    super.connectedCallback();
    // Nothing to fetch when the caller already knows, either way: an address is
    // used as given, and an empty string is a screen that has asked and been told
    // there is none.
    if (this.url === undefined && !this.asking) void this.loadUrl();
  }

  /**
   * Ask the address whether anything is listening, when there is a new one.
   *
   * Run from `updated` rather than from the places that set `url`, because there
   * are two of them and they are on different sides of this element: the
   * capabilities read here, and a caller that passes the address down
   * (`tabs/host-module.ts` does). One hook that watches the property is one place
   * that cannot be forgotten by the next caller.
   */
  protected override updated(changed: Map<string, unknown>): void {
    if (changed.has("url")) void this.probe();
  }

  /**
   * Whether the address answers at all, which is not the same question as
   * whether it is set.
   *
   * **An iframe pointed at a host that is not there does not come up empty.** It
   * comes up as the browser's own error page -- "this site can't be reached" --
   * inside the panel, under this panel's label, which reads as Open House having
   * broken rather than as the editor being down. So the address is asked first
   * and the editor is embedded only if something answers.
   *
   * The question is a `no-cors` GET, and it is deliberately one the page throws
   * away: what is being asked is whether the address resolves and accepts a
   * connection, not what it says. `no-cors` is what makes that a question a
   * browser will ask across origins at all, and it is also the reason a refusal
   * is meaningful -- the answer is opaque and ignored, so the only thing that can
   * reject is the request failing to reach the address at all.
   */
  private async probe(): Promise<void> {
    const url = this.url;
    if (!url || this.probedFor === url) return;
    this.probedFor = url;
    this.reachable = null;
    const control = new AbortController();
    const stop = setTimeout(() => control.abort(), PROBE_MS);
    try {
      await fetch(url, {
        mode: "no-cors",
        cache: "no-store",
        signal: control.signal,
      });
      if (this.probedFor === url) this.reachable = true;
    } catch {
      if (this.probedFor === url) this.reachable = false;
    } finally {
      clearTimeout(stop);
    }
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
    // A reload is also the way *back* from an address that did not answer: the
    // box may have come up since, and there is no iframe to re-navigate when
    // there is none. Pressed while the editor is up, it is only a re-navigation
    // -- asking again would take the working editor away to ask a question whose
    // answer is already on the screen.
    if (this.reachable === false) {
      this.probedFor = "";
      void this.probe();
    }
  }

  override render(): TemplateResult {
    // **Not asked for yet, so nothing is said yet.** `url` is undefined until the
    // capabilities read lands, and the "set an address" sentence drawn on it
    // flashed at every house that has one for the length of the round trip.
    if (this.url === undefined) return html``;
    if (!this.url) {
      return html`<p class="help" data-node-red>
        Set the Node-RED address in this integration's options and its editor
        is embedded here.
      </p>`;
    }
    return html`<div class="embed" data-node-red>
      ${this.renderBar()}
      ${this.reachable === null
        ? html`<p class="help" style="margin:8px 10px">
            Looking for the editor at <code>${this.url}</code>...
          </p>`
        : this.reachable
          ? html`<iframe
              title=${this.label || "Node-RED"}
              src=${this.source}
              referrerpolicy="no-referrer"
            ></iframe>`
          : html`<div style="padding:8px 10px">
              ${banner(
                "warn",
                html`The editor at <code>${this.url}</code> did not answer, so
                  it is not embedded here. It may be down, or the address may be
                  one this page cannot reach at all -- a panel served over https
                  cannot load an editor that is not. Both are worth a look, and
                  Reload asks again.`,
              )}
            </div>`}
    </div>`;
  }

  /** The row above the editor: what it is, and the two ways to it. */
  private renderBar(): TemplateResult {
    return html`<div class="embed-bar">
      <span class="label">${this.label || "Node-RED"}</span>
      <span class="embed-actions">
        <button type="button" @click=${() => this.reload()}>Reload</button>
        <a href=${this.source} target="_blank" rel="noreferrer"
          >Open its own tab</a
        >
      </span>
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
