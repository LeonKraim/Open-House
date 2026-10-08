/**
 * Unit tests for the Dev tab's one rule about data.
 *
 * The tab is mostly a screen, and a screen is tested by looking at it — the
 * browser round is where the journey is checked. What can be pinned here is the
 * small piece of reasoning the screen does about the server's answer, and the
 * export's opening room is the piece worth pinning: it is the difference
 * between an export that names the entities the module acts on and one that
 * names none.
 */

import { strict as assert } from "node:assert";
import { test } from "node:test";

import type { DevValueRow, InstalledModule } from "../api/models.ts";
import {
  placementRoom,
  secondsPerUnit,
  waitableValues,
  waitedRow,
} from "./dev.ts";

/** An installed module, with only the fields the rule under test reads. */
function module_(pack: string, room_id: string): InstalledModule {
  return { pack, room_id, house: room_id === "" } as InstalledModule;
}

test("the room a module was placed in is the room the export opens on", () => {
  const modules = [module_("bedtime", ""), module_("lights", "kitchen")];
  assert.equal(placementRoom(modules, "lights"), "kitchen");
});

test("a module put in the house answers the house, not nothing", () => {
  // `""` is the house's own spelling and a real placement. Folding it into
  // `undefined` would make `followPlacement` leave the export's room wherever
  // it was, which for a house module would be a room it is not in.
  const modules = [module_("bedtime", "")];
  assert.equal(placementRoom(modules, "bedtime"), "");
});

test("a module nobody placed is unknown, and moves nothing", () => {
  assert.equal(placementRoom([module_("bedtime", "")], "ghost"), undefined);
});

/** A value row, with only the fields the rule under test reads. */
function value_(
  kind: string,
  unit: string | null = null,
  rest: Partial<DevValueRow> = {},
): DevValueRow {
  return { key: `input:${kind}_row`, label: kind, kind, unit, ...rest } as DevValueRow;
}

test("only a value that can be a length of time is offered as something to wait for", () => {
  // The blueprint this journey is walked with has a boolean and two clock times
  // among its values. Waiting on one produced `{type: duration, default: true}`,
  // which the manifest validator refuses -- so the offer is the half of the
  // rule the server's own check enforces from its side.
  const values = [
    value_("integer"),
    value_("number"),
    value_("duration"),
    value_("boolean"),
    value_("time"),
    value_("string"),
    value_("enum"),
  ];
  assert.deepEqual(
    waitableValues(values).map(({ value }) => value.kind),
    ["integer", "number", "duration"],
  );
});

test("a value measured in percent or kelvin is not offered, however numeric", () => {
  // The same blueprint bounds its brightness in `%` and its colour temperature
  // in `K`, and both are `number` rows. A wait is a whole number of seconds, so
  // offering "Max Brightness (%)" would write a wait of a hundred seconds.
  const values = [
    value_("integer", "%"),
    value_("number", "K"),
    value_("integer", "minutes"),
    value_("integer", "seconds"),
    value_("integer", "min"),
    value_("number", "s"),
    value_("duration", "seconds"),
  ];
  assert.deepEqual(
    waitableValues(values).map(({ value }) => value.label),
    ["integer", "integer", "integer", "number", "duration"],
  );
});

test("a value measured in minutes is written in seconds, bounds and all", () => {
  const row = value_("integer", "minutes", {
    label: "Trigger Interval (minutes)",
    default: 5,
    minimum: 1,
    maximum: 60,
  });
  const waited = waitedRow(row);
  assert.equal(waited.unit, "seconds");
  assert.equal(waited.default, 300);
  assert.equal(waited.minimum, 60);
  assert.equal(waited.maximum, 3600);
  // The title named the unit it used to be in, and would otherwise read
  // "Trigger Interval (minutes)" over a control counting seconds.
  assert.equal(waited.label, "Trigger Interval");
});

test("a value already counted in seconds is left exactly as the source wrote it", () => {
  const row = value_("integer", "seconds", {
    label: "Transition Time (seconds)",
    default: 30,
    minimum: 0,
    maximum: 600,
  });
  assert.equal(waitedRow(row), row);
});

test("a unit the tab does not know is not a length of time", () => {
  assert.equal(secondsPerUnit("minutes"), 60);
  assert.equal(secondsPerUnit("SECONDS"), 1);
  assert.equal(secondsPerUnit(null), 1);
  assert.equal(secondsPerUnit(""), 1);
  assert.equal(secondsPerUnit("%"), null);
  assert.equal(secondsPerUnit("K"), null);
});

test("a waitable row is offered with the index the plan keys it by", () => {
  // `settingKey(row, index)` is the manifest's option key, so a renumbered list
  // would offer a key the plan never writes.
  const values = [value_("boolean"), value_("integer"), value_("time")];
  assert.deepEqual(
    waitableValues(values).map(({ index }) => index),
    [1],
  );
});
