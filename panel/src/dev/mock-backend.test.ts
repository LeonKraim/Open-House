/**
 * Unit tests for the in-page mock backend.
 *
 * The mock is a *development aid* -- nothing imports it but `npm run dev` -- and
 * that is exactly why it needs pinning: a screen developed against a backend
 * that answers the wrong thing looks finished and is not, and the failure lands
 * on somebody with a real Home Assistant. Nothing else in the repository reads
 * this file, so these are the only tests that can notice it drifting from the
 * protocol it claims to implement.
 *
 * They run under Node's own test runner with type stripping (`npm test`), the
 * same way the rest do, and they drive the mock the way the panel does: through
 * `HaConnection`, not through its internals.
 */

import { strict as assert } from "node:assert";
import { test } from "node:test";

import { COMMANDS } from "../api/protocol.ts";
import type { PublishedRow, RoomDetail } from "../api/models.ts";
import type { HaConnection } from "../api/connection.ts";
import { mockHass } from "./mock-backend.ts";

/** The mock's connection, which `mockHass` always supplies. */
function connection(): HaConnection {
  const hass = mockHass();
  assert.ok(hass.connection, "mockHass built no connection");
  return hass.connection;
}

/** The one command the panel sends as a subscription rather than a reply. */
const VIA_SUBSCRIPTION = new Set<string>([COMMANDS.activitySubscribe]);

test("the mock has heard of every command the panel can send", async () => {
  // **The coverage pin, and it is deliberately blunt.** A command with no
  // `case` falls through to the mock's own `default`, which throws
  // "mock backend has no answer for ...": the screen that sent it renders a
  // red banner in `npm run dev` and the command is written off as a server
  // problem. `open_house/modules/read` was missing this way, and so were twelve
  // others, and nothing noticed because nothing enumerated the protocol.
  //
  // Every command is sent with an empty payload, so most of them *refuse* --
  // which is the point: what this pins is that the mock has heard of the
  // command at all, and a refusal is an answer.
  const conn = connection();
  const unanswered: string[] = [];
  for (const type of Object.values(COMMANDS)) {
    if (VIA_SUBSCRIPTION.has(type)) continue;
    try {
      await conn.sendMessagePromise({ type });
    } catch (error) {
      if (error instanceof Error && /has no answer for/.test(error.message)) {
        unanswered.push(type);
      }
    }
  }
  assert.deepEqual(
    unanswered,
    [],
    "the protocol names commands the mock cannot answer:\n" + unanswered.join("\n"),
  );
});

test("the mock streams the one subscription the panel opens", async (t) => {
  // The other half of the pin: `activity/subscribe` must not be matched by the
  // loop above, so it is exercised through the door it actually uses.
  t.mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const events: unknown[] = [];
    const unsubscribe = await connection().subscribeMessage(
      (event) => events.push(event),
      { type: COMMANDS.activitySubscribe },
    );
    assert.deepEqual(events, [], "an entry arrived before anything happened");
    t.mock.timers.tick(1500);
    assert.equal(events.length, 1, "a live subscriber got no entry");
    await unsubscribe();
  } finally {
    t.mock.timers.reset();
  }
});

test("an unsubscribed screen is not sent anything more", async (t) => {
  // **The bug this found.** The mock pushed its one entry from a bare
  // `setTimeout` that nothing cleared, so a screen that had *dropped* its
  // subscription -- the activity tab does, the moment it disconnects -- still
  // received one. The real connection cannot do that, so the mock was teaching
  // the panel a shape of bug it could never see anywhere else.
  t.mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const events: unknown[] = [];
    const unsubscribe = await connection().subscribeMessage(
      (event) => events.push(event),
      { type: COMMANDS.activitySubscribe },
    );
    await unsubscribe();
    t.mock.timers.tick(10_000);
    assert.deepEqual(events, [], "an event arrived after the subscription was dropped");
  } finally {
    t.mock.timers.reset();
  }
});

