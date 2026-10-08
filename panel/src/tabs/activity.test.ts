/**
 * Unit tests for the Activity tab's two pieces of reasoning about data.
 *
 * The screen is mostly a table and is checked by looking at it -- the browser
 * round is where the journey is walked. What can be pinned here is what the tab
 * decides *from* the server's answer and knows nothing else about: how a
 * behaviour's id becomes a word a person reads, and which rows a room and an
 * outcome filter leave showing. The second is the one a wrong comparison would
 * hide silently -- an `=== ""` against the wrong side shows every row or none,
 * and a person cannot tell either from a working filter on a quiet house.
 */

import { strict as assert } from "node:assert";
import { test } from "node:test";

import type { DecisionLogEntry } from "../api/models.ts";
import { behaviourLabel, visibleEntries } from "./activity.ts";

test("a behaviour's id reads as words, not as a key", () => {
  // The exact shape the house's own system behaviours arrive in.
  assert.equal(behaviourLabel("system.update_available"), "System: update available");
  // One word, one capital.
  assert.equal(behaviourLabel("override"), "Override");
  // A snake_case name with no namespace.
  assert.equal(behaviourLabel("safety_alert"), "Safety alert");
});

test("the transform knows no vocabulary: a nested id reads the same way", () => {
  // Nothing here is translated by a table of known names -- the dots become
  // colons and the underscores spaces -- so a behaviour this panel has never
  // seen reads as well as one the packs ship. Only the *first* letter is
  // capitalised, so a namespaced id reads "Prefix: rest of it" (a sentence)
  // rather than "Prefix: Rest Of It" (a title).
  assert.equal(behaviourLabel("a.b.c"), "A: b: c");
  assert.equal(behaviourLabel("floor_heating.morning_boost"), "Floor heating: morning boost");
});

test("an empty id is empty, not a lone capital", () => {
  // `charAt(0)` on the empty string is the empty string, so the guard is that
  // the capital is only applied when there is something to capitalise.
  assert.equal(behaviourLabel(""), "");
});

/** One log entry, with only the fields the filters read. */
function entry_(
  room: string | null,
  outcome: DecisionLogEntry["outcome"],
): DecisionLogEntry {
  return { id: `${room ?? "house"}:${outcome}`, room, outcome } as DecisionLogEntry;
}

test("no filter shows every entry", () => {
  const entries = [entry_("kitchen", "applied"), entry_(null, "skipped")];
  assert.equal(visibleEntries(entries, "", "all").length, 2);
});

test("a room filter keeps only that room's entries", () => {
  const entries = [
    entry_("kitchen", "applied"),
    entry_("bedroom", "applied"),
    entry_(null, "blocked"),
  ];
  assert.deepEqual(
    visibleEntries(entries, "kitchen", "all").map((one) => one.room),
    ["kitchen"],
  );
});

test("an outcome filter keeps only that outcome", () => {
  const entries = [
    entry_("kitchen", "applied"),
    entry_("kitchen", "blocked"),
    entry_("kitchen", "applied"),
  ];
  assert.deepEqual(
    visibleEntries(entries, "", "applied").map((one) => one.outcome),
    ["applied", "applied"],
  );
});

test("the two filters narrow together, not one over the other", () => {
  // The case both being present is for: a room and an outcome, and the row that
  // answers both is the only one left.
  const entries = [
    entry_("kitchen", "applied"),
    entry_("kitchen", "blocked"),
    entry_("bedroom", "blocked"),
  ];
  const visible = visibleEntries(entries, "kitchen", "blocked");
  assert.equal(visible.length, 1);
  assert.equal(visible[0]?.room, "kitchen");
  assert.equal(visible[0]?.outcome, "blocked");
});

test("a filter that matches nothing is empty, not everything", () => {
  const entries = [entry_("kitchen", "applied")];
  assert.deepEqual(visibleEntries(entries, "ghost", "all"), []);
  assert.deepEqual(visibleEntries(entries, "", "error"), []);
});
