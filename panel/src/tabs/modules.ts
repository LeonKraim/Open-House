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
import { HOUSE_REACH, reachControl } from "../components/reach.ts";
import { withoutReachRoles } from "../components/schema-spec.ts";
import type { InstalledModule, RoomSummary } from "../api/models.ts";

export class ModulesTab extends OpenHouseElement {
  static override properties = {
    ...OpenHouseElement.properties,
    modules: { state: true },
    rooms: { state: true },
    isLoading: { state: true },
    error: { state: true },
    busy: { state: true },
    drafts: { state: true },
    dirty: { state: true },
  };

  private modules: InstalledModule[] = [];
  /**
   * Every room, because a behaviour's reach is a room.
   *
   * The dropdown beside each behaviour asks "where does this apply", and the
   * answers are the house's rooms -- one the page has to be able to name, so
   * this tab reads the room list as well as the modules.
   */
  private rooms: RoomSummary[] = [];
  private isLoading = true;
  private error: ReturnType<OpenHouseElement["toError"]> | null = null;
  private busy: string | null = null;
  /** Unsaved settings per module, keyed by pack, so one card's Save is its own. */
  private drafts: Record<string, Record<string, unknown>> = {};
  private dirty: Record<string, boolean> = {};

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
      const [modules, rooms] = await Promise.all([
        client.modules(),
        client.rooms(),
      ]);
      this.modules = modules;
      this.rooms = rooms;
      // A read is the server's answer, so every draft is dropped: keeping an
      // edit through a read that just told us the stored value would show a form
      // disagreeing with the engine.
      this.drafts = {};
      this.dirty = {};
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.isLoading = false;
      this.requestUpdate();
    }
  }

  /**
   * Save one module's settings, and only that module's.
   *
   * The command takes a map of values and writes each key it is given, so one
   * card's keys leave the others alone -- which is what makes the card, not the
   * page, the unit a person saves. A module placed in the house writes at house
   * scope; one in a room writes at that room's.
   */
  private async saveModule(module: InstalledModule): Promise<void> {
    const values = this.drafts[module.pack];
    if (values === undefined) return;
    this.busy = `options:${module.pack}`;
    this.error = null;
    this.requestUpdate();
    try {
      if (module.house) {
        await this.requireClient().setHouseOptions(values);
      } else {
        await this.requireClient().setRoomOptions(module.room_id, values);
      }
      await this.load();
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.busy = null;
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

  /**
   * Turn one behaviour of a pack on or off, leaving its siblings alone.
   *
   * The switch the modules screen exists to offer: "bedtime" is a bag of atoms
   * -- lights off, thermostat, locks -- and a person who wants the lights and
   * not the locks turns the one chip off rather than not installing the pack.
   * `busy` is keyed by pack *and* atom so the row's other chips stay live while
   * the one being written is disabled.
   */
  private async toggleBehaviour(
    module: InstalledModule,
    behaviour: InstalledModule["behaviours"][number],
  ): Promise<void> {
    this.busy = `${module.pack}:${behaviour.id}`;
    this.error = null;
    try {
      await this.requireClient().setModuleBehaviourEnabled(
        module.room_id,
        module.pack,
        behaviour.id,
        !behaviour.enabled,
      );
      await this.load();
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.busy = null;
      this.requestUpdate();
    }
  }

  /**
   * Tick or untick one place a behaviour applies to.
   *
   * The engine holds two kinds of place, and the control offers any number of
   * them at once. The whole house is the atom widened (`scope: "house"`), which
   * is one setting on the atom. A room is the atom's own enable flag in that
   * room: a room-scoped atom is *evaluated* in every room and runs in the ones
   * its flag is on in, so "run it in the Kitchen" is switching the atom on
   * there -- one module, switched on in as many rooms as are ticked, and the
   * ticks read back from the engine (`Engine.active_rooms`), never from a
   * second copy of the rule here.
   */
  private async setReach(
    module: InstalledModule,
    behaviour: InstalledModule["behaviours"][number],
    place: string,
    on: boolean,
  ): Promise<void> {
    this.busy = `${module.pack}:${behaviour.id}:scope`;
    this.error = null;
    this.requestUpdate();
    try {
      const client = this.requireClient();
      if (place === HOUSE_REACH) {
        await client.setModuleBehaviourScope(
          module.room_id,
          module.pack,
          behaviour.id,
          on ? "house" : "room",
        );
      } else {
        await client.setModuleBehaviourEnabled(
          place,
          module.pack,
          behaviour.id,
          on,
        );
      }
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
      <h1 style="margin-bottom:8px">Modules</h1>
      <p class="help">
        Every module installed anywhere in the house, each drawn as one card:
        what it does -- its behaviours, each with the act it performs -- and the
        settings that shape how it does it, together. A module is the unit a
        person installs and configures, so it is the unit this page draws.
      </p>
      ${this.modules.length === 0
        ? this.emptyState(
            "No modules installed",
            "Nothing is installed yet. Open a room and use \"Add module to room\", or browse the Store.",
          )
        : html`<div class="stack" style="margin-top:12px">
            ${this.modules.map((module) => this.renderCard(module))}
          </div>`}
    `;
  }

  /**
   * Where one behaviour applies: a dropdown of rooms, each one a checkbox.
   *
   * "This room or the whole house" is a *where*, and a two-word chip made a
   * person translate a place into a boolean in their head -- "unticked" of what,
   * exactly? A single-choice dropdown named the places but could only name one,
   * and an atom a person wants in the Kitchen *and* the Hall had no way to say
   * so. So the menu holds a checkbox per place, and the ticks are the answer.
   *
   * The house box is drawn disabled when the house scope cannot resolve the
   * atom's slots (`widenable`), because offering a choice the server refuses
   * would be the control lying about what is possible.
   */
  private renderReach(
    module: InstalledModule,
    behaviour: InstalledModule["behaviours"][number],
  ): TemplateResult {
    return reachControl({
      module,
      behaviour,
      rooms: this.rooms,
      disabled: this.busy !== null,
      onToggle: (place, on) => void this.setReach(module, behaviour, place, on),
    });
  }

  /**
   * One module as a card: what it does and how it is set, in one place.
   *
   * The behaviours and the settings are the same subject -- "the fridge has been
   * open too long" is a behaviour, and how long "too long" is is one of the
   * module's settings -- so the card is the module and everything it owns is
   * inside it, rather than the switches in one table and every module's numbers
   * in a form somewhere else.
   */
  private renderCard(module: InstalledModule): TemplateResult {
    const schema = withoutReachRoles(module.options_schema);
    const draft = { ...module.options, ...(this.drafts[module.pack] ?? {}) };
    const isDirty = this.dirty[module.pack] === true;
    return html`<div class="card">
      <div class="row spread wrap">
        <div class="stack">
          <h3>${module.name || module.pack}</h3>
          <p class="muted small">
            ${module.house
              ? html`<span class="chip" title="Installed into the whole house."
                  >the house</span
                >`
              : html`<a
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
                >`}
            &middot; v${module.version}
            ${module.satisfiable
              ? null
              : html`<span
                  class="chip"
                  title=${`Cannot be switched on until ${
                    module.house ? "the house binds" : "this room binds"
                  } ${module.missing_slots.join(", ")}.`}
                  >needs ${module.missing_slots.join(", ")}</span
                >`}
            <span class="chip ${module.enabled ? "ok" : "warn"}"
              >${module.enabled ? "enabled" : "disabled"}</span
            >
          </p>
        </div>
        ${this.admin
          ? html`<div class="row">
              <button
                type="button"
                class="icon"
                ?disabled=${this.busy !== null ||
                (!module.enabled && !module.satisfiable)}
                title=${!module.enabled && !module.satisfiable
                  ? `Bind ${module.missing_slots.join(", ")} on ${
                      module.house ? "a room's" : "the room's"
                    } settings first.`
                  : ""}
                @click=${() => void this.toggle(module)}
              >
                ${module.enabled ? "Disable" : "Enable"}
              </button>
              <button
                type="button"
                class="icon danger"
                ?disabled=${this.busy !== null}
                @click=${() => void this.uninstall(module)}
              >
                Remove
              </button>
            </div>`
          : nothing}
      </div>

      ${module.behaviours.length === 0
        ? null
        : html`<div class="stack" style="margin-top:10px">
            <span class="muted small">Does this:</span>
            ${module.behaviours.map((behaviour) => this.renderBehaviour(module, behaviour))}
          </div>`}
      ${schema === null
        ? null
        : html`<div style="margin-top:10px">
            <div class="row spread">
              <span class="muted small">Settings</span>
              ${this.admin && isDirty
                ? html`<div class="row">
                    <button
                      type="button"
                      ?disabled=${this.busy !== null}
                      @click=${() => {
                        const { [module.pack]: _dropped, ...rest } = this.drafts;
                        this.drafts = rest;
                        this.dirty = { ...this.dirty, [module.pack]: false };
                      }}
                    >
                      Discard
                    </button>
                    <button
                      type="button"
                      class="primary"
                      ?disabled=${this.busy !== null}
                      @click=${() => void this.saveModule(module)}
                    >
                      Save
                    </button>
                  </div>`
                : null}
            </div>
            <open-house-schema-form
              .schema=${schema}
              .values=${draft}
              .readonly=${!this.admin}
              @value-changed=${(event: CustomEvent<Record<string, unknown>>) => {
                this.drafts = { ...this.drafts, [module.pack]: event.detail };
                this.dirty = { ...this.dirty, [module.pack]: true };
              }}
            ></open-house-schema-form>
          </div>`}
    </div>`;
  }

  /**
   * One behaviour: its switch, its reach, and a sentence saying what it does.
   *
   * The description is the whole point -- a chip reading "The fridge has been
   * open too long" names the behaviour and says nothing about the act, so the
   * person deciding whether to flip it is guessing. The server builds the
   * sentence from the behaviour's own declaration (`_behaviour_summary`); the
   * panel renders it and invents none of it.
   */
  private renderBehaviour(
    module: InstalledModule,
    behaviour: InstalledModule["behaviours"][number],
  ): TemplateResult {
    const ready = module.satisfiable || behaviour.enabled;
    return html`<div class="stack">
      <div class="row wrap">
        <label class="toggle">
          <input
            type="checkbox"
            .checked=${behaviour.enabled}
            ?disabled=${this.busy !== null || !ready || !this.admin}
            title=${!ready
              ? `Bind ${module.missing_slots.join(", ")} on ${
                  module.house ? "a room's" : "the room's"
                } settings first.`
              : ""}
            @change=${() => void this.toggleBehaviour(module, behaviour)}
          />
          <span>${behaviour.label || behaviour.id}</span>
        </label>
        ${this.renderReach(module, behaviour)}
      </div>
      ${behaviour.description
        ? html`<p class="help" style="margin:0 0 0 2px">${behaviour.description}</p>`
        : null}
    </div>`;
  }
}

if (!customElements.get("open-house-tab-modules")) {
  customElements.define("open-house-tab-modules", ModulesTab);
}