test("a created room is a room the house holds", async () => {
  // The mock answered `roomCreate` with `roomDetail(payload.room_id)`, and a
  // create sends no `room_id` -- so the reply was the Kitchen's page whatever
  // was typed, `rooms/list` never grew the room, and every command that
  // addressed it afterwards fell back to the Kitchen too.
  const conn = connection();
  const made = (await conn.sendMessagePromise({
    type: COMMANDS.roomCreate,
    name: "Study",
    room_type: "office",
  })) as RoomDetail;
  assert.equal(made.id, "study");
  assert.equal(made.name, "Study");
  assert.equal(made.type_label, "Office");

  const list = (await conn.sendMessagePromise({ type: COMMANDS.roomsList })) as {
    rooms: { id: string; name: string }[];
  };
  assert.ok(
    list.rooms.some((room) => room.id === "study"),
    "the room was created and is not in the house",
  );

  // And the page reads back by the id the reply named, which is what every
  // screen that opens the room next will address.
  const read = (await conn.sendMessagePromise({
    type: COMMANDS.roomGet,
    room_id: "study",
  })) as RoomDetail;
  assert.equal(read.name, "Study");
});

test("renaming a room renames the room", async () => {
  const conn = connection();
  await conn.sendMessagePromise({
    type: COMMANDS.roomCreate,
    name: "Parlour",
    room_type: "living_room",
  });
  const renamed = (await conn.sendMessagePromise({
    type: COMMANDS.roomUpdate,
    room_id: "parlour",
    name: "Drawing room",
  })) as RoomDetail;
  assert.equal(renamed.name, "Drawing room");
  const list = (await conn.sendMessagePromise({ type: COMMANDS.roomsList })) as {
    rooms: { id: string; name: string }[];
  };
  assert.equal(list.rooms.find((room) => room.id === "parlour")?.name, "Drawing room");
});

test("a room that is not there is refused, not answered with another room", async () => {
  // `roomDetail` fell back to the first room in the fixture, so an unknown id
  // answered the Kitchen's page -- a screen rendering a room that does not exist
  // and looking entirely right doing it.
  const conn = connection();
  await conn.sendMessagePromise({
    type: COMMANDS.roomCreate,
    name: "Attic",
    room_type: "other",
  });
  await conn.sendMessagePromise({ type: COMMANDS.roomDelete, room_id: "attic" });
  await assert.rejects(
    () => conn.sendMessagePromise({ type: COMMANDS.roomGet, room_id: "attic" }),
    (error: unknown) =>
      typeof error === "object" && error !== null && (error as { code?: string }).code === "not_found",
  );
});

test("a refusal the mock makes carries the code the panel branches on", async () => {
  // The mock threw bare `Error`s for everything the *server* refuses, and the
  // client maps a plain `Error` to the code `unknown` -- so a room that is not
  // there read as "Something went wrong" over a sentence the fixture had
  // written for exactly that case.
  assert.deepEqual(await refusalOf({ type: COMMANDS.roomDelete, room_id: "nowhere" }), {
    code: "not_found",
    message: "no room nowhere",
  });
});

/** The `{code, message}` a command was refused with. */
async function refusalOf(message: Record<string, unknown>): Promise<unknown> {
  try {
    await connection().sendMessagePromise(message);
  } catch (error) {
    return error;
  }
  throw new Error(`the mock answered ${String(message.type)} instead of refusing it`);
}

test("publishing a row answers with the store as well as the house", async () => {
  // The server rewrites the definition behind a published row, so its reply
  // carries the store too -- and the client's own return type says so. The mock
  // answered `{module, modules}` and left `store` undefined, so a screen reading
  // it drew an empty store.
  const conn = connection();
  const reply = (await conn.sendMessagePromise({
    type: COMMANDS.modulesPublish,
    module: "evening_lighting_kitchen",
    setting: "threshold",
    publish: true,
  })) as { store?: unknown[] };
  assert.ok(Array.isArray(reply.store), "the store did not come back");
  assert.ok((reply.store ?? []).length > 0, "the store came back empty");
});

