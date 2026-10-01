/**
 * "Add module to room": the offers, their conflict check, and the install.
 *
 * spec.txt's requirement is exact: it "shows only packs the room can satisfy,
 * with a conflict check before install". Both halves are the server's work and
 * both arrive in one `roomAvailableModules` answer -- `satisfiable` and
 * `missing_slots` are the "can satisfy" verdict, `conflicts` is the conflict
 * check. The panel renders those verdicts; it does not recompute them, because
 * a second implementation of "can this room satisfy this pack" is a second
 * definition of it, free to disagree with the engine's.
 *
 * What the screen owes the user, given those verdicts:
 *
 *   * A pack the room cannot satisfy is shown, greyed, with the exact slots
 *     missing -- hiding it would leave the user wondering why a pack they read
 *     about is absent.
 *   * A blocking conflict disables install and names the installed pack and the
 *     range; a warning conflict installs but says what it will do.
 *   * An already-installed pack says so rather than offering a second install.
 */

import { html, type TemplateResult } from "lit";
import { OpenHouseElement } from "../base.ts";
import { SchemaForm } from "../components/schema-form.ts";
import type { ModuleOffer, ModuleConflict } from "../api/models.ts";

export class AddModuleDialog extends OpenHouseElement {
  static override properties = {
    ...OpenHouseElement.properties,
    open: { type: Boolean },
    roomId: { type: String },
  };

  declare open: boolean;
  declare roomId: string;

  private offers: ModuleOffer[] = [];
  private isLoading = false;
  private error: ReturnType<OpenHouseElement["toError"]> | null = null;
  private busyPack: string | null = null;
  private filter = "";

  constructor() {
    super();
    this.open = false;
    this.roomId = "";
  }

  override updated(changed: Map<string, unknown>): void {
    if (changed.has("open") && this.open) {
      void this.load();
    }
  }

  private async load(): Promise<void> {
    this.isLoading = true;
    this.error = null;
    this.requestUpdate();
    try {
      this.offers = await this.requireClient().availableModules(this.roomId);
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.isLoading = false;
      this.requestUpdate();
    }
  }

  private async install(offer: ModuleOffer): Promise<void> {
    if (!offer.satisfiable || this.blocking(offer).length > 0) return;
    this.busyPack = offer.pack;
    this.error = null;
    try {
      await this.requireClient().installModule(this.roomId, offer.pack);
      this.dispatchEvent(
        new CustomEvent("module-installed", {
          detail: { pack: offer.pack },
          bubbles: true,
          composed: true,
        }),
      );
      this.open = false;
      this.dispatchEvent(new CustomEvent("closed", { bubbles: true, composed: true }));
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.busyPack = null;
      this.requestUpdate();
    }
  }

  private blocking(offer: ModuleOffer): ModuleConflict[] {
    return offer.conflicts.filter((conflict) => conflict.severity === "blocking");
  }

  private warnings(offer: ModuleOffer): ModuleConflict[] {
    return offer.conflicts.filter((conflict) => conflict.severity === "warning");
  }

  protected override render(): TemplateResult {
    const visible = this.offers.filter((offer) =>
      this.filter.trim() === ""
        ? true
        : `${offer.name} ${offer.pack} ${offer.description}`
            .toLowerCase()
            .includes(this.filter.trim().toLowerCase()),
    );
    return html`<open-house-dialog
      .heading=${"Add module to room"}
      .open=${this.open}
      @closed=${() => {
        this.open = false;
        this.dispatchEvent(new CustomEvent("closed", { bubbles: true, composed: true }));
      }}
    >
      ${this.errorBanner(this.error)}
      <input
        type="text"
        class="grow"
        placeholder="Filter modules"
        aria-label="Filter modules"
        .value=${this.filter}
        @input=${(event: Event) => {
          this.filter = (event.target as HTMLInputElement).value;
        }}
      />
      ${this.isLoading
        ? this.loading("Looking for modules the room can satisfy...")
        : visible.length === 0
          ? this.emptyState(
              "No modules",
              this.offers.length === 0
                ? "No pack in the house or the store offers anything this room can take yet."
                : "No module matches that filter.",
            )
          : html`<div class="stack" style="margin-top:12px">
              ${visible.map((offer) => this.renderOffer(offer))}
            </div>`}
    </open-house-dialog>`;
  }

