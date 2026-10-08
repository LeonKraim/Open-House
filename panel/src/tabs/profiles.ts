/**
 * The Profiles tab: what profiles exist, which rooms are on one, and the file.
 *
 * Profiles are the "premade and preintegrated" idea (spec.txt, Phase 3). The
 * panel's job is not to author them -- a profile-set is a pack -- but to show
 * what is available and to let an admin move a room onto a different one on an
 * axis, put the house on a house profile, or carry a profile between houses as
 * a file. Selecting is per room and per axis, so this screen keeps the room and
 * the axis together rather than presenting a flat list of profiles that would
 * lose which of them can run at the same time as which.
 *
 * The file half is here rather than on a screen of its own because it is the
 * same subject: the thing being exported is what this page lists, and a profile
 * whose row is two cards away from the button that writes it is a screen a
 * person has to already know their way around. What replaced the whole-house
 * backup is narrower on purpose -- a profile names settings, so it is portable;
 * a house document names *this house's* devices, so it is not.
 */

import { html, nothing, type TemplateResult } from "lit";
import { OpenHouseElement } from "../base.ts";
import type { ProfileRef, RoomSummary } from "../api/models.ts";

export class ProfilesTab extends OpenHouseElement {
  static override properties = {
    ...OpenHouseElement.properties,
    profiles: { state: true },
    rooms: { state: true },
    isLoading: { state: true },
    error: { state: true },
    busy: { state: true },
    notice: { state: true },
    replaceOnImport: { state: true },
    fileLabel: { state: true },
    renaming: { state: true },
    typed: { state: true },
  };

