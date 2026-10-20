/**
 * Unit tests for what an unfinished import is, and what it survives.
 *
 * The screen itself is checked by looking at it. What is pinned here is the
 * store, because it is the one part of the feature with no screen to look at:
 * a draft that is written and not read back, or read back into a form filled
 * from the wrong document, looks exactly like a draft that worked until the
 * moment somebody needs it -- which is the moment they have already lost the
 * work it was supposed to save.
 *
 * Four claims, and each is a way the feature is worse than nothing if it is
 * wrong:
 *
 *   * **A draft is filed under what it is about.** Two imports of two sources
 *     are two drafts; two writes of one source are one.
 *   * **A draft survives the shape of the store being wrong.** A row that cannot
 *     be read is dropped and the rest are kept -- the reader is the reporting
 *     one, and a browser that died between two writes must not be a person
 *     locked out of their own panel.
 *   * **A store that is missing or full is not an error.** No storage at all is
 *     the ordinary state of a browser with it turned off, and it has to leave the
 *     screen working.
 *   * **The list is newest first**, because the row a person wants is the one
 *     they were just working on.
 */

import { strict as assert } from "node:assert";
import { test } from "node:test";

import {
  DRAFT_KEY,
  DRAFT_VERSION,
  draftId,
  fingerprint,
  forget,
  read,
  write,
  type DraftAnswers,
  type DraftStorage,
  type ImportDraft,
} from "./drafts.ts";

/** A store in memory, with the two methods this module is allowed to use. */
function store_(): DraftStorage & { held: string | null } {
  const box = {
    held: null as string | null,
    getItem(key: string): string | null {
      return key === DRAFT_KEY ? box.held : null;
    },
    setItem(key: string, value: string): void {
      if (key === DRAFT_KEY) box.held = value;
    },
  };
  return box;
}

/** The answers a save would send, with nothing in them. */
function answers_(rest: Partial<DraftAnswers> = {}): DraftAnswers {
  return {
    title: "Night lights",
    description: "",
    author: "",
    version: "1.0.0",
    licence: "no_licence",
    replace: false,
    bindings: {},
    outputs: [],
    settings: [],
    casts: {},
    flows: [],
    automations: [],
    ...rest,
  };
}

function draft_(rest: Partial<ImportDraft> = {}): ImportDraft {
  return {
    id: draftId("automation", "1700000000001", ""),
    title: "Night lights",
    at: 1_700_000_000_000,
    kind: "automation",
    key: "1700000000001",
    text: "",
    answers: answers_(),
    ...rest,
  };
}

test("a draft is filed under the source it is about", () => {
  // The id is what makes "the import I was doing" one thing: an automation by
  // its own id, a blueprint by its path, and a pasted document by a print of
  // itself -- because a pasted document has no name, and two of them are two
  // imports rather than one that keeps being overwritten.
  assert.equal(draftId("automation", "1700000000001", ""), "automation:1700000000001");
  assert.equal(draftId("blueprint", "blueprints/night.yaml", ""), "blueprint:blueprints/night.yaml");
  assert.match(draftId("text", "", "alias: x"), /^text:[0-9a-f]{8}$/);
  assert.notEqual(draftId("text", "", "alias: x"), draftId("text", "", "alias: y"));
  // And a source that has not been read yet is still one source, so a screen
  // that has just switched tabs writes over the same row rather than adding one.
  assert.equal(draftId("automation", "a", ""), draftId("automation", "a", "ignored"));
});

test("a print of a document is short, stable and different for different text", () => {
  const once = fingerprint("alias: Night lights\n");
  assert.equal(once, fingerprint("alias: Night lights\n"));
  assert.equal(once.length, 8);
  assert.notEqual(once, fingerprint("alias: Night lights"));
});

test("what is written is what comes back", () => {
  const store = store_();
  const draft = draft_({ answers: answers_({ settings: ["lux_sensor"] }) });
  const list = write(store, draft);
  assert.deepEqual(list, [draft]);
  assert.deepEqual(read(store), [draft]);
});

test("one source is one draft, however many times it is written", () => {
  // Every keystroke writes. A store that kept each one would offer a person
  // twenty versions of the same unfinished form with nothing to tell them apart.
  const store = store_();
  write(store, draft_({ answers: answers_({ title: "first" }) }));
  write(store, draft_({ answers: answers_({ title: "second" }) }));
  const held = read(store);
  assert.equal(held.length, 1);
  assert.equal(held[0]?.answers.title, "second");
});

