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
  schemaForKeys,
  withoutReachRoles,
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

test("a schema cut to keys keeps only those keys, in the order asked", () => {
  const schema: JsonSchema = {
    type: "object",
    title: "Options",
    properties: { a: { type: "number" }, b: { type: "boolean" }, c: {} },
    required: ["b", "c"],
  };
  const cut = schemaForKeys(schema, ["c", "a"]);
  assert.deepEqual(Object.keys(cut?.properties ?? {}), ["c", "a"]);
  // `b` is required but was not asked for, so it is not carried as required.
  assert.deepEqual(cut?.required, ["c"]);
  assert.equal(cut?.title, "Options");
});

test("a key the schema does not declare is dropped, not invented", () => {
  const schema: JsonSchema = {
    type: "object",
    properties: { a: { type: "number" } },
  };
  assert.equal(schemaForKeys(schema, ["ghost"]), null);
  assert.equal(schemaForKeys(null, ["a"]), null);
  assert.equal(schemaForKeys(schema, []), null);
});

test("a module's derived reach-role switches are not drawn", () => {
  const schema: JsonSchema = {
    type: "object",
    properties: {
      "module.bedtime.reach.light_group": { type: "boolean" },
      "module.bedtime.grace": { type: "number" },
      // Not a role: the name merely starts with the word.
      "module.bedtime.reach_boost": { type: "number" },
    },
    required: ["module.bedtime.reach.light_group", "module.bedtime.grace"],
  };
  const drawn = withoutReachRoles(schema);
  assert.deepEqual(Object.keys(drawn?.properties ?? {}), [
    "module.bedtime.grace",
    "module.bedtime.reach_boost",
  ]);
  // A hidden key cannot stay required, or the form would demand a field it
  // never drew.
  assert.deepEqual(drawn?.required, ["module.bedtime.grace"]);
});

test("a module whose only settings were its reach roles draws no form", () => {
  const schema: JsonSchema = {
    type: "object",
    properties: { "module.bedtime.reach.lock": { type: "boolean" } },
  };
  assert.equal(withoutReachRoles(schema), null);
});

test("a schema with no reach-role keys is returned unchanged", () => {
  const schema: JsonSchema = {
    type: "object",
    properties: { "module.fan.boost": { type: "boolean" } },
  };
  assert.equal(withoutReachRoles(schema), schema);
  assert.equal(withoutReachRoles(null), null);
});