  private profiles: ProfileRef[] = [];
  private rooms: RoomSummary[] = [];
  private isLoading = true;
  private error: ReturnType<OpenHouseElement["toError"]> | null = null;
  private busy = false;
  private notice: string | null = null;
  /** Whether an import may overwrite a profile this house already holds. */
  private replaceOnImport = false;
  private fileLabel = "";
  /** The profile the rename sheet is renaming, and the name typed into it. */
  private renaming: string | null = null;
  private typed = "";

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
      const client = this.requireClient();
      const [profiles, rooms] = await Promise.all([
        client.profiles(),
        client.rooms(),
      ]);
      this.profiles = profiles;
      this.rooms = rooms;
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.isLoading = false;
      this.requestUpdate();
    }
  }

  private async activate(
    roomId: string,
    axis: string,
    profile: string,
  ): Promise<void> {
    await this.act(() =>
      this.requireClient().activateProfile(roomId, axis, profile),
    );
  }

  /**
   * Put the house on a house profile, or take it off one.
   *
   * Both ends of the same fact, so they share a method: leaving a house profile
   * is the only way to *stop* being on one, and a screen that offered activation
   * without it would be a switch with one position.
   */
  private async setHouseProfile(profile: string | null): Promise<void> {
    await this.act(() =>
      profile === null
        ? this.requireClient().deactivateHouseProfile()
        : this.requireClient().activateHouseProfile(profile),
    );
  }

  /**
   * Give a house profile a different name, without putting the house on it.
   *
   * The reason this is here and not only beside the selector is that the only
   * other way to manage a profile would be to put the house on it first -- and
   * for a profile taken from this house, that means restoring the whole house
   * before you are allowed to rename it. Managing the library and being on a
   * profile are two different acts.
   */
  private async rename(from: string): Promise<void> {
    const to = this.typed.trim();
    if (to === "") return;
    await this.act(() => this.requireClient().renameProfile(from, to));
    this.renaming = null;
    this.typed = "";
  }

  /**
   * Drop a house profile, and everything that named it.
   *
   * A room selection naming it goes with it (`ProfileSet.remove`), and a house
   * profile in force is released -- which is why this is offered on every row
   * rather than only on the one in force.
   */
  private async drop(name: string): Promise<void> {
    await this.act(() => this.requireClient().removeProfile(name));
  }

  /** One write, one reload, one place the busy flag is cleared. */
  private async act(write: () => Promise<unknown>): Promise<void> {
    this.busy = true;
    this.error = null;
    this.notice = null;
    try {
      await write();
      await this.load();
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.busy = false;
      this.requestUpdate();
    }
  }

  /**
   * Download a profile, or every profile, as a file.
   *
   * The server answers the document; the panel only puts it in a download,
   * because a file is a thing the browser owns. `profile` absent is the whole
   * set, which the server sends as `{ profiles: [...] }` -- the form the
   * importer reads back, so a file exported here imports here or anywhere.
   */
  private async export(profile?: string): Promise<void> {
    this.busy = true;
    this.error = null;
    this.notice = null;
    this.requestUpdate();
    try {
      const document = await this.requireClient().exportProfile(profile);
      const name = profile === undefined ? "openhouse-profiles" : `profile-${profile}`;
      this.download(document, `${name}.json`);
      this.notice =
        profile === undefined
          ? "Every profile exported and downloaded."
          : `The ${profile} profile exported and downloaded.`;
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.busy = false;
      this.requestUpdate();
    }
  }

  private download(document: unknown, filename: string): void {
    const blob = new Blob([JSON.stringify(document, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const anchor = globalThis.document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  /**
   * Read a chosen file back into this house.
   *
   * Nothing is applied until the server has read the whole document: the
   * importer validates every profile before writing any of them, and refuses a
   * name this house already holds unless the replace box is ticked -- which is
   * why that box is on the screen and not a default. The answer names what
   * happened, rather than the panel guessing from what it sent.
   */
  private async onFile(event: Event): Promise<void> {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    this.fileLabel = file.name;
    this.busy = true;
    this.error = null;
    this.notice = null;
    this.requestUpdate();
    try {
      const document = JSON.parse(await file.text()) as unknown;
      const result = await this.requireClient().importProfiles(
        document,
        this.replaceOnImport,
      );
      this.notice = this.importNotice(result.imported, result.replaced);
      await this.load();
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.busy = false;
      input.value = "";
      this.requestUpdate();
    }
  }

  private importNotice(imported: string[], replaced: string[]): string {
    if (imported.length === 0 && replaced.length === 0) {
      return "The file held no profiles that were not already here.";
    }
    const parts: string[] = [];
    if (imported.length > 0) parts.push(`added ${imported.join(", ")}`);
    if (replaced.length > 0) parts.push(`replaced ${replaced.join(", ")}`);
    return `Import done: ${parts.join("; ")}.`;
  }

  /**
   * A new name for a profile the house is holding, asked for from its own card.
   *
   * One field, and a sentence under it, because "rename" on a thing other
   * things refer to is exactly where a person expects to break those
   * references: every room that was on it stays on it, and anything else that
   * named it names the new name afterwards.
   */
  private renderRenaming(from: string): TemplateResult {
    return html`<open-house-dialog
      .heading=${"Rename profile"}
      .open=${true}
      @dialog-closed=${() => {
        this.renaming = null;
      }}
    >
      <div class="field">
        <label class="label" for="profile-rename-name">Name</label>
        <input
          id="profile-rename-name"
          type="text"
          class="grow"
          .value=${this.typed}
          placeholder=${from}
          @input=${(event: Event) => {
            this.typed = (event.target as HTMLInputElement).value;
          }}
        />
      </div>
      <p class="help">
        Every room that is on ${from} stays on it: a rename moves the profile,
        it does not make a second one. The house is not put on it by this, and
        not taken off it.
      </p>
      <div class="row">
        <button
          type="button"
          id="profile-rename-confirm"
          ?disabled=${this.busy || this.typed.trim() === ""}
          @click=${() => void this.rename(from)}
        >
          Rename it
        </button>
      </div>
    </open-house-dialog>`;
  }

  protected override render(): TemplateResult {
    if (this.isLoading && this.profiles.length === 0 && this.rooms.length === 0) {
      return this.loading("Reading profiles...");
    }
    const roomProfiles = this.profiles.filter((p) => p.kind === "room");
    const houseProfiles = this.profiles.filter((p) => p.kind === "house");
    const houseInForce = houseProfiles.find((profile) => profile.active) ?? null;
    return html`
      ${this.errorBanner(this.error)}
      ${this.notice ? html`<div class="banner info">${this.notice}</div>` : nothing}
      <h1 style="margin-bottom:12px">Profiles</h1>

      <div class="card">
        <h2>House profiles</h2>
        <p class="help">
          A house profile bundles a selection for every room at once -- a
          "vacation" or "guests over" setup you can switch to whole. Putting the
          house on one selects a profile for each room it names; taking it off
          leaves those room selections where they are, because this house does
          not remember what every room was on before.
        </p>
        ${houseProfiles.length === 0
          ? html`<p class="muted">No house profiles installed.</p>`
          : html`<div class="grid">
              ${houseProfiles.map(
                (profile) => html`<div class="card">
                  <div class="row spread">
                    <h3>${profile.label}</h3>
                    ${profile.active
                      ? html`<span class="chip ok">in force</span>`
                      : nothing}
                  </div>
                  <p class="muted small">${profile.description}</p>
                  <div class="row wrap">
                    ${this.admin
                      ? html`<button
                          type="button"
                          class=${profile.active ? "" : "primary"}
                          ?disabled=${this.busy}
                          @click=${() =>
                            void this.setHouseProfile(
                              profile.active ? null : profile.name,
                            )}
                        >
                          ${profile.active
                            ? "Take the house off this"
                            : "Activate for the house"}
                        </button>`
                      : nothing}
                    <button
                      type="button"
                      class="icon"
                      ?disabled=${this.busy}
                      @click=${() => void this.export(profile.name)}
                    >
                      Export
                    </button>
                    ${this.admin
                      ? html`<button
                          type="button"
                          class="icon"
                          data-profile-rename=${profile.name}
                          ?disabled=${this.busy}
                          @click=${() => {
                            this.typed = "";
                            this.renaming = profile.name;
                          }}
                        >
                          Rename
                        </button>`
                      : nothing}
                    ${this.admin
                      ? html`<button
                          type="button"
                          class="icon danger"
                          data-profile-delete=${profile.name}
                          ?disabled=${this.busy}
                          @click=${() => void this.drop(profile.name)}
                        >
                          Delete
                        </button>`
                      : nothing}
                  </div>
                </div>`,
              )}
            </div>`}
        ${houseInForce
          ? html`<p class="muted small" style="margin-top:8px">
              The house is on ${houseInForce.label}.
            </p>`
          : html`<p class="muted small" style="margin-top:8px">
              No house profile is in force.
            </p>`}
        ${this.renaming === null ? nothing : this.renderRenaming(this.renaming)}
      </div>

      <div class="card">
        <h2>Room profiles</h2>
        <p class="help">
          Each room runs one profile per axis. Two profiles in one room must be
          on different axes.
        </p>
        ${roomProfiles.length === 0
          ? html`<p class="muted">No room profiles installed.</p>`
          : html`<table>
              <thead>
                <tr>
                  <th>Profile</th>
                  <th>Axis</th>
                  <th>Description</th>
                  <th></th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                ${roomProfiles.map(
                  (profile) => html`<tr>
                    <td>${profile.label}</td>
                    <td>${profile.axis ?? "-"}</td>
                    <td class="muted small">${profile.description}</td>
                    <td>
                      <span class="chip ${profile.active ? "ok" : ""}"
                        >${profile.active ? "in use" : ""}</span
                      >
                    </td>
                    <td>
                      <button
                        type="button"
                        class="icon"
                        ?disabled=${this.busy}
                        @click=${() => void this.export(profile.name)}
                      >
                        Export
                      </button>
                    </td>
                  </tr>`,
                )}
              </tbody>
            </table>`}
      </div>

      <div class="card">
        <h2>Rooms</h2>
        ${this.rooms.length === 0
          ? html`<p class="muted">No rooms.</p>`
          : this.rooms.map((room) => this.renderRoom(room))}
      </div>

      ${this.renderFiles()}
    `;
  }

  /**
   * The file half: every profile out, or a file in.
   *
   * One card, because exporting and importing are the same subject and a person
   * who has just been handed a profile file arrives at this screen to find the
   * other half of the pair. The replace box is here rather than a confirm
   * dialog, because what it decides -- whether a tuned profile of the same name
   * is overwritten -- is a fact about the file that the person knows and the
   * panel does not.
   */
  private renderFiles(): TemplateResult {
    return html`<div class="card">
      <h2>Files</h2>
      <p class="help">
        A profile is portable: it names settings, not this house's devices, so a
        file exported here imports anywhere. Export a single profile from its row
        above, or every profile at once here; import reads back either form.
      </p>
      <div class="row wrap">
        <button
          type="button"
          ?disabled=${this.busy || this.profiles.length === 0}
          @click=${() => void this.export()}
        >
          Export every profile
        </button>
      </div>
      <div class="field" style="margin-top:12px">
        <div class="label-row">
          <span class="label">Import a profile file</span>
        </div>
        <input
          type="file"
          accept="application/json,.json"
          aria-label="Choose a profile file"
          ?disabled=${!this.admin || this.busy}
          @change=${(event: Event) => void this.onFile(event)}
        />
      </div>
      <label class="toggle">
        <input
          type="checkbox"
          .checked=${this.replaceOnImport}
          @change=${(event: Event) => {
            this.replaceOnImport = (event.target as HTMLInputElement).checked;
          }}
        />
        <span>Overwrite profiles this house already holds</span>
      </label>
      ${this.fileLabel
        ? html`<p class="muted small">Last file: ${this.fileLabel}</p>`
        : nothing}
    </div>`;
  }

  private renderRoom(room: RoomSummary): TemplateResult {
    const axes = new Map<string, ProfileRef[]>();
    for (const profile of this.profiles) {
      if (profile.kind !== "room" || profile.axis === null) continue;
      const bucket = axes.get(profile.axis) ?? [];
      bucket.push(profile);
      axes.set(profile.axis, bucket);
    }
    return html`<div class="field">
      <div class="row spread">
        <span class="label">${room.name}</span>
        <div class="row wrap">
          ${Object.entries(room.active_profiles).map(
            ([axis, profile]) => html`<span class="chip">${axis}: ${profile}</span>`,
          )}
        </div>
      </div>
      ${axes.size === 0
        ? html`<p class="muted small">No room profiles available.</p>`
        : html`<div class="grid">
            ${[...axes.entries()].map(
              ([axis, profiles]) => html`<div>
                <div class="label-row"><span class="label">${axis}</span></div>
                <select
                  aria-label="${room.name} ${axis}"
                  ?disabled=${!this.admin || this.busy}
                  @change=${(event: Event) =>
                    void this.activate(
                      room.id,
                      axis,
                      (event.target as HTMLSelectElement).value,
                    )}
                >
                  ${profiles.map(
                    (profile) => html`<option
                      value=${profile.name}
                      ?selected=${room.active_profiles[axis] === profile.name}
                    >
                      ${profile.label}
                    </option>`,
                  )}
                </select>
              </div>`,
            )}
          </div>`}
    </div>`;
  }
}

if (!customElements.get("open-house-tab-profiles")) {
  customElements.define("open-house-tab-profiles", ProfilesTab);
}
