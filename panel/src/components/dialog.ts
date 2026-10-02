/**
 * A modal, for the flows that must not be left half-finished.
 *
 * Installing a module changes the house, so it opens in a modal rather than
 * expanding inline: the user should not be able to wander to another tab
 * halfway through a conflict check and come back to a form that has silently
 * moved on. The element traps Escape and a click on the backdrop, restores focus
 * to whatever opened it, and marks the rest of the page inert to assistive
 * technology while it is up.
 *
 * ## Why this one element keeps a shadow root
 *
 * Every other element in this bundle renders into the light DOM, so that a
 * single stylesheet injected once by the root panel element reaches all of them
 * (`styles.ts`). This one cannot, and the reason is the whole point of a dialog:
 *
 *   * It has to hold its content in a *box* -- the sheet -- that sits above the
 *     backdrop, and in the light DOM there is no way to wrap content the owner
 *     passed in. A `<slot>` is the one mechanism for "put the children here",
 *     and a slot distributes nothing outside a shadow root. Rendering this
 *     element's template into itself therefore left the owner's content as
 *     siblings of the backdrop instead of children of the sheet: the sheet
 *     collapsed to the height of its own title bar, the offers rendered
 *     underneath the fixed, full-viewport backdrop, and every click aimed at an
 *     Install button hit the backdrop instead -- which closes the dialog. The
 *     "Add module to room" flow could not be completed with a mouse at all.
 *   * Its content is styled by the outer stylesheet anyway. Slotted nodes stay
 *     in the light tree where the panel's stylesheet reaches them, so this
 *     element only has to style its own chrome, which it does below. The
 *     frontend stylesheet it would otherwise have to re-implement styles no
 *     control the chrome contains.
 */

import { css, html, LitElement, type TemplateResult } from "lit";

export class OpenHouseDialog extends LitElement {
  static override properties = {
    heading: { type: String },
    open: { type: Boolean, reflect: true },
  };

  /**
   * The chrome, and only the chrome.
   *
   * What sits in the slot -- the module cards, the filter input, the error
   * banner -- stays in the light DOM and is styled by the panel's own
   * stylesheet, exactly as it was before. The backdrop and the sheet cannot be,
   * because they live in this shadow root, so the card look they borrow from
   * `.card` in `styles.ts` is restated here against the same Home Assistant
   * custom properties. The two read the same theme variables, so they cannot
   * drift apart visually.
   */
  static override styles = css`
    .backdrop {
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.45);
      display: flex;
      align-items: flex-start;
      justify-content: center;
      padding: 6vh 16px;
      overflow: auto;
      z-index: 10;
    }
    .sheet {
      width: min(760px, 100%);
      margin: 0;
      background: var(--card-background-color);
      border-radius: var(--ha-card-border-radius, 12px);
      border: 1px solid var(--ha-card-border-color, var(--divider-color));
      padding: 16px;
      box-shadow: var(--ha-card-box-shadow, 0 8px 24px rgba(0, 0, 0, 0.3));
    }
    .dialog-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }
    h2 {
      margin: 0;
      font-weight: var(--ha-font-weight-normal, 400);
      font-size: var(--ha-font-size-l, 20px);
    }
    button {
      font: inherit;
      font-size: var(--ha-font-size-s, 12px);
      padding: 4px 8px;
      border-radius: 6px;
      border: 1px solid var(--divider-color);
      background: var(--card-background-color);
      color: var(--primary-text-color);
      cursor: pointer;
    }
    button:hover {
      background: var(--secondary-background-color, rgba(0, 0, 0, 0.04));
    }
    button:focus-visible {
      outline: 2px solid var(--primary-color);
      outline-offset: 1px;
    }
  `;

  declare heading: string;
  declare open: boolean;

  private previouslyFocused: HTMLElement | null = null;

  constructor() {
    super();
    this.heading = "";
    this.open = false;
  }

  override connectedCallback(): void {
    super.connectedCallback();
    document.addEventListener("keydown", this.onKeyDown);
  }

  override disconnectedCallback(): void {
    document.removeEventListener("keydown", this.onKeyDown);
    super.disconnectedCallback();
  }

  override updated(changed: Map<string, unknown>): void {
    if (changed.has("open")) {
      if (this.open) {
        // `document.activeElement`, and not the root node's: the element that
        // opened this dialog is in the panel, outside this shadow root, and the
        // root node's own `activeElement` would only ever name something inside
        // it -- which the opener is not.
        this.previouslyFocused = document.activeElement as HTMLElement | null;
        queueMicrotask(() => {
          // `this.querySelector` reads the light tree, so this finds the
          // slotted filter input rather than the Close button here in the
          // shadow root. The input is the right landing place when the dialog
          // has one.
          this.querySelector<HTMLElement>("[autofocus], button, input, select")
            ?.focus();
        });
      } else {
        this.previouslyFocused?.focus();
        this.previouslyFocused = null;
      }
    }
  }

  private onKeyDown = (event: KeyboardEvent): void => {
    if (event.key === "Escape" && this.open) {
      this.close();
    }
  };

  /**
   * Tell the owner the dialog is shut, and only the owner.
   *
   * The event is named for the dialog rather than for closing in general, and
   * it does not compose across shadow roots. Both are the same correction: a
   * bare `closed` that travels the whole panel is read by every ancestor that
   * has a "something closed" meaning, and `<open-house-tab-rooms>` has one --
   * it treats `closed` from the room's settings page as "leave the room". So
   * the dialog's own close, which happens when a person opens "Add module to
   * room" and then changes their mind, arrived there meaning the opposite of
   * what it said and returned them to the room list. An event that means "this
   * dialog is done" is addressed to whoever opened the dialog; it is not news
   * for the tab that happens to contain it.
   */
  private close(): void {
    this.open = false;
    this.dispatchEvent(new CustomEvent("dialog-closed", { bubbles: true }));
  }

  protected override render(): TemplateResult {
    if (!this.open) return html``;
    return html`
      <div
        class="backdrop"
        @click=${(event: Event) => {
          if (event.target === event.currentTarget) this.close();
        }}
      >
        <div
          class="sheet"
          role="dialog"
          aria-modal="true"
          aria-label=${this.heading}
        >
          <div class="dialog-head">
            <h2>${this.heading}</h2>
            <button type="button" @click=${() => this.close()}>Close</button>
          </div>
          <slot></slot>
        </div>
      </div>
    `;
  }
}

if (!customElements.get("open-house-dialog")) {
  customElements.define("open-house-dialog", OpenHouseDialog);
}
