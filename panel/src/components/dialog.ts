/**
 * A modal, for the flows that must not be left half-finished.
 *
 * Installing a module changes the house, so it opens in a modal rather than
 * expanding inline: the user should not be able to wander to another tab
 * halfway through a conflict check and come back to a form that has silently
 * moved on. The element traps Escape and a click on the backdrop, restores focus
 * to whatever opened it, and marks the rest of the page inert to assistive
 * technology while it is up.
 */

import { LitElement, html, type TemplateResult } from "lit";

export class OpenHouseDialog extends LitElement {
  static override properties = {
    heading: { type: String },
    open: { type: Boolean, reflect: true },
  };

  declare heading: string;
  declare open: boolean;

  private previouslyFocused: HTMLElement | null = null;

  constructor() {
    super();
    this.heading = "";
    this.open = false;
  }

  protected override createRenderRoot(): HTMLElement | DocumentFragment {
    return this;
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
        this.previouslyFocused = (this.getRootNode() as Document)
          .activeElement as HTMLElement | null;
        queueMicrotask(() => {
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

  private close(): void {
    this.open = false;
    this.dispatchEvent(
      new CustomEvent("closed", { bubbles: true, composed: true }),
    );
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
          class="sheet card"
          role="dialog"
          aria-modal="true"
          aria-label=${this.heading}
        >
          <div class="row spread">
            <h2>${this.heading}</h2>
            <button type="button" class="icon" @click=${() => this.close()}>
              Close
            </button>
          </div>
          <slot></slot>
        </div>
      </div>
      <style>
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
          box-shadow: var(--ha-card-box-shadow, 0 8px 24px rgba(0, 0, 0, 0.3));
        }
      </style>
    `;
  }
}

if (!customElements.get("open-house-dialog")) {
  customElements.define("open-house-dialog", OpenHouseDialog);
}
