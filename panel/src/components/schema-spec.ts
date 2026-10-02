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

/**
 * The same schema cut down to `keys`, so one module's settings can be drawn on
 * their own.
 *
 * The page is handed one flat schema of every module's settings; a card for a
 * single module needs the slice belonging to it and nothing else. Keys the
 * schema does not declare are dropped rather than invented, because a field for
 * a setting this page cannot set is a control that writes nothing. `null` when
 * the slice is empty, which is the same "nothing to configure here" the whole
 * schema answers with -- so a module with no settings simply has no form.
 */
export function schemaForKeys(
  schema: JsonSchema | null,
  keys: readonly string[],
): JsonSchema | null {
  const declared = schema?.properties ?? {};
  const properties: Record<string, JsonSchema> = {};
  for (const key of keys) {
    const node = declared[key];
    if (node !== undefined) properties[key] = node;
  }
  if (Object.keys(properties).length === 0) return null;
  return {
    type: "object",
    title: schema?.title ?? undefined,
    properties,
    required: (schema?.required ?? []).filter((key) => key in properties),
  };
}

/**
 * The key shape of a module's derived reach-role switch: `module.<pack>.reach.<slot>`.
 *
 * Anchored at the end because that is where the slot name is: `reach.light_group`
 * and not `reach.light_group.boost`, so a pack's own option that merely *starts*
 * with the word is not caught by it.
 */
const REACH_ROLE_KEY = /\.reach\.[^.]+$/;

/**
 * A module's schema with its derived reach-role switches taken out.
 *
 * `reach.<slot>` options are not a pack's settings: the adapter derives one per
 * role a pack's behaviours write through (`live_profiles.reach_properties`), so
 * a module that dims lights and drops thermostats arrives with a "light group"
 * switch and a "climate zone" switch whether or not its author ever thought
 * about them. They answer the same question the reach control beside each
 * behaviour answers -- what does this act on -- in the engine's vocabulary
 * rather than a person's, so a card that drew both asked it twice, once in a
 * language the person had to translate.
 *
 * Dropped from the *drawn* schema only. The key stays in the page's
 * `option_keys`, so it is still claimed by its module and cannot fall through to
 * the page's leftover bucket -- the "Other settings" card this rule exists to
 * keep empty. A pack that declares the option itself is dropped with the derived
 * one, which is right: it is the same switch under the same key.
 *
 * `null` when nothing is left, which is the same "this module has no settings"
 * `schemaForKeys` answers with -- a card draws no form rather than a heading
 * over an empty one.
 */
export function withoutReachRoles(schema: JsonSchema | null): JsonSchema | null {
  const declared = schema?.properties;
  if (schema == null || declared == null) return schema;
  const properties: Record<string, JsonSchema> = {};
  for (const [key, node] of Object.entries(declared)) {
    if (!REACH_ROLE_KEY.test(key)) properties[key] = node;
  }
  if (Object.keys(properties).length === Object.keys(declared).length) return schema;
  // Nothing left but the reach switches: the module has no settings of its own,
  // which is the `null` a card draws as no form at all rather than a heading
  // over an empty one.
  if (Object.keys(properties).length === 0) return null;
  return {
    ...schema,
    properties,
    required: (schema.required ?? []).filter((key) => key in properties),
  };
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