test("two sources are two drafts, and the newest is first", () => {
  const store = store_();
  const older = draft_({ at: 1_000 });
  const newer = draft_({
    id: draftId("blueprint", "blueprints/night.yaml", ""),
    kind: "blueprint",
    key: "blueprints/night.yaml",
    at: 2_000,
  });
  write(store, older);
  const list = write(store, newer);
  assert.deepEqual(
    list.map((draft) => draft.id),
    [newer.id, older.id],
  );
  // And the order survives the round trip through the store, not just the
  // answer this call happens to return.
  assert.deepEqual(
    read(store).map((draft) => draft.id),
    [newer.id, older.id],
  );
});

test("a draft can be given up on without disturbing the others", () => {
  const store = store_();
  const one = draft_();
  const two = draft_({
    id: draftId("blueprint", "blueprints/night.yaml", ""),
    kind: "blueprint",
    key: "blueprints/night.yaml",
    at: 2_000,
  });
  write(store, one);
  write(store, two);
  assert.deepEqual(
    forget(store, one.id).map((draft) => draft.id),
    [two.id],
  );
  assert.deepEqual(
    read(store).map((draft) => draft.id),
    [two.id],
  );
  // Forgetting one that is not there is a no-op rather than a refusal: the
  // caller is a button, and the answer to "it is already gone" is the list
  // without it, which is what they asked for.
  assert.deepEqual(
    forget(store, "automation:nobody").map((draft) => draft.id),
    [two.id],
  );
});

test("a store that cannot be read is a store with nothing in it", () => {
  // Every one of these is a browser doing something ordinary: no storage at all
  // (a policy forbids it), a key somebody wrote by hand, a value from a version
  // whose shape is not this one, and a document cut off mid-write by a browser
  // that died. None is an error to show a person, because none of them is
  // something they can act on -- the answer to all four is an empty list.
  assert.deepEqual(read(null), []);
  assert.deepEqual(read(store_()), []);

  const junk = store_();
  junk.held = "not json at all";
  assert.deepEqual(read(junk), []);

  const other = store_();
  other.held = JSON.stringify({ version: DRAFT_VERSION + 1, drafts: [draft_()] });
  assert.deepEqual(read(other), []);

  const wrong = store_();
  wrong.held = JSON.stringify({ drafts: [draft_()] });
  assert.deepEqual(read(wrong), []);

  const cut = store_();
  cut.held = JSON.stringify({ version: DRAFT_VERSION, drafts: [draft_()] }).slice(0, 60);
  assert.deepEqual(read(cut), []);
});

test("a row that cannot be read is dropped and the rest are kept", () => {
  // The reporting reader, the same one `engine/custom_slots.record_from` is: a
  // store written by an earlier version, or by hand, must not take the whole
  // panel with it. The good row is *beside* the bad ones deliberately -- a
  // reader that gave up at the first bad row would pass a test that only ever
  // held one.
  const store = store_();
  const good = draft_();
  const rows = [
    good,
    // No id at all: nothing to file it under, so nothing to offer back.
    { ...draft_(), id: "" },
    // A kind this screen cannot offer, which would be read as a source with no
    // picker.
    { ...draft_(), id: "automation:x", kind: "script" },
    // Answers that are not answers: a `settings` that is not a list of names
    // would put a switch on the wrong input.
    { ...draft_(), id: "automation:y", answers: { ...answers_(), settings: "lux_sensor" } },
    // A `bindings` that is not a mapping, for the same reason.
    { ...draft_(), id: "automation:z", answers: { ...answers_(), bindings: [] } },
    // An `at` that is not a time: the list sorts by it.
    { ...draft_(), id: "automation:w", at: "yesterday" },
    // Not a row at all.
    "nonsense",
  ];
  store.held = JSON.stringify({ version: DRAFT_VERSION, drafts: rows });
  assert.deepEqual(
    read(store).map((draft) => draft.id),
    [good.id],
  );
});

test("a store that refuses to write is not an error either", () => {
  // A full origin throws `QuotaExceededError` on the write, and a browser with
  // storage turned off answers `null` for the property. Both mean a person does
  // not get their unfinished form back -- a disappointment, and the one thing
  // that must not happen is an error banner over an import that is otherwise
  // fine, because that is a lie about what is wrong.
  const full: DraftStorage = {
    getItem: () => null,
    setItem: () => {
      throw new Error("QuotaExceededError");
    },
  };
  const draft = draft_();
  assert.deepEqual(write(full, draft), [draft]);
  assert.deepEqual(forget(full, draft.id), []);

  // And no store at all, which is the same story told without an exception.
  assert.deepEqual(write(null, draft), [draft]);
  assert.deepEqual(forget(null, draft.id), []);
});
