/**
 * The Modules tab: every installed module, across every room.
 *
 * The room settings page answers "what is in this room"; this answers "what is
 * in the house", which is the question a user has when something is behaving
 * oddly and they do not yet know which room to look in. Each row therefore
 * carries its room and links straight to it, rather than making the user
 * remember where they saw the module.
 */

import { html, nothing, type TemplateResult } from "lit";
import { OpenHouseElement } from "../base.ts";
import type { InstalledModule } from "../api/models.ts";

export class ModulesTab extends OpenHouseElement {
  private modules: InstalledModule[] = [];
  private isLoading = true;
  private error: ReturnType<OpenHouseElement["toError"]> | null = null;
  private busy: string | null = null;

  override connectedCallback(): void {
    super.connectedCallback();
    void this.load();
  }

  reload(): void {
    void this.load();
  }

  private async load(): Promise<void> {
    this.isLoading = true;
    this.error = null;
    this.requestUpdate();
    try {
      this.modules = await this.requireClient().modules();
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.isLoading = false;
      this.requestUpdate();
    }
  }

  private async toggle(module: InstalledModule): Promise<void> {
    this.busy = module.pack;
    this.error = null;
    try {
      await this.requireClient().setModuleEnabled(
        module.room_id,
        module.pack,
        !module.enabled,
      );
      await this.load();
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.busy = null;
      this.requestUpdate();
    }
  }

  private async uninstall(module: InstalledModule): Promise<void> {
    this.busy = module.pack;
    this.error = null;
    try {
      await this.requireClient().uninstallModule(module.room_id, module.pack);
      await this.load();
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.busy = null;
      this.requestUpdate();
    }
  }

  protected override render(): TemplateResult {
    if (this.isLoading && this.modules.length === 0) {
      return this.loading("Reading installed modules...");
    }
    return html`
      ${this.errorBanner(this.error)}
      <h1 style="margin-bottom:12px">Modules</h1>
      ${this.modules.length === 0
        ? this.emptyState(
            "No modules installed",
            "Nothing is installed yet. Open a room and use \"Add module to room\", or browse the Store.",
          )
        : html`<table>
            <thead>
              <tr>
                <th>Module</th>
                <th>Room</th>
                <th>Version</th>
                <th>Behaviours</th>
                <th>State</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              ${this.modules.map((module) => this.renderRow(module))}
            </tbody>
          </table>`}
    `;
  }

  private renderRow(module: InstalledModule): TemplateResult {
    return html`<tr>
      <td>${module.name || module.pack}</td>
      <td>
        <a
          href="#"
          @click=${(event: Event) => {
            event.preventDefault();
            this.dispatchEvent(
              new CustomEvent("navigate", {
                detail: { tab: "rooms", roomId: module.room_id },
                bubbles: true,
                composed: true,
              }),
            );
          }}
          >${module.room_id}</a
        >
      </td>
      <td class="muted">v${module.version}</td>
      <td>
        <div class="row wrap">
          ${module.behaviours.map(
            (behaviour) =>
              html`<span class="chip ${behaviour.enabled ? "ok" : ""}"
                >${behaviour.label || behaviour.id}</span
              >`,
          )}
        </div>
      </td>
      <td>
        <span class="chip ${module.enabled ? "ok" : "warn"}"
          >${module.enabled ? "enabled" : "disabled"}</span
        >
      </td>
      <td>
        ${this.admin
          ? html`<div class="row">
              <button
                type="button"
                class="icon"
                ?disabled=${this.busy === module.pack}
                @click=${() => void this.toggle(module)}
              >
                ${module.enabled ? "Disable" : "Enable"}
              </button>
              <button
                type="button"
                class="icon danger"
                ?disabled=${this.busy === module.pack}
                @click=${() => void this.uninstall(module)}
              >
                Remove
              </button>
            </div>`
          : nothing}
      </td>
    </tr>`;
  }
}

if (!customElements.get("open-house-tab-modules")) {
  customElements.define("open-house-tab-modules", ModulesTab);
}
