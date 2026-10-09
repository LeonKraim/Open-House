/**
 * Unit tests for the Store tab's reasoning about the published Store.
 *
 * The screen itself is checked by looking at it -- the browser walk is what
 * proves the filter, the stars and the comment tray render -- but the decisions
 * behind them are pure and are pinned here: which of the two lists the filter
 * shows, what a rating reads as when nobody has rated it, the star band, the
 * sentence a refused install becomes, and the claim form's own validation.
 *
 * These are exactly the answers a walk cannot easily see: a null rating that
 * quietly renders as zero, a name with a capital that is let through to a round
 * trip, a refusal read as a connection failure and so offered no Replace.
 */

import { strict as assert } from "node:assert";
import { test } from "node:test";

import type { PublishedRow } from "../api/models.ts";
import {
  browseRows,
  claimProblem,
  installRefusal,
  ratingLabel,
  starBand,
} from "./store.ts";

/** One published row, with only the fields a test reads given. */
function published(over: Partial<PublishedRow> = {}): PublishedRow {
  return {
    id: "r1",
    slug: "r1",
    title: "A module",
    summary: "",
    publisher: "sam",
    mine: false,
    version: "1.0.0",
    tier: "community",
    review: "",
    rating: null,
    stars_count: 0,
    comments: 0,
    installs: 0,
    updated: "2026-01-01T00:00:00Z",
    ...over,
  };
}

test("the filter shows the half it was asked for, and only that half", () => {
  const here = published({ id: "here", title: "Here" });
  const away = published({ id: "away", title: "Away" });
  const split = { installed: [here], not_installed: [away] };
  assert.deepEqual(browseRows(split, "installed"), [here]);
  assert.deepEqual(browseRows(split, "not_installed"), [away]);
});

test("a side with nothing in it is an empty list, not the other side", () => {
  // The trap this guards: a filter that fell back to the whole list when one
  // half was empty would show installed modules under "Not installed", which is
  // the one thing the filter exists to stop.
  const split = { installed: [published()], not_installed: [] };
  assert.deepEqual(browseRows(split, "not_installed"), []);
});

test("a module nobody has rated says so, and does not read as zero", () => {
  // `null` is not zero: nought stars is a rating somebody gave, and a screen
  // that drew 0.0 for an unrated module would be saying something no person
  // said. This is the case a walk is most likely to miss.
  assert.equal(ratingLabel(null, 0), "not rated yet");
});

test("a rating reads as the average and the number of people", () => {
  assert.equal(ratingLabel(4.5, 3), "4.5 from 3 people");
});

test("one rater is one person, not one people", () => {
  assert.equal(ratingLabel(5, 1), "5.0 from 1 person");
});

test("an unrated module draws an empty band", () => {
  assert.equal(starBand(null), "");
});

test("the band is filled to the nearest whole star", () => {
  assert.equal(starBand(4), "★★★★☆");
  assert.equal(starBand(5), "★★★★★");
  assert.equal(starBand(1), "★☆☆☆☆");
});

test("a refusal about a name already held offers the replace", () => {
  // `invalid_format` is the code for a value refused on its merits, which is
  // what "a module of that name is already here" is -- so the row shows the
  // Store's sentence and a Replace confirm.
  const refusal = installRefusal({
    code: "invalid_format",
    message: "this house already holds a module called porch_lamp",
  });
  assert.equal(refusal.replace, true);
  assert.equal(refusal.sentence, "this house already holds a module called porch_lamp");
});

test("a transport failure is not a replace, and shows its own sentence", () => {
  const refusal = installRefusal({ code: "unavailable", message: "Home Assistant is not reachable." });
  assert.equal(refusal.replace, false);
  assert.equal(refusal.sentence, "Home Assistant is not reachable.");
});

test("a thrown value with no code still yields a sentence", () => {
  // A plain `Error` carries no code, which `asPanelError` maps to `unknown`;
  // the sentence is still the one the caller threw rather than a blank.
  const refusal = installRefusal(new Error("boom"));
  assert.equal(refusal.replace, false);
  assert.equal(refusal.sentence, "boom");
});

test("a name that is empty is refused before the request", () => {
  assert.equal(claimProblem(""), "Pick a name to publish under.");
  assert.equal(claimProblem("   "), "Pick a name to publish under.");
});

test("a name with a space is refused before the request", () => {
  assert.ok(claimProblem("marq barq") !== null);
});

test("a name with a capital is refused before the request", () => {
  assert.ok(claimProblem("Marqbarq") !== null);
});

test("a lower case name with dashes and digits is accepted", () => {
  assert.equal(claimProblem("marqbarq"), null);
  assert.equal(claimProblem("marq-baq_2"), null);
  // Surrounding space is trimmed rather than refused: the button sends the
  // trimmed name, so a name a person pasted with a stray space is a name.
  assert.equal(claimProblem("  marqbarq  "), null);
});
