/**
 * The notice a page shows when the house it was rendered from has moved on.
 *
 * A room page and the House page both draw controls whose values were decided
 * against the house profile in force when the page was read. Switch the house
 * profile -- in another tab, or from the Rooms tab while this one is open -- and
 * every one of those controls is now holding an answer from a profile that is
 * no longer in force. Applying it would land it over the profile that is, which
 * is the bug this whole mechanism exists to stop: a background save loop
 * overwriting a good configuration with the settings of an old one.
 *
 * So the page stops being the live page. It is not *wrong* -- it is what the
 * house looked like -- but nothing on it may be acted on, because the server
 * refuses the write and the person would otherwise read the refusal as the
 * panel being broken. Reloading is the one thing that helps, so reloading is the
 * one thing offered.
 *
 * ## Why this is not `open-house-dialog`
 *
 * The dialog is dismissible by design: Escape, a click on the backdrop, and a
 * Close button all shut it, and the flows it was built for are ones a person
 * may come back to. This is the opposite. Escape must not clear it, a click
 * outside must not clear it, and there is no Close: the only way back is to
 * reload, and a notice somebody can dismiss leaves them on a page that silently
 * refuses every write. It renders into the light DOM like every element here
 * except the dialog, so the panel's one stylesheet reaches it.
 */

import { html, LitElement, type TemplateResult } from "lit";

export class OpenHousePageStale extends LitElement {
  static override properties = {
    open: { type: Boolean, reflect: true },
    reason: { type: String },
  };

  declare open: boolean;
  /** What the page was showing when it went stale, if the owner knows. */
  declare reason: string;

  constructor() {
    super();
    this.open = false;
    this.reason = "";
  }

  /** No shadow root: the panel's stylesheet styles this, like everything else. */
  protected override createRenderRoot(): HTMLElement | DocumentFragment {
    return this;
  }

  protected override render(): TemplateResult {
    if (!this.open) return html``;
    return html`
      <div class="stale-backdrop" role="alertdialog" aria-modal="true">
        <div class="stale-sheet">
          <h2>This page has been disabled because you switched your house profile. Please reload.</h2>
          ${this.reason ? html`<p class="muted">${this.reason}</p>` : null}
          <p class="help">
            What this page was showing was read from the profile that was in
            force when it was opened. Writing it now would put those answers
            over the profile you switched to, so this page has stopped
            accepting changes.
          </p>
          <button
            type="button"
            class="primary"
            autofocus
            @click=${() => window.location.reload()}
          >
            Reload
          </button>
        </div>
      </div>
    `;
  }
}

if (!customElements.get("open-house-page-stale")) {
  customElements.define("open-house-page-stale", OpenHousePageStale);
}
