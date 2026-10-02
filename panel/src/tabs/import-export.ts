/**
 * The Import/Export tab: a full backup, and a dry-run restore.
 *
 * The shape of the document is the frozen `schemas/export-document`; the panel
 * neither invents it nor validates it. What the panel owns is the *sequence*
 * Phase 3 asks for: choose a file, see the diff, re-link anything whose entity
 * is gone, then apply -- and applying takes an automatic snapshot first, so undo
 * is not a feature the user has to remember to reach for.
 *
 * The dry run is a separate command from the apply on purpose. A preview that
 * were the apply with a flag would be one refactor away from being the apply.
 */

import { html, nothing, type TemplateResult } from "lit";
import { OpenHouseElement } from "../base.ts";
import type { ImportDiffRow, ImportPreview } from "../api/models.ts";

export class ImportExportTab extends OpenHouseElement {
  static override properties = {
    ...OpenHouseElement.properties,
    exportPreview: { state: true },
    preview: { state: true },
    pending: { state: true },
    fileLabel: { state: true },
    relinkChoice: { state: true },
    isLoading: { state: true },
    busy: { state: true },
    error: { state: true },
    notice: { state: true },
  };

  private exportPreview = "";
  private preview: ImportPreview | null = null;
  private pending: unknown = null;
  private fileLabel = "";
  private relinkChoice = new Map<string, string>();
  private isLoading = false;
  private busy = false;
  private error: ReturnType<OpenHouseElement["toError"]> | null = null;
  private notice: string | null = null;

  private async doExport(): Promise<void> {
    this.busy = true;
    this.error = null;
    this.notice = null;
    try {
      const document = await this.requireClient().exportDocument();
      this.exportPreview = JSON.stringify(document, null, 2);
      this.download(document);
      this.notice = "Exported and downloaded.";
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.busy = false;
      this.requestUpdate();
    }
  }

