#!/usr/bin/env node
// Generate TypeScript types for the panel from the frozen JSON Schemas.
//
// `schemas/` is the single source of truth for the engine, the panel, the pack
// CI and the AI tools (spec.txt, "Tech stack"). The panel is the one consumer
// that cannot import a Python model, so its view of every frozen document is
// generated here rather than hand-copied -- a hand-copied interface is a second
// source of truth, and the two drift the first time a schema version moves.
//
// What is generated, and what is deliberately not:
//
//   * One interface (or type alias) per concept, from the *current* version of
//     each concept -- "current" meaning the version no other version names in
//     `supersedes`. `pack-manifest/1.0.0` and `1.1.0` are present and are not
//     the ones emitted; `1.2.0` is.
//   * One interface per `$defs` entry, prefixed with the concept name, so a
//     `$defs.binding` in `house` is `HouseBinding` rather than a bare `Binding`
//     that would collide with another concept's.
//   * A `SCHEMA_VERSIONS` map naming the exact version each type came from, so
//     the panel can assert the schema version it was built against.
//
//   * Not generated: validation. These are wire types for a document the
//     server has already validated; the panel does not re-run JSON Schema.
//   * Not generated: the conditional requirements a schema states as
//     `allOf`/`if`/`then` (for example "a `module` pack must carry
//     `requires_slots`"). Those are clauses of validation, and collapsing them
//     into a TypeScript union would be inventing a discriminated shape the
//     schema does not actually declare. A field such a clause requires is
//     emitted optional, with the reason recorded in the file header.
//   * Not generated: `pattern`, `minLength`, `minItems` and the other value
//     constraints. TypeScript cannot express them and encoding them as branded
//     types would make every literal a cast.
//
// Usage:
//   node scripts/generate-types.mjs            # write src/types/generated.ts
//   node scripts/generate-types.mjs --stdout   # print, do not write (used by
//                                              # the drift test)
//   node scripts/generate-types.mjs --check    # exit 1 if the committed file
//                                              # is stale

import { readdirSync, readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const PANEL = resolve(HERE, "..");
const ROOT = resolve(PANEL, "..");
const SCHEMAS = join(ROOT, "schemas");
const OUT = join(PANEL, "src", "types", "generated.ts");

const args = process.argv.slice(2);
const toStdout = args.includes("--stdout");
const check = args.includes("--check");

// --------------------------------------------------------------------------
// File discovery
// --------------------------------------------------------------------------

/** Every JSON Schema file under `schemas/`, sorted, as absolute paths. */
function schemaFiles() {
  const found = [];
  const walk = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (entry.name.endsWith(".json")) found.push(full);
    }
  };
  walk(SCHEMAS);
  return found.sort();
}

const VERSION_RE = /^\d+\.\d+\.\d+$/;

/**
 * The concept key for a file: its path under `schemas/`, minus extension and
 * minus a trailing version segment. A concept is `house`, not `house/1.0.0`,
 * because the version is already `SCHEMA_VERSIONS`'s value and repeating it in
 * the key would make the key change every release.
 */
function conceptKey(absPath) {
  const rel = relative(SCHEMAS, absPath)
    .split(sep)
    .join("/")
    .replace(/\.json$/, "");
  const segs = rel.split("/");
  if (segs.length > 1 && VERSION_RE.test(segs[segs.length - 1])) segs.pop();
  return segs.join("/");
}

/**
 * The directory a version file lives in, or `null` for a catalog-style file.
 *
 * A version file is `<concept>/<semver>.json`; its concept directory is the
 * parent. Everything else (the `catalog/` files, which are single documents)
 * is keyed by its own filename.
 */
function conceptDir(absPath) {
  const base = absPath.slice(absPath.lastIndexOf(sep) + 1);
  if (!VERSION_RE.test(base.replace(/\.json$/, ""))) return null;
  return dirname(absPath);
}

