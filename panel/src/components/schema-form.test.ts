/**
 * Unit tests for the schema-driven form's classification rules.
 *
 * These are the decisions a pack's schema turns on, so each case below is a
 * shape a pack could actually declare: an enum becomes a select, an enum inside
 * an array becomes a checkbox group, `format: "time"` becomes a time input, and
 * a shape outside the supported subset is classified `unsupported` rather than
 * guessed at. The last one is the important one -- a form that quietly renders
 * nothing for a field a pack believes it configured is the failure this whole
 * approach exists to avoid.
 */

import { strict as assert } from "node:assert";
import { test } from "node:test";

import {
  defaultValue,
  fieldKind,
  fieldLabel,
  fieldOrder,
  humanizeKey,
  isMissing,
  type JsonSchema,
} from "./schema-spec.ts";

test("humanizeKey turns snake, camel and kebab case into a sentence case label", () => {
  assert.equal(humanizeKey("some_key"), "Some key");
  assert.equal(humanizeKey("someKey"), "Some key");
  assert.equal(humanizeKey("some-key"), "Some key");
  assert.equal(humanizeKey("Some Key"), "Some key");
});

test("a title wins as the label, the key otherwise", () => {
  assert.equal(fieldLabel("dim_level", { title: "Brightness" }), "Brightness");
  assert.equal(fieldLabel("dim_level", {}), "Dim level");
});

test("enum, boolean, number and array map to their controls", () => {
  assert.equal(fieldKind({ enum: ["a", "b"] }), "enum");
  assert.equal(fieldKind({ type: "boolean" }), "boolean");
  assert.equal(fieldKind({ type: "integer" }), "number");
  assert.equal(fieldKind({ type: "string" }), "text");
  assert.equal(fieldKind({ type: "string", format: "time" }), "time");
  assert.equal(fieldKind({ type: "string", format: "multiline" }), "multiline");
  assert.equal(fieldKind({ type: "array", items: { type: "string" } }), "list");
  assert.equal(
    fieldKind({ type: "array", items: { enum: ["x", "y"] } }),
    "multi-enum",
  );
  assert.equal(
    fieldKind({ type: "object", properties: { a: { type: "string" } } }),
    "object",
  );
});

test("a shape outside the subset is unsupported, not silently dropped", () => {
  assert.equal(
    fieldKind({ type: "array", items: { type: "object" } }),
    "unsupported",
  );
  assert.equal(fieldKind({}), "unsupported");
});

test("required fields render first, in the order the schema lists them", () => {
  const schema: JsonSchema = {
    type: "object",
    properties: { zulu: {}, alpha: {}, middle: {} },
    required: ["zulu", "alpha"],
  };
  assert.deepEqual(fieldOrder(schema), ["zulu", "alpha", "middle"]);
});

test("a required key the schema does not declare is ignored", () => {
  const schema: JsonSchema = {
    type: "object",
    properties: { a: {} },
    required: ["a", "ghost"],
  };
  assert.deepEqual(fieldOrder(schema), ["a"]);
});

test("defaults come from the schema, then from the kind", () => {
  assert.equal(defaultValue({ default: 5 }), 5);
  assert.equal(defaultValue({ type: "boolean" }), false);
  assert.equal(defaultValue({ type: "number" }), 0);
  assert.deepEqual(defaultValue({ type: "array", items: { type: "string" } }), []);
  assert.equal(defaultValue({ type: "string" }), "");
});

test("an object default collects only the children that declare one", () => {
  const schema: JsonSchema = {
    type: "object",
    properties: { a: { default: 1 }, b: { type: "string" } },
  };
  assert.deepEqual(defaultValue(schema), { a: 1 });
});

test("missing means empty for the field's own kind", () => {
  assert.equal(isMissing({ type: "string" }, ""), true);
  assert.equal(isMissing({ type: "string" }, "x"), false);
  assert.equal(isMissing({ type: "string" }, undefined), true);
  assert.equal(isMissing({ type: "array", items: { type: "string" } }, []), true);
  assert.equal(
    isMissing({ type: "array", items: { type: "string" } }, ["x"]),
    false,
  );
  // A boolean is never missing: `false` is a value a user chose.
  assert.equal(isMissing({ type: "boolean" }, false), false);
  assert.equal(isMissing({ type: "number" }, 0), false);
});
