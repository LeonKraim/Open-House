/**
 * A behaviour's priority: the number that settles a disagreement.
 *
 * Two modules do not have to be in conflict to want opposite things about one
 * device -- a motion rule and a bedtime shutdown both propose for the hall light
 * in the same tick -- and the engine's arbitration reduces the pair to one
 * command by the higher `priority`, with the pack's own order as the tie-break.
 * A pack declares a rank per behaviour and the catalog publishes a default for
 * the ones that say nothing, but *which* of two rival modules a household would
 * rather have win is a fact about that household, and it is the one thing the
 * pack cannot know. So the rank is editable here, per module interaction, and
 * the declared rank is kept beside it as the thing "reset" returns to.
 *
 * The control is a number field and a reset, and nothing else: the value is a
 * plain integer with no bounds (the manifest schema puts none on it either), so
 * a slider would have to invent a range and a stepper would make the interesting
 * direction the slow one. The two facts a person needs -- the rank now, and
 * whether they are the one who set it -- are the two the server sends
 * (`priority`, `priority_set`), so this compares nothing and derives nothing.
 */

import { html, type TemplateResult } from "lit";
import type { InstalledModule } from "../api/models.ts";

type Behaviour = InstalledModule["behaviours"][number];

export interface PriorityOptions {
  behaviour: Behaviour;
  disabled: boolean;
  /** A new rank, as the person typed it. Always a whole number. */
  onSet: (priority: number) => void;
  /** Put the rank back to what the pack declared. */
  onReset: () => void;
}

/** The rank, as one compact control: a number field, and a reset when earned. */
export function priorityControl(options: PriorityOptions): TemplateResult {
  const { behaviour, disabled, onSet, onReset } = options;
  const label = behaviour.label || behaviour.id;
  // A field the person is typing into must not be rewritten from under them, so
  // the *value* is the server's answer and a rejected entry is corrected on the
  // next render rather than mid-keystroke. `change` (not `input`) is what makes
  // that work: the correction lands when the field is left, which is also when
  // a half-typed "-" stops being a number at all.
  return html`<label
    class="priority"
    title=${`${label} ranks ${behaviour.priority}. When two modules want one ` +
    `device in the same tick, the higher rank wins. The pack's own rank is ` +
    `${behaviour.default_priority}.`}
  >
    <span class="muted small">Priority</span>
    <input
      type="number"
      step="1"
      .value=${String(behaviour.priority)}
      ?disabled=${disabled}
      aria-label=${`Priority for ${label}`}
      @change=${(event: Event) => {
        const field = event.target as HTMLInputElement;
        const typed = Number(field.value);
        if (!Number.isInteger(typed)) {
          // The server refuses a non-integer and the schema says `integer`; a
          // field left holding "2.5" would be a panel showing a rank the house
          // does not have, so it snaps back to the rank it does have.
          field.value = String(behaviour.priority);
          return;
        }
        if (typed !== behaviour.priority) onSet(typed);
      }}
    />
    ${behaviour.priority_set
      ? html`<button
          type="button"
          class="icon"
          ?disabled=${disabled}
          title=${`Put this back to the pack's own rank, ${behaviour.default_priority}.`}
          @click=${() => onReset()}
        >
          Reset
        </button>`
      : null}
  </label>`;
}