/** Pick the current version of a concept: the one nobody supersedes. */
function currentVersion(files) {
  const superseded = new Set();
  const parsed = files.map((f) => ({
    file: f,
    doc: JSON.parse(readFileSync(f, "utf8")),
  }));
  for (const { doc } of parsed) {
    if (typeof doc.supersedes === "string") superseded.add(doc.supersedes);
  }
  // Prefer the highest version that is not named as superseded. A concept with
  // a single version has no `supersedes` chain at all, so every file is a
  // candidate there and the sort picks the only one.
  const candidates = parsed.filter(
    ({ doc }) => !superseded.has(doc.schema_version),
  );
  const pool = candidates.length > 0 ? candidates : parsed;
  pool.sort((a, b) =>
    String(a.doc.schema_version).localeCompare(String(b.doc.schema_version), {
      numeric: true,
    }),
  );
  return pool[pool.length - 1];
}

// --------------------------------------------------------------------------
// Naming
// --------------------------------------------------------------------------

const pascal = (value) =>
  String(value)
    .split(/[^A-Za-z0-9]+/)
    .filter(Boolean)
    .map((part) => part[0].toUpperCase() + part.slice(1))
    .join("");

/** A rough singular used only to name an array's element type. */
function singular(word) {
  if (/ies$/.test(word)) return word.replace(/ies$/, "y");
  if (/(ses|xes|zes|ches|shes)$/.test(word)) return word.replace(/es$/, "");
  if (/s$/.test(word) && !/ss$/.test(word)) return word.replace(/s$/, "");
  return word;
}

/** The emitted type name for a concept file. */
function conceptName(key, doc) {
  if (key.startsWith("catalog/")) return "Catalog" + pascal(key.slice(8));
  if (typeof doc.title === "string" && doc.title.length > 0) {
    return pascal(doc.title);
  }
  return pascal(key.split("/").pop());
}

// --------------------------------------------------------------------------
// Build the index of every file, and of the types we will emit
// --------------------------------------------------------------------------

const files = schemaFiles();
/** abs path -> { key, name, doc, file } for every file we know about. */
const index = new Map();
/** The files whose current version we will emit types for. */
const current = [];

for (const file of files) {
  const doc = JSON.parse(readFileSync(file, "utf8"));
  const key = conceptKey(file);
  index.set(resolve(file), {
    key,
    name: conceptName(key, doc),
    doc,
    file: resolve(file),
  });
}

// Group concept directories and pick the current version of each.
const byDir = new Map();
for (const file of files) {
  const dir = conceptDir(file);
  if (dir === null) {
    current.push(resolve(file));
    continue;
  }
  if (!byDir.has(dir)) byDir.set(dir, []);
  byDir.get(dir).push(file);
}
for (const [, group] of [...byDir.entries()].sort()) {
  const chosen = currentVersion(group);
  current.push(resolve(chosen.file));
}
current.sort();

// --------------------------------------------------------------------------
// Schema -> TypeScript
// --------------------------------------------------------------------------

const UNSUPPORTED_NOTE =
  "Value constraints (`pattern`, `minLength`, `minItems`, `minimum`) and the " +
  "conditional clauses a schema states as `allOf`/`if`/`then` are not encoded: " +
  "TypeScript cannot express them, and a field such a clause conditionalises is " +
  "emitted optional rather than guessed at.";

function literal(value) {
  if (value === null) return "null";
  if (typeof value === "string") return JSON.stringify(value);
  if (typeof value === "number" || typeof value === "boolean")
    return String(value);
  return "unknown";
}

/** Render a string-literal union, or `null` when the node carries no enum. */
function enumType(node) {
  if (Array.isArray(node.enum)) {
    return node.enum.map(literal).join(" | ") || "never";
  }
  if (Object.prototype.hasOwnProperty.call(node, "const")) {
    return literal(node.const);
  }
  return null;
}

function normalizeRefPath(fromFile, filePart) {
  if (!filePart) return resolve(fromFile);
  if (/^https?:\/\//.test(filePart)) {
    // The `$id`s are absolute URLs under a single host; map one back to a file
    // by its trailing `schemas/...` path.
    const marker = "/schemas/";
    const at = filePart.indexOf(marker);
    if (at === -1) return null;
    const tail = filePart.slice(at + marker.length).split("#")[0];
    return resolve(SCHEMAS, tail);
  }
  return resolve(dirname(fromFile), filePart);
}

