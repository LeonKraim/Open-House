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

import { html, nothing, type TemplateResult } from "lit";
import { OpenHouseElement } from "../base.ts";
import { SchemaForm } from "../components/schema-form.ts";
import type {
  ModuleOffer,
  ModuleOfferRow,
  ModuleConflict,
} from "../api/models.ts";

export class AddModuleDialog extends OpenHouseElement {
  static override properties = {
    ...OpenHouseElement.properties,
    open: { type: Boolean },
    roomId: { type: String },
    // The dialog's heading, because the same dialog serves a room and the house:
    // the house is a placement a person can install into (`roomId` is `""`), and
    // a form headed "Add module to room" over the House tab would name a room the
    // person is not on.
    heading: { type: String },
    offers: { state: true },
    stored: { state: true },
    isLoading: { state: true },
    error: { state: true },
    busyPack: { state: true },
    // The filter is typed into, not fetched, so it has to be reactive or the
    // list never narrows (see base.ts).
    filter: { state: true },
  };

  declare open: boolean;
  declare roomId: string;
  declare heading: string;

  private offers: ModuleOffer[] = [];
  /**
   * The modules this house kept, from its own store.
   *
   * A different kind of offer from a catalog pack, and offered here rather than
   * in the Store tab for exactly that reason: the store is where a module is
   * *kept*, and this dialog is where it is *placed*. A store module is already
   * installed -- there is nothing to buy and no version to compare -- so its row
   * carries its slots and one button, and the button adds it here.
   */
  private stored: ModuleOfferRow[] = [];
  private isLoading = false;
  private error: ReturnType<OpenHouseElement["toError"]> | null = null;
  private busyPack: string | null = null;
  private filter = "";

  constructor() {
    super();
    this.open = false;
    this.roomId = "";
    this.heading = "Add module to room";
  }

  override updated(changed: Map<string, unknown>): void {
    if (changed.has("open") && this.open) {
      // **The dialog is reused, so its state is not.** A room and the house
      // both keep one `<open-house-add-module>` and only flip `.open`, so a
      // filter typed into the last attempt would narrow this one -- and a
      // filter that matches nothing would greet the next open with "No module
      // matches that filter." about words nobody typed this time.
      this.filter = "";
      void this.load();
    }
  }

  private async load(): Promise<void> {
    this.isLoading = true;
    this.error = null;
    this.requestUpdate();
    const client = this.requireClient();
    // The two are asked separately, and the store's failure is swallowed: a
    // house that cannot answer for its own modules still has a catalog to offer
    // packs from, and a dialog that opened empty for that reason would hide
    // every pack behind a fault in the half nobody asked about.
    const [offers, store] = await Promise.allSettled([
      client.availableModules(this.roomId),
      client.modulesStore(this.roomId),
    ]);
    if (offers.status === "fulfilled") this.offers = offers.value;
    else this.error = this.toError(offers.reason);
    this.stored = store.status === "fulfilled" ? store.value.store : [];
    this.isLoading = false;
    this.requestUpdate();
  }

  /**
   * Put one of this house's own modules into the room this dialog is over.
   *
   * The answers are the definition's -- a stored module installs as what it is,
   * which is the whole of what defining it once bought -- so nothing is sent but
   * the placement. A room that cannot answer one of its slots gets the module
   * anyway and it waits; the room's page is where the device is then bound.
   */
  private async addStored(offer: ModuleOfferRow): Promise<void> {
    this.busyPack = `store:${offer.slug}`;
    this.error = null;
    try {
      await this.requireClient().modulesDeploy(offer.slug, this.roomId);
      this.dispatchEvent(
        new CustomEvent("module-installed", {
          detail: { pack: offer.slug },
          bubbles: true,
        }),
      );
      this.open = false;
      this.dispatchEvent(new CustomEvent("add-module-closed", { bubbles: true }));
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.busyPack = null;
      this.requestUpdate();
    }
  }

  private async install(offer: ModuleOffer): Promise<void> {
    // Unsatisfiable is installable. A module whose devices are not configured
    // yet goes in and arrives disabled; that is what makes the room's devices
    // configurable in the first place, since they are the modules' slots. Only
    // a blocking conflict or a module already in the room stops an install.
    if (this.blocking(offer).length > 0 || offer.already_installed) return;
    this.busyPack = offer.pack;
    this.error = null;
    try {
      await this.requireClient().installModule(this.roomId, offer.pack);
      this.dispatchEvent(
        new CustomEvent("module-installed", {
          detail: { pack: offer.pack },
          bubbles: true,
        }),
      );
      this.open = false;
      // `add-module-closed`, not `closed`: the room's settings page is not the
      // only ancestor that reads a bare `closed`, and the tab above it reads
      // one as "leave the room". See `components/dialog.ts`.
      this.dispatchEvent(new CustomEvent("add-module-closed", { bubbles: true }));
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.busyPack = null;
      this.requestUpdate();
    }
  }

  /**
   * Whether this dialog was opened on the house rather than a room.
   *
   * The same dialog serves both, and the house's placement is the empty room
   * id -- the same spelling the whole module API uses for it. The copy that
   * names the target ("in the room", "in the house") is derived from this
   * rather than from a second flag, so the two cannot disagree.
   */
  private get isHouse(): boolean {
    return this.roomId === "";
  }

  private blocking(offer: ModuleOffer): ModuleConflict[] {
    return offer.conflicts.filter((conflict) => conflict.severity === "blocking");
  }

  private warnings(offer: ModuleOffer): ModuleConflict[] {
    return offer.conflicts.filter((conflict) => conflict.severity === "warning");
  }

