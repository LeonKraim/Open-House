/**
 * The parts one slot has been split into, and how a person changes them.
 *
 * A part is a role's half -- `light_group` split into `a` and `b` -- and it is
 * **still the same slot**: one role, one name. That is the whole design, and it
 * is why this block is drawn *inside* the slot's row rather than in rows of its
 * own: two rows side by side would read as two roles that happen to share a
 * prefix, and a person would have to work out from the names that they are halves
 * of one thing.
 *
 * Each part is bound **once**, here, and everything on that part acts on that one
 * device -- which is what makes two modules sharing a part a fact rather than a
 * promise. So the device control is on this block and not on a module's row; a
 * module's row only says *which* part it is on (`components/slot-devices.ts`).
 *
 * Three actions, and the third can be refused: deleting a part a module still
 * names is a change that would move every module on it, so the server refuses it
 * in a sentence naming them and the page shows that sentence where it shows every
 * other refusal. Nothing here silently unbinds anything.
 *
 * Drawn by both pages that own a slot -- a room's and the House tab's -- because
 * the two are the same control over the same house-level record: a part is not a
 * room's, and a House tab that could split a role while a room's page could only
 * see the halves would be two answers to one question.
 */

import { html, nothing, type TemplateResult } from "lit";
import type { SlotPart } from "../api/models.ts";
import { STATUS_CHIP, STATUS_LABEL } from "./binding-status.ts";

/** One action a person can take on a part, in the server's own spelling. */
export type SlotPartAction = "add" | "rename" | "remove";

export interface SlotPartsOptions {
  /** The slot being split, by its binding key. */
  slot: string;
  /** The halves it has been split into, with the device each is bound to. */
  parts: SlotPart[];
  /** False for a reader who may not write: the block is drawn and inert. */
  admin: boolean;
  /** True while a write is in flight, which disables every control. */
  busy: boolean;
  /**
   * The name being typed, for one key of this block, or `undefined` when that
   * control is closed.
   *
   * Asked per key rather than handed down as one value, because this block has
   * two name fields -- one for a new part and one for each rename -- and a single
   * value would open all of them at once. The *page* holds the map (a row is
   * rebuilt on every `requestUpdate`, and a half-typed name in a control that was
   * rebuilt is a name a person types twice), so the page is asked.
   */
  draft: (key: string) => string | undefined;
  onDraft: (key: string, value: string | null) => void;
  /** Bind, or rebind, the device one half acts on. */
  onBind: (part: SlotPart) => void;
  /**
   * Take the device off one half, leaving the half in place.
   *
   * Here because a *removal* is refused while the part is bound: without this,
   * a person who gave both halves a device and then wanted the slot whole again
   * would have no way out -- Delete would refuse, naming a binding nothing on the
   * page could take off. It is the same control a room's slot row draws, one row
   * down, and it is drawn only when there is something to take off.
   */
  onUnbind: (part: SlotPart) => void;
  /** Split, rename or rejoin -- the three the server takes. */
  onAct: (action: SlotPartAction, name: string, newName?: string) => void;
}

/** The draft key for a new part of a slot, or a rename of one that exists. */
export function partDraftKey(slot: string, part?: string): string {
  return part === undefined ? slot : `${slot}/${part}`;
}

/** The parts block for one slot: each half, its device, and what can be done. */
export function slotParts(options: SlotPartsOptions): TemplateResult {
  const { slot, parts, admin, busy } = options;
  const draft = options.draft(partDraftKey(slot));
  const inert = busy || !admin;
  return html`<div class="stack" style="margin-top:6px;gap:4px">
    ${parts.length === 0 && draft === undefined
      ? nothing
      : html`<span class="muted small">Parts</span>`}
    ${parts.map((part) => partRow(options, part))}
    ${!admin
      ? nothing
      : draft === undefined
        ? html`<button
            type="button"
            class="icon"
            data-add-part=${slot}
            @click=${() => options.onDraft(partDraftKey(slot), "")}
            >Add a part</button
          >`
        : html`<div class="row wrap" style="align-items:center;gap:6px">
            <input
              type="text"
              style="min-width:8rem"
              aria-label="The new part's name"
              placeholder="a"
              .value=${draft}
              @input=${(event: Event) =>
                options.onDraft(
                  partDraftKey(slot),
                  (event.target as HTMLInputElement).value,
                )}
            />
            <button
              type="button"
              class="icon primary"
              ?disabled=${inert || draft.trim() === ""}
              @click=${() => options.onAct("add", draft.trim())}
              >Add it</button
            >
            <button type="button" @click=${() => options.onDraft(partDraftKey(slot), null)}>
              Not now
            </button>
            <span class="muted small"
              >Two modules on one part share its device; put them on different
              parts and each half is bound here.</span
            >
          </div>`}
  </div>`;
}

/** One half: its name, its device, and the three things a person can do to it. */
function partRow(options: SlotPartsOptions, part: SlotPart): TemplateResult {
  const { slot, busy, admin } = options;
  const key = partDraftKey(slot, part.name);
  const renaming = options.draft(key);
  const inert = busy || !admin;
  return html`<div
    class="row wrap"
    style="align-items:center;gap:6px"
    data-slot-part-row=${part.slot}
  >
    ${renaming === undefined
      ? html`<span data-part-name=${part.slot}>${part.label}</span>`
      : html`<input
            type="text"
            style="min-width:8rem"
            aria-label=${`The new name for the ${part.label} part`}
            .value=${renaming}
            @input=${(event: Event) =>
              options.onDraft(key, (event.target as HTMLInputElement).value)}
          />
          <button
            type="button"
            class="icon primary"
            ?disabled=${inert || renaming.trim() === ""}
            @click=${() =>
              options.onAct("rename", part.name, renaming.trim())}
            >Rename it</button
          >
          <button type="button" @click=${() => options.onDraft(key, null)}>
            Cancel
          </button>`}
    <span class="chip ${STATUS_CHIP[part.status]}">${STATUS_LABEL[part.status]}</span>
    <span class="muted small">${part.entity_id ?? "nothing bound"}</span>
    ${renaming !== undefined || !admin
      ? nothing
      : html`<span class="grow"></span>
          <button
            type="button"
            class="icon"
            ?disabled=${busy}
            @click=${() => options.onBind(part)}
            >${part.entity_id === null ? "Bind" : "Replace"}</button
          >
          ${part.entity_id === null
            ? nothing
            : html`<button
                type="button"
                class="icon"
                ?disabled=${busy}
                @click=${() => options.onUnbind(part)}
                >Unbind</button
              >`}
          <button
            type="button"
            class="icon"
            ?disabled=${busy}
            @click=${() => options.onDraft(key, part.name)}
            >Rename</button
          >
          <button
            type="button"
            class="icon"
            ?disabled=${busy}
            @click=${() => options.onAct("remove", part.name)}
            >Delete</button
          >`}
  </div>`;
}
