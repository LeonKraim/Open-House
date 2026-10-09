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

import type { PublishedRow, StoreStatus } from "../api/models.ts";
import {
  browseRows,
  claimProblem,
  installRefusal,
  publishBlockers,
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

/** A status with only the two fields the publish pre-flight reads. */
function status(over: Partial<StoreStatus> = {}): StoreStatus {
  return { url: "https://store.example", name: "sam", ...over };
}

test("a house that has a Store and a name has nothing left to ask for", () => {
  // Not the same as "the dialog does not open": it opens on every publish,
  // because the description is a thing to set each time. What is empty here is
  // the set of *facts* it has to collect before it can send anything.
  assert.deepEqual(publishBlockers(status()), []);
});

test("the name is asked for until this house has claimed one", () => {
  assert.deepEqual(publishBlockers(status({ name: "" })), ["name"]);
});

test("the address is never something a publish asks for", () => {
  // The address is answered before any screen draws: it is the one this build
  // ships with, so a publish has one whether or not anybody typed it. Asking
  // here made a person retype a question the repository had already answered,
  // and put the Store in front of the module they pressed Publish on. A house
  // wanting a Store of its own sets the option, which is the Connect form and
  // not this.
  assert.deepEqual(publishBlockers(status({ url: "", name: "" })), ["name"]);
  assert.deepEqual(publishBlockers(status({ url: "" })), []);
});

test("a status that has not been read is not a missing name", () => {
  // `null` is "no answer yet", not "nothing configured": asking for a name
  // because a read had not landed would be asking for something that may well
  // already be set. Nothing is known to be missing, so nothing is asked for and
  // the publish itself answers if it turns out something was.
  assert.deepEqual(publishBlockers(null), []);
});