  protected override render(): TemplateResult {
    // Modules and behaviours, and no other kind. `roomAvailableModules` answers
    // with every published pack and carries each one's `kind` precisely so this
    // screen can decide. A `module` does something in a room: it is plugged into
    // the room's placeholders and carries the behaviours that act through them. A
    // `behavior` is the same thing narrowed to one behaviour offered on its own --
    // an atom a person adds to a room exactly as they add a module -- which is why
    // it is offered here too. A `room-template` is not a thing a room can be
    // given: it declares what a room *has* (its slots), never what it *does*, and
    // the manifest schema refuses it a behaviour. Offered here under an Install
    // button it was a trap -- five of the nine packs this house publishes are
    // templates, so a person could install "Kitchen room", see it appear in the
    // Modules list, and find that every Enable button did nothing, because
    // `_enabled` is `False` for a pack that registered no behaviours and always
    // will be. `house-template` and `profile-set` are the same trap one level up.
    const modules = this.offers.filter(
      (offer) => offer.kind === "module" || offer.kind === "behavior",
    );
    const visible = modules.filter((offer) =>
      this.filter.trim() === ""
        ? true
        : `${offer.name} ${offer.pack} ${offer.description}`
            .toLowerCase()
            .includes(this.filter.trim().toLowerCase()),
    );
    // The same dialog serves a room and the house, so the copy that names the
    // target has to be the target's. `this.roomId` is `""` for the house, which
    // is the placement the whole module API spells the house with.
    const where = this.isHouse ? "house" : "room";
    const whereThe = this.isHouse ? "the house" : "this room";
    return html`<open-house-dialog
      .heading=${this.heading || `Add module to ${where}`}
      .open=${this.open}
      @dialog-closed=${() => {
        this.open = false;
        this.dispatchEvent(new CustomEvent("add-module-closed", { bubbles: true }));
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
      ${this.renderStored()}
      ${this.isLoading
        ? this.loading(`Looking for modules ${whereThe} can satisfy...`)
        : visible.length === 0
          ? this.emptyState(
              "No modules",
              modules.length === 0
                ? this.stored.length === 0
                  ? `No pack in the house or the store offers anything ${whereThe} can take yet.`
                  : "No catalog packs to add. Your own modules are above."
                : "No module matches that filter.",
            )
          : html`<div class="stack" style="margin-top:12px">
              ${visible.map((offer) => this.renderOffer(offer))}
            </div>`}
    </open-house-dialog>`;
  }

  /**
   * The modules this house kept, ahead of the catalog.
   *
   * First because they are the person's own: a module saved out of an automation
   * in the Dev tab is the one they came here to place, and the catalog is what
   * they browse when they have nothing of their own. The two lists are drawn
   * differently and say so, because a pack is a thing a room *satisfies* and a
   * stored module is a thing this house already has.
   */
  private renderStored(): TemplateResult | typeof nothing {
    if (this.stored.length === 0) return nothing;
    const whereThe = this.isHouse ? "the house" : "this room";
    return html`<section style="margin-top:12px">
      <h3>Your modules</h3>
      <p class="help">
        Saved in your store. Adding one to ${whereThe} makes a copy of it here,
        with its own automation and its own outputs -- the module itself stays
        where it is, ready to add somewhere else too.
      </p>
      <div class="stack">
        ${this.stored.map((offer) => this.renderStoredRow(offer))}
      </div>
    </section>`;
  }

  private renderStoredRow(offer: ModuleOfferRow): TemplateResult {
    const busy = this.busyPack === `store:${offer.slug}`;
    const here = offer.deployed.some((where) => where.room_id === this.roomId);
    const missing = offer.missing_slots;
    return html`<div class="card" data-module=${offer.slug}>
      <div class="row spread wrap">
        <div class="grow">
          <h3>${offer.title}</h3>
          <p class="muted small">
            <code>${offer.slug}</code>
            &middot; v${offer.version} &middot; ${offer.licence}
          </p>
        </div>
        ${here
          ? html`<span class="chip ok">already here</span>`
          : html`<button
              type="button"
              class="primary"
              id="add-stored-${offer.slug}"
              ?disabled=${busy}
              @click=${() => void this.addStored(offer)}
            >
              ${busy
                ? "Adding..."
                : this.isHouse
                  ? "Add to the house"
                  : "Add to this room"}
            </button>`}
      </div>
      <p>${offer.description}</p>
      ${offer.slots.length > 0
        ? html`<div class="row wrap" style="margin-top:6px">
            ${offer.slots.map(
              (slot) =>
                html`<span class="chip ${missing.includes(slot) ? "error" : "ok"}"
                  >${slot}${missing.includes(slot) ? " (missing)" : ""}</span
                >`,
            )}
          </div>`
        : nothing}
      ${missing.length > 0
        ? html`<div class="banner warn">
            ${this.isHouse ? "This house" : "This room"} has not been given a
            device for ${missing.join(", ")} yet. Add it anyway and the module
            waits -- bind the device on a room's settings page and it runs.
          </div>`
        : nothing}
    </div>`;
  }

  private renderOffer(offer: ModuleOffer): TemplateResult {
    const blocked = this.blocking(offer);
    const warns = this.warnings(offer);
    const installable = blocked.length === 0 && !offer.already_installed;
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
      ${!offer.satisfiable && blocked.length === 0 && !offer.already_installed
        ? html`<div class="banner warn">
            ${this.isHouse ? "This house" : "This room"} has not been given the devices
            it needs yet: ${offer.missing_slots.join(", ")}. Install it anyway and
            it arrives switched off; bind those devices on a room's settings page
            to be able to switch it on.
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
    if (offer.already_installed) {
      return this.isHouse
        ? "This module is already installed in the house."
        : "This module is already installed in the room.";
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