  private download(document: unknown): void {
    const blob = new Blob([JSON.stringify(document, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const anchor = globalThis.document.createElement("a");
    anchor.href = url;
    anchor.download = "open-house-export.json";
    anchor.click();
    URL.revokeObjectURL(url);
  }

  private async onFile(event: Event): Promise<void> {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    this.fileLabel = file.name;
    this.error = null;
    this.notice = null;
    this.preview = null;
    try {
      const text = await file.text();
      this.pending = JSON.parse(text) as unknown;
      this.isLoading = true;
      this.preview = await this.requireClient().previewImport(this.pending);
      this.relinkChoice = new Map(
        this.preview.relink.map((request) => [
          `${request.room_id}/${request.slot}`,
          request.candidates[0]?.entity_id ?? "",
        ]),
      );
    } catch (error) {
      this.error = this.toError(error);
      this.pending = null;
    } finally {
      this.isLoading = false;
      this.requestUpdate();
    }
  }

  private async apply(): Promise<void> {
    if (!this.pending) return;
    this.busy = true;
    this.error = null;
    try {
      const result = await this.requireClient().applyImport(this.pending);
      for (const request of this.preview?.relink ?? []) {
        const entityId = this.relinkChoice.get(`${request.room_id}/${request.slot}`);
        if (entityId) {
          await this.requireClient().replace(request.room_id, request.slot, entityId);
        }
      }
      this.notice = `Import applied. Snapshot ${result.snapshot_id} taken, so this can be undone.`;
      this.preview = null;
      this.pending = null;
      this.fileLabel = "";
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.busy = false;
      this.requestUpdate();
    }
  }

  protected override render(): TemplateResult {
    return html`
      ${this.errorBanner(this.error)}
      ${this.notice ? html`<div class="banner info">${this.notice}</div>` : null}

      <h1 style="margin-bottom:12px">Import / Export</h1>

      <div class="card">
        <h2>Export</h2>
        <p class="help">
          A full backup: every room, binding, profile and option. The document
          matches the export-document schema, so it is re-importable after a
          re-pair because each binding carries its registry id as well as its
          entity id.
        </p>
        <button
          type="button"
          class="primary"
          ?disabled=${this.busy}
          @click=${() => void this.doExport()}
        >
          ${this.busy ? "Exporting..." : "Export and download"}
        </button>
        ${this.exportPreview
          ? html`<details style="margin-top:8px">
              <summary>Preview</summary>
              <pre style="overflow:auto;max-height:320px">${this.exportPreview}</pre>
            </details>`
          : nothing}
      </div>

      <div class="card">
        <h2>Import</h2>
        <p class="help">
          Import runs a dry run first and shows every change it would make. It
          takes a snapshot before applying, so an import can be undone.
        </p>
        <input
          type="file"
          accept="application/json,.json"
          aria-label="Choose an export file"
          @change=${(event: Event) => void this.onFile(event)}
        />
        ${this.fileLabel ? html`<p class="muted small">${this.fileLabel}</p>` : nothing}
        ${this.isLoading ? this.loading("Checking the file...") : nothing}
        ${this.preview ? this.renderPreview(this.preview) : nothing}
      </div>
    `;
  }

  private renderPreview(preview: ImportPreview): TemplateResult {
    return html`<div style="margin-top:12px">
      <div class="row">
        <span class="chip ${preview.compatible ? "ok" : "error"}">
          format ${preview.format_version}
        </span>
        ${preview.compatible
          ? nothing
          : html`<span class="chip error">incompatible</span>`}
      </div>
      ${preview.notes.map((note) => html`<p class="help">${note}</p>`)}

      <h3 style="margin-top:12px">Changes</h3>
      ${preview.diff.length === 0
        ? html`<p class="muted">Nothing would change.</p>`
        : html`<table>
            <thead>
              <tr>
                <th>Change</th>
                <th>Scope</th>
                <th>Path</th>
                <th>Before</th>
                <th>After</th>
              </tr>
            </thead>
            <tbody>
              ${preview.diff.map((row) => this.renderDiff(row))}
            </tbody>
          </table>`}

      ${preview.relink.length > 0
        ? html`<h3 style="margin-top:12px">Re-link needed</h3>
            <p class="help">
              These bindings name a device that is no longer present. Pick a
              replacement for each, or leave it blank to leave the slot unbound.
            </p>
            ${preview.relink.map((request) => {
              const key = `${request.room_id}/${request.slot}`;
              return html`<div class="field">
                <div class="label-row">
                  <span class="label">${request.room_id} / ${request.slot}</span>
                </div>
                <select
                  aria-label="Relink ${key}"
                  @change=${(event: Event) => {
                    this.relinkChoice.set(
                      key,
                      (event.target as HTMLSelectElement).value,
                    );
                  }}
                >
                  <option value="">Leave unbound</option>
                  ${request.candidates.map(
                    (candidate) => html`<option
                      value=${candidate.entity_id}
                      ?selected=${this.relinkChoice.get(key) === candidate.entity_id}
                    >
                      ${candidate.friendly_name} (${candidate.entity_id})
                    </option>`,
                  )}
                </select>
              </div>`;
            })}`
        : nothing}

      <div class="row" style="margin-top:12px">
        <button
          type="button"
          class="primary"
          ?disabled=${!this.admin || this.busy || !preview.compatible}
          @click=${() => void this.apply()}
        >
          ${this.busy ? "Applying..." : "Apply import"}
        </button>
        <button
          type="button"
          @click=${() => {
            this.preview = null;
            this.pending = null;
            this.fileLabel = "";
          }}
        >
          Cancel
        </button>
      </div>
    </div>`;
  }

  private renderDiff(row: ImportDiffRow): TemplateResult {
    return html`<tr>
      <td><span class="chip ${this.diffChip(row.kind)}">${row.kind}</span></td>
      <td>${row.scope}</td>
      <td class="muted small">${row.path}</td>
      <td class="muted small">${row.before ?? "-"}</td>
      <td class="small">${row.after ?? "-"}</td>
    </tr>`;
  }

  private diffChip(kind: ImportDiffRow["kind"]): string {
    switch (kind) {
      case "add":
        return "ok";
      case "remove":
        return "error";
      case "change":
        return "warn";
      default:
        return "";
    }
  }
}

if (!customElements.get("open-house-tab-import-export")) {
  customElements.define("open-house-tab-import-export", ImportExportTab);
}
