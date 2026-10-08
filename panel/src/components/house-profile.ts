/**
 * The profile the whole house is on, at the top of the screen a person is on.
 *
 * A house profile is not a room's setting -- it is the thing that sets every
 * room at once: each room's profile on each axis, the packs installed, every
 * module hosted with the configuration it is on, and the settings of the house
 * and of each room. Which makes it the one control that belongs on more than one
 * screen: the Rooms tab is where a person looks at their house as a whole, and
 * the House tab is where they decide what it is doing. Both questions have the
 * same answer, and an answer that could only be given on a third screen would be
 * a control nobody finds.
 *
 * ## Taking a profile, and putting the house back on one
 *
 * A profile here is not authored -- the frozen `schemas/profile/` document is
 * pack data, and the Profiles tab has always been able to list, activate and
 * move one in and out as a file. What neither could do is make one *out of the
 * house in front of you*. That is what New is: it reads the whole house, names
 * it, and puts the house on it. Which, for a profile taken from that very house,
 * is no change at all -- the act is naming, so that "the way it was last week"
 * is a selection rather than a rebuild by hand.
 *
 * Putting a house back on one writes each part where a person's own edit of that
 * part is written, so the next thing anybody sets is what the house is set to. A
 * profile that outranked later edits would be a setting nobody could change
 * without knowing which profile they were on, which is the one thing this panel
 * must never be.
 */

import { html, nothing, type TemplateResult } from "lit";

import { OpenHouseElement } from "../base.ts";
import type { ProfileRef } from "../api/models.ts";

export class OpenHouseHouseProfile extends OpenHouseElement {
  static override properties = {
    ...OpenHouseElement.properties,
    profiles: { state: true },
    busy: { state: true },
    naming: { state: true },
    renaming: { state: true },
    typed: { state: true },
    error: { state: true },
  };

  /** Every profile the house holds, or null before anything has been read. */
  profiles: ProfileRef[] | null = null;
  /** Whether a read has been started, so a failed one is not started for ever. */
  private read = false;
  busy = false;
  /** Whether the "New profile" sheet is up. */
  naming = false;
  /** Whether the rename sheet is, and which profile it is renaming. */
  renaming: string | null = null;
  /** The name typed into whichever sheet is up. */
  typed = "";
  error: ReturnType<OpenHouseElement["toError"]> | null = null;

  override connectedCallback(): void {
    super.connectedCallback();
    void this.load();
  }

  override updated(): void {
    // The client is assigned by the parent after the element is constructed, so
    // the first read cannot happen in `connectedCallback` alone: an element
    // mounted before its parent has a client would sit empty for ever.
    if (this.client !== null && !this.read) {
      void this.load();
    }
  }

  private async load(): Promise<void> {
    if (!this.client || this.read) return;
    this.read = true;
    this.busy = true;
    try {
      this.profiles = await this.client.profiles();
      this.error = null;
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.busy = false;
    }
  }

  private get houseProfiles(): ProfileRef[] {
    return (this.profiles ?? []).filter((one) => one.kind === "house");
  }

  private get active(): string {
    return this.houseProfiles.find((one) => one.active)?.name ?? "";
  }

  private async put(name: string): Promise<void> {
    this.busy = true;
    this.error = null;
    try {
      this.profiles =
        name === ""
          ? await this.requireClient().deactivateHouseProfile()
          : await this.requireClient().activateHouseProfile(name);
      // The rooms moved with it, so every screen showing a room is now showing
      // something else: the page that holds this control reloads rather than
      // this element guessing which of its parts changed.
      if (name !== "") this.changed();
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.busy = false;
    }
  }

  private async take(): Promise<void> {
    this.busy = true;
    this.error = null;
    try {
      this.profiles = await this.requireClient().captureHouseProfile(this.typed);
      this.naming = false;
      this.typed = "";
      this.changed();
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.busy = false;
    }
  }

  private async rename(): Promise<void> {
    const from = this.renaming;
    if (from === null) return;
    this.busy = true;
    this.error = null;
    try {
      this.profiles = await this.requireClient().renameProfile(from, this.typed);
      this.renaming = null;
      this.typed = "";
      // The name is what a selection is made by, so renaming a profile the
      // house is on moves the house's own answer too: the page that holds this
      // control reloads rather than this element guessing which rows changed.
      this.changed();
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.busy = false;
    }
  }