test("editing a module without its answers is refused the way the server refuses it", async () => {
  // The server's schema is `vol.Required` for these six, and the client's type
  // said they were optional -- so a caller who left one out sent nothing at all
  // and got a refusal naming a field the type had told them they did not owe.
  const refused = await refusalOf({
    type: COMMANDS.modulesEdit,
    module: "evening_lighting_kitchen",
    kind: "text",
    title: "Evening lighting",
    bindings: {},
    outputs: [],
    settings: [],
    casts: {},
    flows: [],
    // `automations` left out on purpose.
  });
  assert.deepEqual(refused, {
    code: "invalid_format",
    message: "automations is required to edit a module",
  });
});

test("editing a module with every answer is answered with the house and the store", async () => {
  const conn = connection();
  const reply = (await conn.sendMessagePromise({
    type: COMMANDS.modulesEdit,
    module: "evening_lighting_kitchen",
    kind: "text",
    title: "Evening lighting, retitled",
    bindings: {},
    outputs: [],
    settings: [],
    casts: {},
    flows: [],
    automations: [],
  })) as { module: string; modules: { slug: string; title: string }[]; store: unknown[] };
  assert.equal(reply.module, "evening_lighting_kitchen");
  assert.equal(
    reply.modules.find((row) => row.slug === "evening_lighting_kitchen")?.title,
    "Evening lighting, retitled",
  );
  assert.ok(Array.isArray(reply.store));
});

test("taking a house profile puts the house on it, and moves the revision", async () => {
  // Capture is `activate_house` by another door: the house goes on the profile
  // as part of taking it, so a page rendered before it is stale -- and a mock
  // that left the revision alone would let a write from that page land.
  const conn = connection();
  const before = (await conn.sendMessagePromise({
    type: COMMANDS.roomGet,
    room_id: "kitchen",
  })) as RoomDetail;

  const reply = (await conn.sendMessagePromise({
    type: COMMANDS.profileCapture,
    name: "Christmas Lights",
  })) as { profiles: { name: string; kind: string; active: boolean }[] };
  const taken = reply.profiles.find((profile) => profile.name === "christmas_lights");
  assert.ok(taken, "the profile was taken and is not in the list");
  assert.equal(taken.kind, "house");
  assert.equal(taken.active, true);

  const after = (await conn.sendMessagePromise({
    type: COMMANDS.roomGet,
    room_id: "kitchen",
  })) as RoomDetail;
  assert.equal(after.revision, before.revision + 1, "the house moved and the revision did not");

  const refused = await refusalOf({
    type: COMMANDS.roomBind,
    room_id: "kitchen",
    slot: "ceiling_light",
    entity_id: "light.kitchen",
    revision: before.revision,
  });
  assert.equal((refused as { code?: string }).code, "stale_page");
});

test("renaming a profile keeps everything that named it", async () => {
  const conn = connection();
  const reply = (await conn.sendMessagePromise({
    type: COMMANDS.profileRename,
    profile: "evening",
    to: "Late evening",
  })) as { profiles: { name: string; label: string }[] };
  assert.ok(
    reply.profiles.some((profile) => profile.name === "late_evening"),
    "the renamed profile is not in the list",
  );
  assert.equal(
    reply.profiles.some((profile) => profile.name === "evening"),
    false,
    "the old name is still there",
  );
});

test("removing a profile takes it off the list", async () => {
  const conn = connection();
  const reply = (await conn.sendMessagePromise({
    type: COMMANDS.profileRemove,
    profile: "dim",
  })) as { profiles: { name: string }[] };
  assert.equal(reply.profiles.some((profile) => profile.name === "dim"), false);
});

test("the dev tab's lists name what the fixture holds", async () => {
  const conn = connection();
  const reply = (await conn.sendMessagePromise({ type: COMMANDS.devSources })) as {
    automations: { key: string }[];
    blueprints: { key: string; domain?: string }[];
  };
  assert.ok(reply.automations.length > 0);
  assert.ok(reply.blueprints.length > 0);
});

