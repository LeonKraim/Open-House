/**
 * How a binding's health reads and how it is coloured, spelled once.
 *
 * A slot's status is one of six closed values the server computes
 * (`_status` in `ha_adapter/live_modules.py`), and every table that draws a slot
 * has to turn one into a word and a chip class. Spelled at each table it is a
 * mapping that can drift -- the room's page calling `unknown` a warning while
 * another page calls it neutral, for one entity, in one house.
 *
 * There are two mappings, and they are two because the same word means two
 * things. The room and module pages draw a *bound* slot, where `missing` is a
 * device that is gone and `unknown` is one Home Assistant cannot report on, and
 * both are coloured alarmingly. The house's page draws *collected* roles, where
 * a role only reaches the house because a room left its own binding empty and
 * so `missing` is the ordinary state rather than a fault, and it colours that
 * and `unknown` less alarmingly. Folded together, one word would have to carry
 * both meanings and be wrong in one of the two places; kept beside each other
 * here, the divergence is visible in one file instead of drifting between two.
 */

import type { BindingStatusKind } from "../api/models.ts";

/** The word a status is drawn as. */
export const STATUS_LABEL: Record<BindingStatusKind, string> = {
  ok: "ok",
  unavailable: "unavailable",
  unknown: "unknown",
  missing: "missing",
  domain_mismatch: "wrong domain",
  unbound: "unbound",
};

/** The chip class a status is drawn with: `ok`, `warn`, `error`, or none. */
export const STATUS_CHIP: Record<BindingStatusKind, string> = {
  ok: "ok",
  unavailable: "warn",
  unknown: "warn",
  missing: "error",
  domain_mismatch: "error",
  unbound: "",
};

/**
 * The house's page answers, where a collected role reads differently.
 *
 * `missing` is a room that left the role empty -- the house slot is standing in
 * for a room's binding, and there being none is why the global one is drawn at
 * all -- so it is a warning and not an error, and `unknown` is neutral because a
 * role no module reaches yet is not something Home Assistant failed to report.
 */
export const HOUSE_STATUS_LABEL: Record<BindingStatusKind, string> = {
  ok: "ok",
  unavailable: "unavailable",
  unknown: "unknown",
  missing: "missing",
  domain_mismatch: "wrong domain",
  unbound: "unbound",
};

/** The chip class for the house's *collected* roles. See `HOUSE_STATUS_LABEL`. */
export const HOUSE_STATUS_CHIP: Record<BindingStatusKind, string> = {
  ok: "ok",
  unavailable: "warn",
  unknown: "",
  missing: "warn",
  domain_mismatch: "warn",
  unbound: "",
};