  private renderOffer(offer: ModuleOffer): TemplateResult {
    const blocked = this.blocking(offer);
    const warns = this.warnings(offer);
    const installable = offer.satisfiable && blocked.length === 0 && !offer.already_installed;
    return html`<div class="card" data-pack=${offer.pack}>
      <div class="row spread wrap">
        <div class="grow">
          <h3>${offer.name || offer.pack}</h3>
          <p class="muted small">
            ${offer.pack} &middot; v${offer.version} &middot; ${offer.kind}
            &middot; ${offer.license}
          </p>
        </div>
        <div class="row">
          ${offer.already_installed
            ? html`<span class="chip ok">installed</span>`
            : installable
              ? html`<button
                  type="button"
                  class="primary"
                  ?disabled=${this.busyPack === offer.pack}
                  @click=${() => void this.install(offer)}
                >
                  ${this.busyPack === offer.pack ? "Installing..." : "Install"}
                </button>`
              : html`<button type="button" disabled title=${this.reason(offer)}>
                  Cannot install
                </button>`}
        </div>
      </div>

      <p>${offer.description}</p>

      <div class="row wrap" style="margin-top:6px">
        ${offer.requires_slots.map(
          (slot) =>
            html`<span class="chip ${offer.missing_slots.includes(slot) ? "error" : "ok"}"
              >${slot}${offer.missing_slots.includes(slot) ? " (missing)" : ""}</span
            >`,
        )}
        ${offer.optional_slots.map(
          (slot) =>
            html`<span class="chip"
              >${slot}${offer.optional_slots_present.includes(slot) ? "" : " (optional, absent)"}</span
            >`,
        )}
      </div>

      ${blocked.length > 0
        ? html`<div class="banner error">
            <strong>Conflicts with an installed pack</strong>
            ${blocked.map(
              (conflict) => html`<p class="help">
                ${conflict.pack}${conflict.installed_version
                  ? ` v${conflict.installed_version}`
                  : ""}: ${conflict.detail}
              </p>`,
            )}
          </div>`
        : null}
      ${warns.length > 0
        ? html`<div class="banner warn">
            <strong>Heads up</strong>
            ${warns.map(
              (conflict) =>
                html`<p class="help">${conflict.pack}: ${conflict.detail}</p>`,
            )}
          </div>`
        : null}
      ${!offer.satisfiable && blocked.length === 0
        ? html`<div class="banner warn">
            This room is missing required slots:
            ${offer.missing_slots.join(", ")}. Bind them on the room's settings
            page first.
          </div>`
        : null}

      ${offer.behaviours.length > 0
        ? html`<details>
            <summary>${offer.behaviours.length} behaviours</summary>
            <ul>
              ${offer.behaviours.map(
                (behaviour) =>
                  html`<li>
                    ${behaviour.label || behaviour.id}
                    ${behaviour.priority === null
                      ? null
                      : html`<span class="chip">priority ${behaviour.priority}</span>`}
                  </li>`,
              )}
            </ul>
          </details>`
        : null}
      ${offer.options_schema
        ? html`<details>
            <summary>Options this module adds</summary>
            <open-house-schema-form
              .schema=${offer.options_schema}
              .values=${{}}
              readonly
            ></open-house-schema-form>
          </details>`
        : null}
    </div>`;
  }

  private reason(offer: ModuleOffer): string {
    if (offer.already_installed) return "This module is already installed in the room.";
    if (!offer.satisfiable) {
      return `Missing required slots: ${offer.missing_slots.join(", ")}`;
    }
    return "A blocking conflict with an installed pack.";
  }
}

if (!customElements.get("open-house-add-module")) {
  customElements.define("open-house-add-module", AddModuleDialog);
}

// Referenced so the schema form element is registered before a dialog renders
// one, whatever order the bundle evaluates modules in.
void SchemaForm;
