/**
 * How a binding's health reads and how it is coloured, spelled once.
 *
 * A slot's status is one of six closed values the server computes
 * (`_status` in `ha_adapter/live_modules.py`), and every table that draws a slot
 * has to turn one into a word and a chip class. Spelled at each table it is a
 * mapping that can drift -- the room's page calling `unknown` a warning while
 * another page calls it neutral, for one entity, in one house.
 *
 * These are the room and module pages' answers, which the module card shares
 * because it is drawn on the room's page and beside the room's own binding
 * table. `tabs/house.ts` keeps its own mapping, and deliberately: the house's
 * page draws *collected* roles, where `missing` means a room left a slot empty
 * rather than a device that is gone, and it colours that and `unknown` less
 * alarmingly. Folding the two together would have to pick one of the two
 * meanings for a word that has both.
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
