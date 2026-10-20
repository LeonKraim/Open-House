/**
 * An import nobody has finished yet, kept where a reload cannot reach it.
 *
 * **The thing this is for is the worst half-hour in the product.** Importing a
 * blueprint is a reading before it is a host: a person picks a source, answers
 * every input it asks for, casts some of them, names the outputs and fills in
 * four fields about the module -- and every one of those is a judgement about
 * their house that only they can make. Then Home Assistant restarts, or the
 * browser is closed by accident, or the machine goes down, and the page comes
 * back empty. Nothing was saved, because nothing was *finished*, and the person
 * pays for the whole screen twice.
 *
 * So the answers are written as they are made, and the Dev tab offers them back
 * as "left open to be finished".
 *
 * **A draft is not a module, and nothing in the house reads it.** A module is a
 * definition the engine builds automations from; a draft is the screen's own
 * answers to a source, held so that the screen can be put back the way it was.
 * That is why this lives in the browser and not in the house's store: the house
 * would carry state it must ignore, every change to the screen's shape would
 * become a store migration, and the one thing a draft has to survive -- the page
 * going away -- a browser does perfectly well. The seam is `read`/`write`/
 * `forget` over a `Storage`, so moving it to the server later is a change to
 * those three functions and not to the screen.
 *
 * **What a draft is keyed by is what it is about.** Two drafts are two different
 * sources being answered at once -- the same automation in two ways, or a
 * blueprint beside a pasted one -- so the id is the source's: `automation:<id>`,
 * `blueprint:<path>`, and for a pasted document `text:<fingerprint>`, because a
 * pasted document has no name of its own and two of them are two drafts. Nothing
 * else identifies a draft: a title is a thing a person is still typing, and the
 * moment it changes the draft would become a second one.
 *
 * **Reading is tolerant; writing is exact.** A row this module cannot make sense
 * of is dropped rather than thrown at the screen -- the same rule
 * `engine/custom_slots.record_from` follows, and for the same reason: a store
 * written by an earlier version, or by hand, or by a browser that died between
 * two writes, must not be a person locked out of their own panel. A *version*
 * mismatch drops the lot, because the shape below is the screen's own and a
 * screen that read an older shape would fill its fields with the wrong things.
 */

import type { ModuleBinding } from "./models.ts";

/** Where the drafts live. One key for all of them, so a read is one parse. */
export const DRAFT_KEY = "open-house.import-drafts";

/**
 * The shape below, as a number.
 *
 * Bumped when a field changes meaning, and not when one is added: an added field
 * reads as absent and the screen falls back, where a changed one reads as
 * present and wrong. A mismatch drops every draft rather than migrating them --
 * what is lost is an unfinished form, and what would be risked by migrating is a
 * form filled in from another version's answers.
 */
export const DRAFT_VERSION = 1;

/** The three ways a source arrives, as `host-module.ts` spells them. */
export type DraftKind = "automation" | "blueprint" | "text";

/**
 * What the screen sends to `modules/define`, and nothing else.
 *
 * **The same object, not a copy of it.** This is exactly the `definition` the
 * save builds, field for field, because the thing being preserved is the save
 * that has not happened: a draft that held a different shape would be a second
 * description of the same answers, and the two would part company the first time
 * either changed. `replace` is here because it is asked on the import screen and
 * is a decision the person made -- re-importing over a module of the same name
 * is deliberate, and being asked again is being asked something already answered.
 */
export interface DraftAnswers {
  title: string;
  description: string;
  author: string;
  version: string;
  licence: string;
  replace: boolean;
  bindings: Record<string, ModuleBinding>;
  outputs: { name: string; key: string }[];
  settings: string[];
  casts: Record<string, unknown>;
  flows: string[];
  automations: string[];
}

/** One unfinished import: which source, and the answers it has been given. */
export interface ImportDraft {
  /** What this draft is about -- see the module docstring. */
  id: string;
  /** What the list calls it: the module's name, or the source's until one is typed. */
  title: string;
  /** When it was last written, in epoch milliseconds. Newest is first. */
  at: number;
  kind: DraftKind;
  /** The automation's id or the blueprint's path; empty for a pasted document. */
  key: string;
  /** The pasted document; empty for a source the house already has. */
  text: string;
  answers: DraftAnswers;
}

/**
 * The least of a `Storage` this needs, so a test can hand it an object.
 *
 * `localStorage` itself is deliberately not named: the module has to work when
 * there is none (a browser with storage turned off answers `null`), and a test
 * has no business standing up a DOM to ask whether a draft survives.
 */
export interface DraftStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

/**
 * The id a source's draft is filed under.
 *
 * Exported because two callers have to agree about it: the screen, when it
 * decides which draft is the one it is working on, and the list, when it decides
 * which one *not* to offer back. A rule spelled twice is a rule that will
 * disagree.
 */
export function draftId(kind: DraftKind, key: string, text: string): string {
  if (kind === "text") return `text:${fingerprint(text)}`;
  return `${kind}:${key}`;
}

/**
 * A short, stable spelling of a document, for filing a pasted one under.
 *
 * FNV-1a, and not a cryptographic hash: this decides which draft a pasted
 * blueprint is, and the two failures are a collision (two documents offered as
 * one draft) and a length (a key too long for the store). Neither is worth a
 * dependency, and neither is a security property -- a person can only collide
 * with themselves.
 */