function refName(ref, fromFile) {
  const [filePart, pointer = ""] = ref.split("#");
  const target = normalizeRefPath(fromFile, filePart);
  if (target === null) return "unknown";
  const entry = index.get(resolve(target));
  if (!entry) return "unknown";
  const segs = pointer.split("/").filter(Boolean);
  if (segs.length === 0) return entry.name;
  if (segs[0] === "$defs" && segs.length === 2) {
    return entry.name + pascal(segs[1]);
  }
  if (segs[0] === "properties" && segs.length === 3 && segs[2] === "items") {
    // An element type referenced across files, e.g. the behaviour vocabulary's
    // trigger list. The alias is emitted where the property is declared.
    return entry.name + pascal(singular(segs[1]));
  }
  return pointerType(entry, segs);
}

/** Follow a JSON pointer into a document and render the node it lands on. */
function pointerType(entry, segs) {
  let node = entry.doc;
  for (const seg of segs) {
    if (node === null || typeof node !== "object") return "unknown";
    node = node[seg];
  }
  if (node === undefined || node === null || typeof node !== "object") {
    return "unknown";
  }
  return tsType(node, entry.name, entry.file);
}

function objectBody(node, ctxName, ctxFile, indent) {
  const props = node.properties ?? {};
  const required = new Set(Array.isArray(node.required) ? node.required : []);
  const pad = " ".repeat(indent);
  const lines = [];
  for (const key of Object.keys(props).sort()) {
    const optional = required.has(key) ? "" : "?";
    const doc = docComment(props[key], indent);
    if (doc) lines.push(doc);
    const safe = /^[A-Za-z_$][A-Za-z0-9_$]*$/.test(key)
      ? key
      : JSON.stringify(key);
    lines.push(
      `${pad}${safe}${optional}: ${tsType(props[key], ctxName, ctxFile)};`,
    );
  }
  const extra = node.additionalProperties;
  if (extra && typeof extra === "object") {
    lines.push(
      `${pad}[key: string]: ${tsType(extra, ctxName, ctxFile)};`,
    );
  } else if (extra === true && lines.length === 0) {
    lines.push(`${pad}[key: string]: unknown;`);
  }
  return lines;
}

/** A single `/** ... *\/` comment for a property, from its `description`. */
function docComment(node, indent) {
  if (!node || typeof node !== "object") return null;
  const text = node.description;
  if (typeof text !== "string") return null;
  const pad = " ".repeat(indent);
  const oneLine = text.replace(/\s+/g, " ").trim();
  if (oneLine.length <= 96) return `${pad}/** ${oneLine} */`;
  const words = oneLine.split(" ");
  const out = [`${pad}/**`];
  let line = pad + " *";
  for (const word of words) {
    if ((line + " " + word).length > 96) {
      out.push(line);
      line = pad + " *";
    }
    line += " " + word;
  }
  out.push(line, `${pad} */`);
  return out.join("\n");
}

function tsType(node, ctxName, ctxFile) {
  if (!node || typeof node !== "object") return "unknown";
  if (typeof node.$ref === "string") return refName(node.$ref, ctxFile);
  const lit = enumType(node);
  if (lit !== null) return lit;

  const type = Array.isArray(node.type) ? node.type : [node.type];
  const has = (t) => type.includes(t);

  if (has("array")) {
    return `${tsType(node.items ?? {}, ctxName, ctxFile)}[]`;
  }
  if (has("boolean")) return "boolean";
  if (has("integer") || has("number")) return "number";
  if (has("null")) return "null";
  if (has("string")) return "string";

  if (node.properties || node.additionalProperties) {
    const body = objectBody(node, ctxName, ctxFile, 0);
    return body.length === 0 ? "Record<string, never>" : `{ ${body.join(" ")} }`;
  }
  return "unknown";
}

