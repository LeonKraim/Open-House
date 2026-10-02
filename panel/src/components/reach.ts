/**
 * A behaviour's reach: where it applies, as a dropdown of tickable rooms.
 *
 * The question "where does this apply" has more than two answers, and a
 * yes/no chip asked a person to translate a place into a boolean in their head
 * -- "unticked" of what, exactly? A single-choice dropdown named the places
 * but could only name one, and an atom a person wants in the Kitchen *and* the
 * Hall had no way to say so. So the control is a dropdown that holds
 * checkboxes: one tick per place, and any number of them at once.
 *
 * The two kinds of place are the two the engine holds:
 *
 *   - a room, where the atom is switched on and runs in that room, and
 *   - the whole house, where the atom is widened (`scope: "house"`) and runs
 *     once for the house instead of once per room.
 *
 * The rooms are not a guess and not the module's placement: the engine
 * evaluates a room-declared behaviour in every room and its enable flag is what
 * decides which of them it runs in, so `behaviour.active_rooms` -- the engine's
 * own answer -- is the tick set. Ticking a room switches the atom on there;
 * unticking it switches it off there.
 */

import { html, type TemplateResult } from "lit";
import type { InstalledModule, RoomSummary } from "../api/models.ts";

/** The whole house, as a reach checkbox names it. `HOUSE` on the server side. */
export const HOUSE_REACH = "";

type Behaviour = InstalledModule["behaviours"][number];

/**
 * Every place one behaviour applies to: the house, or the rooms it runs in.
 *
 * A widened atom answers the house and no rooms, because `active_rooms` is the
 * rooms it runs *in* and a house-scoped atom runs in none of them -- it runs
 * once, above them all. A narrowed one answers the rooms its flag is on in, and
 * none at all when nobody has switched it on yet, which is the state the tick
 * boxes exist to leave.
 */
export function reachOf(behaviour: Behaviour): Set<string> {
  const places = new Set<string>(behaviour.active_rooms ?? []);
  if (behaviour.scope === "house") places.add(HOUSE_REACH);
  return places;
}

/**
 * The places as one short phrase, in the order the room list names them.
 *
 * Truncated rather than wrapped: the summary sits beside a checkbox in a row
 * that already wraps, and a summary that grew to a paragraph would push the
 * control it belongs to off the line.
 */
export function reachLabel(places: Set<string>, rooms: RoomSummary[]): string {
  const names = rooms
    .filter((room) => places.has(room.id))
    .map((room) => room.name);
  if (places.has(HOUSE_REACH)) names.unshift("The whole house");
  if (names.length === 0) return "Nowhere";
  if (names.length <= 2) return names.join(", ");
  return `${names.slice(0, 2).join(", ")} +${names.length - 2}`;
}

export interface ReachOptions {
  module: InstalledModule;
  behaviour: Behaviour;
  rooms: RoomSummary[];
  disabled: boolean;
  onToggle: (place: string, on: boolean) => void;
}

/** The reach control: a `<details>` whose menu is one checkbox per place. */
export function reachControl(options: ReachOptions): TemplateResult {
  const { module, behaviour, rooms, disabled, onToggle } = options;
  const label = behaviour.label || behaviour.id;
  const places = reachOf(behaviour);
  // A module installed into the house has no room to be narrowed to, so the
  // house tick is a statement of fact there rather than a choice.
  const houseFixed = module.room_id === HOUSE_REACH;
  return html`<details class="reach">
    <summary
      aria-label=${`Where ${label} applies`}
      title=${`${label} applies to ${reachLabel(places, rooms)}.`}
    >
      ${reachLabel(places, rooms)}
    </summary>
    <div class="reach-menu" role="group" aria-label=${`Where ${label} applies`}>
      <label
        class="check"
        title=${houseFixed
          ? "Installed in the whole house, so it always runs there."
          : behaviour.widenable
            ? "Runs once for the whole house instead of once per room."
            : "This one cannot run for the whole house: a room binds its devices."}
      >
        <input
          type="checkbox"
          .checked=${places.has(HOUSE_REACH)}
          ?disabled=${disabled || !behaviour.widenable || houseFixed}
          @change=${(event: Event) =>
            onToggle(
              HOUSE_REACH,
              (event.target as HTMLInputElement).checked,
            )}
        />
        <span>The whole house</span>
      </label>
      ${rooms.map(
        (room) => html`<label class="check">
          <input
            type="checkbox"
            .checked=${places.has(room.id)}
            ?disabled=${disabled}
            @change=${(event: Event) =>
              onToggle(room.id, (event.target as HTMLInputElement).checked)}
          />
          <span>${room.name}</span>
        </label>`,
      )}
      <p class="help" style="margin:2px 0 0">
        An atom runs in every room it is ticked in.
      </p>
    </div>
  </details>`;
}