test("hosting a source adds a module to the house", async () => {
  const conn = connection();
  const reply = (await conn.sendMessagePromise({
    type: COMMANDS.modulesHost,
    kind: "text",
    text: "- trigger: []",
    title: "Hallway at night",
    bindings: { motion: { kind: "slot", slot: "motion_sensor" } },
    outputs: [{ name: "lux", key: "lux" }],
  })) as { module: string; modules: { slug: string; outputs: { key: string }[] }[] };
  assert.equal(reply.module, "hallway_at_night");
  const hosted = reply.modules.find((row) => row.slug === "hallway_at_night");
  assert.ok(hosted, "the module was hosted and is not in the house");
  assert.deepEqual(
    hosted.outputs.map((output) => output.key),
    ["lux"],
  );
});

test("reading a module answers with the module's own document to edit", async () => {
  // The one command the brief names: implemented on the server and absent here,
  // so every screen that opened a module to edit it failed in `npm run dev`.
  const conn = connection();
  const reply = (await conn.sendMessagePromise({
    type: COMMANDS.modulesRead,
    kind: "text",
    module: "evening_lighting_kitchen",
  })) as {
    source: { title: string; inputs: number };
    inputs: unknown[];
    slots: unknown[];
    hosted: unknown[];
    text?: string;
    editing?: { module: string; installs: unknown[] };
  };
  assert.equal(reply.editing?.module, "evening_lighting_kitchen");
  assert.equal(typeof reply.text, "string");
  assert.ok(reply.slots.length > 0);
  assert.ok(reply.editing?.installs.length);
});

test("reading a module the house does not host is refused", async () => {
  const refused = await refusalOf({
    type: COMMANDS.modulesRead,
    kind: "text",
    module: "nothing_called_this",
  });
  assert.equal((refused as { code?: string }).code, "invalid_format");
});

test("the published Store answers with an address and, at first, no name", async () => {
  // The claim form is on screen only while `name` is empty, so a mock that
  // shipped a name would hide the form the walk needs to reach.
  const conn = connection();
  const status = (await conn.sendMessagePromise({
    type: COMMANDS.publishedStatus,
  })) as { url: string; name: string };
  assert.ok(status.url !== "", "the mock Store has no address");
  assert.equal(status.name, "");
});

test("the published list is split by the slug this house already holds", async () => {
  // The split is the server's: the row whose slug names a module this house
  // offers is installed, and the rest are not. A mock that split on anything
  // else would let the filter show an installed module under "Not installed".
  const conn = connection();
  const split = (await conn.sendMessagePromise({
    type: COMMANDS.publishedBrowse,
  })) as { installed: { slug: string }[]; not_installed: { slug: string }[] };
  assert.ok(
    split.installed.some((row) => row.slug === "porch_lamp"),
    "a module this house holds is not on the installed side",
  );
  assert.ok(
    split.not_installed.some((row) => row.slug === "sunrise_alarm"),
    "a module this house does not hold is not on the not-installed side",
  );
});

test("the published fixtures cover a rated row, an unrated one and a published one", async () => {
  const conn = connection();
  const split = (await conn.sendMessagePromise({
    type: COMMANDS.publishedBrowse,
  })) as { installed: PublishedRow[]; not_installed: PublishedRow[] };
  const rows = [...split.installed, ...split.not_installed];
  assert.ok(rows.some((row) => row.mine), "no row is this install's own");
  assert.ok(rows.some((row) => row.rating === null), "no row is unrated");
  assert.ok(rows.some((row) => row.rating !== null), "no row is rated");
});

test("a published module's comments come back as a list", async () => {
  const conn = connection();
  const reply = (await conn.sendMessagePromise({
    type: COMMANDS.publishedComments,
    module_id: "pub-sunrise",
  })) as { comments: { body: string }[] };
  assert.ok(reply.comments.length > 0, "the fixture row has no comments");
});

test("the Store refuses a name somebody has taken, in its own words", async () => {
  const refused = await refusalOf({
    type: COMMANDS.publisherClaim,
    name: "marqbarq",
  });
  assert.deepEqual(refused, {
    code: "invalid_format",
    message: "that name is taken, please pick another name",
  });
});