  private async drop(name: string): Promise<void> {
    this.busy = true;
    this.error = null;
    try {
      this.profiles = await this.requireClient().removeProfile(name);
      this.changed();
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.busy = false;
    }
  }

  private changed(): void {
    this.dispatchEvent(
      new CustomEvent("profiles-changed", { bubbles: true, composed: true }),
    );
  }

  protected override render(): TemplateResult {
    const profiles = this.houseProfiles;
    return html`<div
      class="row spread wrap"
      style="margin-bottom:12px"
      data-house-profile
    >
      <div class="field">
        <div class="label-row">
          <span class="label">Whole house</span>
        </div>
        <select
          id="house-profile"
          aria-label="House profile"
          ?disabled=${!this.admin || this.busy}
          @change=${(event: Event) =>
            void this.put((event.target as HTMLSelectElement).value)}
        >
          <option value="" ?selected=${this.active === ""}>
            No profile: every room on its own
          </option>
          ${profiles.map(
            (one) => html`<option value=${one.name} ?selected=${one.active}>
              ${one.label}
            </option>`,
          )}
        </select>
      </div>
      <div class="row">
        ${this.active === ""
          ? nothing
          : html`<p class="help">
              ${profiles.find((one) => one.name === this.active)?.description ??
              ""}
            </p>`}
        <button
          type="button"
          id="house-profile-new"
          ?disabled=${!this.admin || this.busy}
          @click=${() => {
            this.typed = "";
            this.naming = true;
          }}
        >
          Take a profile from this house
        </button>
        ${this.active === ""
          ? nothing
          : html`
              <button
                type="button"
                id="house-profile-rename"
                ?disabled=${!this.admin || this.busy}
                @click=${() => {
                  this.typed = "";
                  this.renaming = this.active;
                }}
              >
                Rename
              </button>
              <button
                type="button"
                id="house-profile-delete"
                class="danger"
                ?disabled=${!this.admin || this.busy}
                @click=${() => void this.drop(this.active)}
              >
                Delete
              </button>
            `}
      </div>
      ${this.error ? this.errorBanner(this.error) : nothing}
      ${this.naming ? this.renderNaming() : nothing}
      ${this.renaming === null ? nothing : this.renderRenaming(this.renaming)}
    </div>`;
  }

  /**
   * The name a house profile is taken under.
   *
   * A name and nothing else, because there is nothing else to ask: what the
   * profile contains is the house, and the only decision a person makes here is
   * what to call it so they recognise it when they come back.
   */
  private renderNaming(): TemplateResult {
    return html`<open-house-dialog
      .heading=${"Take a profile from this house"}
      .open=${true}
      @dialog-closed=${() => {
        this.naming = false;
      }}
    >
      <div class="field">
        <label class="label" for="house-profile-name">Name</label>
        <input
          id="house-profile-name"
          type="text"
          class="grow"
          .value=${this.typed}
          placeholder="Evening in"
          @input=${(event: Event) => {
            this.typed = (event.target as HTMLInputElement).value;
          }}
        />
      </div>
      <p class="help">
        Everything this house is, named: every room's profile, the house's own
        settings and each room's. The house goes on it as it is taken, so
        nothing changes now — it is the name you come back to later.
      </p>
      <div class="row">
        <button
          type="button"
          id="house-profile-confirm"
          ?disabled=${this.busy || this.typed.trim() === ""}
          @click=${() => void this.take()}
        >
          Take it, and put the house on it
        </button>
      </div>
    </open-house-dialog>`;
  }

  /**
   * A new name for a profile the house is holding.
   *
   * One field and no warning about what a rename costs, because it costs
   * nothing: the profile moves under the new name and every room that was on it
   * is still on it. The sentence under the field says so, because "rename" on a
   * thing everything else refers to is exactly where a person expects to lose
   * the references.
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
        <label class="label" for="house-profile-rename-name">Name</label>
        <input
          id="house-profile-rename-name"
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
        it does not make a second one. Anything else that names it — a rule, a
        screen — names the new name afterwards.
      </p>
      <div class="row">
        <button
          type="button"
          id="house-profile-rename-confirm"
          ?disabled=${this.busy || this.typed.trim() === ""}
          @click=${() => void this.rename()}
        >
          Rename it
        </button>
      </div>
    </open-house-dialog>`;
  }
}

if (!customElements.get("open-house-house-profile")) {
  customElements.define("open-house-house-profile", OpenHouseHouseProfile);
}