// --------------------------------------------------------------------------
// Emission
// --------------------------------------------------------------------------

function emitFile(absPath) {
  const entry = index.get(resolve(absPath));
  const { name, doc, key } = entry;
  const out = [];

  const type = doc.type;
  const hasShape = Boolean(doc.properties || doc.$defs || doc.additionalProperties);
  if (!hasShape && type === undefined) {
    out.push(
      `/** \`${key}\` declares no shape of its own; the version alone is the artifact. */`,
      `export type ${name} = Record<string, never>;`,
    );
  } else if (doc.properties) {
    out.push(`export interface ${name} {`);
    out.push(...objectBody(doc, name, entry.file, 2));
    out.push(`}`);
  } else {
    out.push(`export type ${name} = ${tsType(doc, name, entry.file)};`);
  }

  // Array element aliases, so a `#/properties/x/items` reference from another
  // file has a stable name to point at.
  for (const prop of Object.keys(doc.properties ?? {}).sort()) {
    const schema = doc.properties[prop];
    if (
      schema &&
      typeof schema === "object" &&
      schema.type === "array" &&
      schema.items &&
      typeof schema.items === "object" &&
      !schema.items.$ref
    ) {
      const alias = name + pascal(singular(prop));
      if (alias === name) continue;
      out.push(
        ``,
        `export type ${alias} = ${tsType(schema.items, name, entry.file)};`,
      );
    }
  }

  for (const def of Object.keys(doc.$defs ?? {}).sort()) {
    const node = doc.$defs[def];
    const defName = name + pascal(def);
    out.push(``);
    if (node && typeof node === "object" && node.properties) {
      out.push(`export interface ${defName} {`);
      out.push(...objectBody(node, name, entry.file, 2));
      out.push(`}`);
    } else {
      out.push(`export type ${defName} = ${tsType(node, name, entry.file)};`);
    }
  }

  return { key, version: doc.schema_version ?? "0.0.0", out: out.join("\n") };
}

const sections = current.map(emitFile);
const versions = {};
for (const section of sections) versions[section.key] = section.version;

const header = [
  "// Generated by panel/scripts/generate-types.mjs -- do not edit by hand.",
  "//",
  "// Source of truth: the frozen JSON Schemas under `schemas/`. Regenerate with",
  "//   npm run gen:types",
  "// and the drift check in panel/tests/test_types_generation.py will fail if",
  "// this file and the schemas disagree.",
  "//",
  "// " + UNSUPPORTED_NOTE,
  "",
  "/** The exact schema version each emitted type was generated from. */",
  "export const SCHEMA_VERSIONS = {",
  ...Object.keys(versions)
    .sort()
    .map((key) => `  ${JSON.stringify(key)}: ${JSON.stringify(versions[key])},`),
  "} as const;",
  "",
  "/** The concept keys `SCHEMA_VERSIONS` names. */",
  "export type SchemaConcept = keyof typeof SCHEMA_VERSIONS;",
  "",
];

const banner = (key, version) => [
  "",
  `// ${"-".repeat(70)}`,
  `// ${key} @ ${version}`,
  `// ${"-".repeat(70)}`,
  "",
];

const body = sections
  .map((section) => banner(section.key, section.version).join("\n") + section.out)
  .join("\n");

const document = header.join("\n") + body + "\n";

if (toStdout || check) {
  let existing = "";
  try {
    existing = readFileSync(OUT, "utf8");
  } catch {
    existing = "";
  }
  if (check) {
    if (existing !== document) {
      process.stderr.write(
        "generated types are stale: run `npm run gen:types` in panel/\n",
      );
      process.exit(1);
    }
    process.exit(0);
  }
  process.stdout.write(document);
} else {
  mkdirSync(dirname(OUT), { recursive: true });
  writeFileSync(OUT, document, "utf8");
  process.stdout.write(
    `wrote ${relative(ROOT, OUT).split(sep).join("/")} (${sections.length} concepts)\n`,
  );
}