test("claiming a name, then publishing a module, puts it on the Store", async () => {
  // Ordered after the refusal above on purpose: claiming is once, so the
  // acceptable name can only be claimed after the taken one has been tried.
  const conn = connection();
  const claimed = (await conn.sendMessagePromise({
    type: COMMANDS.publisherClaim,
    name: "ada",
  })) as { name: string };
  assert.equal(claimed.name, "ada");

  const status = (await conn.sendMessagePromise({ type: COMMANDS.publishedStatus })) as {
    name: string;
  };
  assert.equal(status.name, "ada");

  const reply = (await conn.sendMessagePromise({
    type: COMMANDS.publishedPublish,
    module: "evening_lighting",
  })) as { published: PublishedRow; store: unknown[] };
  assert.equal(reply.published.publisher, "ada");
  assert.equal(reply.published.mine, true);
  assert.ok(Array.isArray(reply.store), "the store did not come back with the publish");
});

test("installing a module the house already holds is refused, and replace settles it", async () => {
  // The one refusal a Replace confirm exists for: the request is answered as a
  // value refused on its merits, which is the code the panel reads to offer it.
  const conn = connection();
  const first = (await conn.sendMessagePromise({
    type: COMMANDS.publishedInstall,
    module_id: "pub-guest",
  })) as { module: string; replaced: boolean };
  assert.equal(first.module, "guest_greeting");
  assert.equal(first.replaced, false);

  const refused = await refusalOf({
    type: COMMANDS.publishedInstall,
    module_id: "pub-guest",
  });
  assert.equal((refused as { code?: string }).code, "invalid_format");

  const again = (await conn.sendMessagePromise({
    type: COMMANDS.publishedInstall,
    module_id: "pub-guest",
    replace: true,
  })) as { replaced: boolean };
  assert.equal(again.replaced, true);
});

// -- the Publish button's setup step -------------------------------------------
//
// These run last and in this order on purpose. The mock's Store is module-level
// state shared by every test in this file, and the two below deliberately walk it
// into a house with no Store and back out again -- so anything ordered after them
// would be reading a mock somebody else had reconfigured.

test("an empty address is a house with no Store, and drops the claimed name", async () => {
  // The state the Publish button now has to be able to leave from, and the only
  // way to reach it in the mock: the same `publishedConfigure` command the screen
  // sends. Clearing the address forgets the name, because a name belongs to the
  // Store that issued it -- the rule that makes the dialog ask for the address
  // *before* the name.
  const conn = connection();
  const cleared = (await conn.sendMessagePromise({
    type: COMMANDS.publishedConfigure,
    url: "",
  })) as { url: string; name: string };
  assert.equal(cleared.url, "");
  assert.equal(cleared.name, "");

  const status = (await conn.sendMessagePromise({
    type: COMMANDS.publishedStatus,
  })) as { url: string; name: string };
  assert.equal(status.url, "");
});

test("connecting a Store and claiming a name is what makes a publish go through", async () => {
  // The dialog's three steps, in the order it performs them: set the address,
  // claim the name, publish. Before the address there is nothing to publish to,
  // so the first two are what the third needs rather than a formality before it.
  const conn = connection();
  const connected = (await conn.sendMessagePromise({
    type: COMMANDS.publishedConfigure,
    url: "https://store.example",
  })) as { url: string; name: string };
  assert.equal(connected.url, "https://store.example");
  assert.equal(connected.name, "");

  await conn.sendMessagePromise({ type: COMMANDS.publisherClaim, name: "walkhouse" });

  const status = (await conn.sendMessagePromise({
    type: COMMANDS.publishedStatus,
  })) as { url: string; name: string };
  assert.equal(status.url, "https://store.example");
  assert.equal(status.name, "walkhouse");

  const reply = (await conn.sendMessagePromise({
    type: COMMANDS.publishedPublish,
    module: "evening_lighting",
  })) as { published: PublishedRow };
  assert.equal(reply.published.publisher, "walkhouse");
});
