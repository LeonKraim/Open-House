/**
 * Unit tests for which cast a row is answered with, asked two ways.
 *
 * The two questions look like one and are not, and the difference is the whole
 * reason there are two functions: `castModeForSetting` answers *which editor to
 * open*, and `castHeldBy` answers *what the module holds*. They part company on
 * exactly one row -- a template -- and that row is the one the detach button is
 * offered on, so a single function would either show a second template box over
 * the first or refuse to detach the one cast a person can write by hand.
 */

import { strict as assert } from "node:assert";
import { test } from "node:test";

import type { ModuleInputRow } from "../api/models.ts";
import { castHeldBy, castModeForSetting } from "./casts.ts";

/** A module's setting row, with only the fields these two rules read. */
function row_(rest: Partial<ModuleInputRow> = {}): ModuleInputRow {
  return {
    name: "lux_sensor",
    title: "Lux sensor",
    description: "",
    default: null,
    has_default: false,
    multiple: false,
    bound: false,
    value: null,
    satisfied: false,
    in_trigger: false,
    selector: "entity",
    options: [],
    ...rest,
  };
}

test("a row holding a plain value holds no cast", () => {
  assert.equal(castHeldBy(row_({ value: "sensor.lux" })), "none");
  assert.equal(castHeldBy(row_()), "none");
  assert.equal(castHeldBy(row_({ value: 42, selector: "number" })), "none");
});

test("a record's condition, flow and automation are all casts it holds", () => {
  // Read off the record's own fields rather than off the binding: a condition, a
  // flow and an automation are each answered by an *entity* the person never
  // chose -- one Open House makes, one a flow writes, one a helper their own
  // automation writes -- so none of the three is visible in the row's value.
  assert.equal(castHeldBy(row_({ cast: { condition: "state" } })), "condition");
  assert.equal(castHeldBy(row_({ flow_id: "abc123" })), "nodered");
  assert.equal(castHeldBy(row_({ automation_id: "open_house_lux_hall" })), "automation");
});

test("a template row is a cast the module holds and not an editor to open", () => {
  // **The one row the two questions answer differently.** The template *is* the
  // row's answer, so the row is already showing its own box -- which is why the
  // menu opens on "Input field" here and would be a second box if it did not.
  // The record holds a cast all the same, and that is what the detach button
  // needs to know.
  const template = row_({ value: "{{ states('sensor.lux') | int }}" });
  assert.equal(castModeForSetting(template), "none");
  assert.equal(castHeldBy(template), "template");
});

test("a template inside a longer string is still a template", () => {
  // `isTemplate` asks about the substring rather than the whole value, because
  // Home Assistant renders a template wherever it appears in an expression --
  // `"{{ a }} and {{ b }}"` is one template and not two, and a row holding one
  // is a row whose answer is logic.
  assert.equal(castHeldBy(row_({ value: "lux is {{ states('sensor.lux') }}" })), "template");
});

test("the menu's own choice wins over the record, which is what it is for", () => {
  // A person who has flipped the menu is looking at the editor they picked, and
  // the row it was flipped on is still holding whatever it held until the save.
  assert.equal(castModeForSetting(row_({ value: "sensor.lux" }), "template"), "template");
  assert.equal(castModeForSetting(row_({ cast: {} }), "none"), "none");
});