export function fingerprint(text: string): string {
  let hash = 0x811c9dc5;
  for (let at = 0; at < text.length; at += 1) {
    hash ^= text.charCodeAt(at);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(16).padStart(8, "0");
}

/**
 * Every draft the store holds, newest first.
 *
 * Never throws and never refuses: a store that cannot be read is a store with
 * nothing in it, which is the same answer as a first visit. That is the whole
 * of what a draft is worth -- a person who cannot be given their unfinished form
 * back is where they would have been anyway, and one who is shown an error
 * banner instead has been told their panel is broken by a feature that only
 * exists to be helpful.
 */
export function read(storage: DraftStorage | null): ImportDraft[] {
  const raw = safely(storage, (held) => held.getItem(DRAFT_KEY));
  if (typeof raw !== "string" || !raw) return [];
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return [];
  }
  if (!isRecord(parsed) || parsed.version !== DRAFT_VERSION) return [];
  if (!Array.isArray(parsed.drafts)) return [];
  return parsed.drafts
    .map(readable)
    .filter((draft): draft is ImportDraft => draft !== null)
    .sort((left, right) => right.at - left.at);
}

/**
 * `draft` written into `storage`, and the list as it now stands.
 *
 * Replaced rather than appended when a draft of the same id is already there:
 * a draft is the *current* answers to one source, so the second write is the
 * first one superseded, and a store that kept both would offer a person two
 * versions of the same unfinished form with nothing to tell them apart.
 */
export function write(
  storage: DraftStorage | null,
  draft: ImportDraft,
): ImportDraft[] {
  const kept = read(storage).filter((held) => held.id !== draft.id);
  const drafts = [draft, ...kept];
  safely(storage, (held) =>
    held.setItem(DRAFT_KEY, JSON.stringify({ version: DRAFT_VERSION, drafts })),
  );
  return drafts.sort((left, right) => right.at - left.at);
}

/** `id` taken away, and the list as it now stands. */
export function forget(
  storage: DraftStorage | null,
  id: string,
): ImportDraft[] {
  const drafts = read(storage).filter((held) => held.id !== id);
  safely(storage, (held) =>
    held.setItem(DRAFT_KEY, JSON.stringify({ version: DRAFT_VERSION, drafts })),
  );
  return drafts;
}

/**
 * One row, as far as this module can vouch for it, or `null`.
 *
 * Every field is checked and not just the ones the list draws, because a draft
 * is read back *into the screen*: a row with a `settings` that is not a list of
 * names would put a row's switch on the wrong input, and a row whose `kind` is
 * not one of the three would be read as a source this screen cannot offer. A row
 * that fails any of them is dropped whole -- half a draft is worse than none,
 * because the half that is missing is what the screen would fill in silently.
 */
function readable(value: unknown): ImportDraft | null {
  if (!isRecord(value)) return null;
  const { id, title, at, kind, key, text, answers } = value;
  if (typeof id !== "string" || !id) return null;
  if (typeof title !== "string") return null;
  if (typeof at !== "number" || !Number.isFinite(at)) return null;
  if (kind !== "automation" && kind !== "blueprint" && kind !== "text") return null;
  if (typeof key !== "string" || typeof text !== "string") return null;
  const held = readableAnswers(answers);
  if (!held) return null;
  return { id, title, at, kind, key, text, answers: held };
}

function readableAnswers(value: unknown): DraftAnswers | null {
  if (!isRecord(value)) return null;
  const {
    title,
    description,
    author,
    version,
    licence,
    replace,
    bindings,
    outputs,
    settings,
    casts,
    flows,
    automations,
  } = value;
  if (
    typeof title !== "string" ||
    typeof description !== "string" ||
    typeof author !== "string" ||
    typeof version !== "string" ||
    typeof licence !== "string"
  ) {
    return null;
  }
  if (typeof replace !== "boolean") return null;
  if (!isRecord(bindings) || !isRecord(casts)) return null;
  if (!strings(settings) || !strings(flows) || !strings(automations)) return null;
  if (!Array.isArray(outputs)) return null;
  const picks = outputs.filter(
    (output): output is { name: string; key: string } =>
      isRecord(output) &&
      typeof output.name === "string" &&
      typeof output.key === "string",
  );
  return {
    title,
    description,
    author,
    version,
    licence,
    replace,
    // Cast to the models' types on the way out rather than validated field by
    // field: `bindings` and `casts` are the *server's* shapes, and this module
    // has no business being a second copy of the server's schema. What it
    // vouches for is that they are objects, which is what the screen reads them
    // as -- and a wrong one is refused by the server with a sentence, on save,
    // which is where every other wrong answer is refused.
    bindings: bindings as Record<string, ModuleBinding>,
    outputs: picks,
    settings,
    casts,
    flows,
    automations,
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function strings(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((one) => typeof one === "string");
}

/**
 * `act` on the store, or nothing at all if the store is missing or refuses.
 *
 * **A browser with no storage is not an error.** `localStorage` is `null` under
 * a policy that forbids it, and `setItem` throws `QuotaExceededError` when the
 * origin is full -- and both of those are reasons a person does not get their
 * unfinished form back, which is a disappointment. Letting either reach the
 * screen would make it an error banner over an import that is otherwise fine,
 * which is a lie about what is wrong.
 */
function safely<T>(
  storage: DraftStorage | null,
  act: (held: DraftStorage) => T,
): T | undefined {
  if (!storage) return undefined;
  try {
    return act(storage);
  } catch {
    return undefined;
  }
}
