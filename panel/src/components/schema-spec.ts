/**
 * The pure half of the schema-driven form.
 *
 * The decisions about *what* a JSON Schema node means -- which control it maps
 * to, what order fields render in, what a required-but-empty field looks like --
 * are separated from the element that draws them. They are the part worth
 * testing, and they test without a DOM: `node --test` runs this file directly,
 * while the element it feeds needs a browser to instantiate.
 *
 * The supported subset is stated in `schema-form.ts`; this file is the authority
 * on how a node is classified.
 */

/** The subset of JSON Schema the panel reads. */
export interface JsonSchema {
  type?: string | string[];
  title?: string;
  description?: string;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  items?: JsonSchema;
  enum?: unknown[];
  default?: unknown;
  format?: string;
  minimum?: number;
  maximum?: number;
  additionalProperties?: boolean | JsonSchema;
}

export type OptionsValues = Record<string, unknown>;

/** The kind of control a schema node maps to. */
export type FieldKind =
  | "object"
  | "enum"
  | "multi-enum"
  | "boolean"
  | "number"
  | "text"
  | "multiline"
  | "time"
  | "color"
  | "list"
  | "unsupported";

function nodeTypes(schema: JsonSchema): string[] {
  if (Array.isArray(schema.type)) return schema.type;
  return schema.type === undefined ? [] : [schema.type];
}

/** Which control a schema node is rendered as. */
export function fieldKind(schema: JsonSchema): FieldKind {
  const types = nodeTypes(schema);
  if (types.includes("object") || schema.properties) return "object";
  if (Array.isArray(schema.enum)) {
    return types.includes("array") ? "multi-enum" : "enum";
  }
  if (types.includes("array")) {
    const item = schema.items;
    if (item && Array.isArray(item.enum)) return "multi-enum";
    if (
      item &&
      nodeTypes(item).every(
        (t) =>
          t === "string" ||
          t === "number" ||
          t === "integer" ||
          t === "boolean",
      )
    ) {
      return "list";
    }
    return "unsupported";
  }
  if (types.includes("boolean")) return "boolean";
  if (types.includes("integer") || types.includes("number")) return "number";
  if (types.includes("string")) {
    switch (schema.format) {
      case "multiline":
      case "text":
        return "multiline";
      case "time":
        return "time";
      case "color":
      case "colour":
        return "color";
      default:
        return "text";
    }
  }
  return "unsupported";
}

/** `some_key` / `someKey` / `Some Key` -> `Some key`. */
export function humanizeKey(key: string): string {
  const spaced = key
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[_-]+/g, " ")
    .trim();
  if (spaced.length === 0) return key;
  return spaced[0]!.toUpperCase() + spaced.slice(1).toLowerCase();
}

/** The label a field renders with: the schema's `title`, else the key. */
export function fieldLabel(key: string, schema: JsonSchema): string {
  return schema.title && schema.title.length > 0
    ? schema.title
    : humanizeKey(key);
}

/** The order fields render in: `required` order first, then the rest. */
export function fieldOrder(schema: JsonSchema): string[] {
  const declared = Object.keys(schema.properties ?? {});
  const required = (schema.required ?? []).filter((key) =>
    declared.includes(key),
  );
  const rest = declared.filter((key) => !required.includes(key)).sort();
  return [...required, ...rest];
}

/** A value for a field that has none yet, from the schema's own `default`. */
export function defaultValue(schema: JsonSchema): unknown {
  if (schema.default !== undefined) return schema.default;
  switch (fieldKind(schema)) {
    case "boolean":
      return false;
    case "number":
      return 0;
    case "multi-enum":
    case "list":
      return [];
    case "object": {
      const object: OptionsValues = {};
      for (const key of Object.keys(schema.properties ?? {})) {
        const child = schema.properties?.[key];
        if (child && child.default !== undefined) object[key] = child.default;
      }
      return object;
    }
    default:
      return "";
  }
}

/** Whether a required field is currently empty. */
export function isMissing(schema: JsonSchema, value: unknown): boolean {
  const kind = fieldKind(schema);
  if (value === undefined || value === null) return true;
  if (kind === "list" || kind === "multi-enum") {
    return Array.isArray(value) && value.length === 0;
  }
  if (
    kind === "text" ||
    kind === "multiline" ||
    kind === "time" ||
    kind === "color"
  ) {
    return typeof value === "string" && value.trim() === "";
  }
  return false;
}
