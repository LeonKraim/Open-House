/**
 * The end-to-end walk: set a whole house up through the panel, as a person.
 *
 * The point of this script is that it touches nothing but the rendered page.
 * It does not call the websocket API, does not read `.storage`, and does not
 * import anything from `custom_components/` -- it finds controls the way a
 * person finds them (see `_ui.mjs`) and clicks them at their real coordinates,
 * so a control that is present but inert fails here exactly as it fails for a
 * user. That is not a stylistic preference: the defects this walk was written
 * to catch were all of that shape -- a button that rendered, took the click,
 * and changed nothing on screen.
 *
 * It runs in steps and prints one line per action, so a failure names the step
 * that failed rather than a stack trace somewhere in the middle.
 *
 * Run: `node panel/scripts/browser-e2e.mjs`
 */

import { openPanel, all, text, click, type, sleep } from "./_ui.mjs";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

/** The repository root, which is where the world-moving script is run from. */
const ROOT = fileURLToPath(new URL("../../", import.meta.url));
const HA_BASE = process.env.HA_BASE_URL ?? "http://localhost:8123";

/**
 * Move the mock house the way the physical world moves a real one.
 *
 * This is the only thing in the walk that does not go through the browser, and
 * it is not an exception to the rule that the panel is driven as a person
 * drives it: a person cannot click to make themselves walk into a room. The
 * sensor notices and publishes. So the walk publishes what a sensor would
 * publish, and then looks at the *panel* to see what the house did about it.
 */
function world(...args) {
  return execFileSync("python", [".local/stimulus.py", ...args], {
    cwd: ROOT,
    encoding: "utf8",
  }).trim();
}

/**
 * The long-lived token, for the reads that do not go through the page.
 *
 * `.local/ha_tokens.json` is what the *browser* is logged in as, and its access
 * token is a session token that expires in half an hour. A walk that runs longer
 * than that -- this one waits five minutes for a room to go quiet, and longer
 * once a run or two have gone before it -- finds its own HTTP reads answering
 * `HTTP 401` while the page it is driving is still logged in perfectly well,
 * because the page refreshes its own token and a file cannot. `HA_TOKEN` in
 * `.env.local` is a long-lived access token, and a read taken outside the page
 * is what it is for.
 */
let longTokenCache = null;
function longLivedToken() {
  if (longTokenCache !== null) return longTokenCache;
  const text = readFileSync(`${ROOT}.env.local`, "utf8");
  for (const line of text.split(/\r?\n/)) {
    if (!line.startsWith("HA_TOKEN=")) continue;
    longTokenCache = line.slice("HA_TOKEN=".length).trim().replace(/^"|"$/g, "");
    return longTokenCache;
  }
  throw new Error("no HA_TOKEN in .env.local");
}

/** What Home Assistant presently holds for one entity. */
async function haState(entityId) {
  const response = await fetch(`${HA_BASE}/api/states/${entityId}`, {
    headers: { Authorization: `Bearer ${longLivedToken()}` },
  });
  return response.ok ? (await response.json()).state : `HTTP ${response.status}`;
}

/**
 * The newest Activity row that says the house *acted*, or `null`.
 *
 * "Newest" is measured against the rows the caller had already seen, by their
 * rendered text, because that is what the tab shows and what a person reads.
 * Opening the row's "Why" is part of finding it: the collapsed row does not
 * carry the entity the decision was about, so the walk opens the row the way a
 * person does and the caller reads the detail with `activityEntity`.
 */
const activityAction = (page, known) =>
  page.evaluate((seen) => {
    const rows = window.__deepAll("open-house-tab-activity tbody tr");
    for (const row of rows) {
      const text = window.__deepText(row).replace(/\s+/g, " ").trim();
      if (seen.includes(text)) continue;
      // `applied` is the panel's word for the engine's `Outcome.ACTED`; the
      // translation is `ha_adapter/live_export.py`'s, and the wire carries the
      // panel's word, so this is the word to match. It is matched without case
      // because the chip draws it the way the outcome menu spells it -- the
      // filter and the row it found are one outcome seen twice, and a person who
      // filtered for "Applied" then meets the same word on the row.
      if (!/\bapplied\b/i.test(text)) continue;
      const why = [...row.querySelectorAll("button")].find(
        (b) => (b.textContent ?? "").trim() === "Why",
      );
      if (!why) continue;
      why.click();
      return text;
    }
    return null;
  }, known);

/**
 * The entity the open Activity detail is about, or `null`.
 *
 * Read from the detail's own entity line rather than from the reason sentence.
 * The reason names every entity the decision consulted -- the motion sensor,
 * the lux sensor, the light -- and the first of those is not the one that
 * changed, so a scan of the prose answers a different question than the one
 * asked. The detail states the subject once, on its own line, above the
 * priority and the timestamp.
 */
const activityEntity = (page) =>
  page.evaluate(() => {
    const help = window.__deepAll("open-house-tab-activity .banner .help")[0];
    if (!help) return null;
    const found = (help.textContent ?? "").trim().match(/^[a-z_]+\.[a-z0-9_]+/);
    return found ? found[0] : null;
  });

/** The Activity tab's rows, as the panel renders them. */
const activityRows = (page) =>
  page.evaluate(() =>
    window
      .__deepAll("open-house-tab-activity tbody tr")
      .map((row) => window.__deepText(row).replace(/\s+/g, " ").trim()),
  );

/**
 * The house this walk builds. Room types come from the shipped catalog.
 *
 * These nine are the rooms `.local/publish_demo_house.py` announces devices
 * for -- one Home Assistant device per room, filed into that room's area -- so
 * every slot the walk reaches has a real entity behind it to be offered. Keep
 * the two lists in step: a room here with no mock device is a room whose every
 * slot answers "No match", and the walk cannot tell that apart from a panel
 * that failed to look.
 */
/**
 * The room the walk gives something to do, and then walks into.
 *
 * One room, named once: the module that acts is installed here and the motion
 * that makes it act is here, so "the room acted" is a claim about a module this
 * walk installed rather than about whatever an earlier run left behind.
 */
const ACT_ROOM = "Living Room";

const ROOMS = [
  { name: "Kitchen", type: "kitchen" },
  { name: "Living Room", type: "living_room" },
  { name: "Bedroom", type: "bedroom" },
  { name: "Bathroom", type: "bathroom" },
  { name: "Garage", type: "garage" },
  { name: "Foyer", type: "foyer" },
  { name: "Hallway", type: "hallway" },
  { name: "Office", type: "office" },
  { name: "Driveway", type: "driveway" },
];

const log = (...parts) => console.log(...parts);
let failures = 0;
const fail = (message) => {
  failures += 1;
  log("  FAIL:", message);
};

/** The room list, as the table renders it. */
const roomRows = (page) =>
  page.evaluate(() =>
    window
      .__deepAll("open-house-tab-rooms tbody tr")
      .map((row) => window.__deepText(row).replace(/\s+/g, " ").trim()),
  );

/** Go to the room list, from wherever we are. */
async function toRoomList(page) {
  for (let attempt = 0; attempt < 4; attempt += 1) {
    if (await page.evaluate(() => window.__deepAll("open-house-tab-rooms").length > 0)) {
      if (
        await page.evaluate(
          () => window.__deepAll("open-house-tab-rooms tbody tr").length > 0,
        )
      ) {
        return true;
      }
    }
    const back = await page.evaluate(
      () => window.__deepAll("open-house-room-settings button").filter(
        (b) => (b.textContent ?? "").trim() === "Back",
      ).length,
    );
    if (back > 0) await click(page, "open-house-room-settings button", { nth: "Back" });
    else if (!(await page.evaluate(() => window.__deepAll("#tab-rooms").length > 0)))
      return false;
    else await click(page, "#tab-rooms");
    // Waited for rather than slept past, and for the same reason every other
    // wait in this file exists: while Home Assistant is still booting, a
    // command sent to it is answered *late* rather than never, and a list that
    // had four seconds of patience reported a rooms tab that had not answered
    // yet. The retry above still stands, so a tab that really is stuck still
    // fails -- it just has to be stuck for a minute rather than four seconds.
    await page
      .waitForFunction(
        () => window.__deepAll("open-house-tab-rooms tbody tr").length > 0,
        null,
        { timeout: 60000, polling: 500 },
      )
      .catch(() => {});
  }
  return page.evaluate(() => window.__deepAll("open-house-tab-rooms tbody tr").length > 0);
}

/**
 * Create one room through the form, and come back to the list.
 *
 * Returns the room's settings page opening, or the refusal the server gave.
 * The form stays open on a refusal, so the caller can read the message rather
 * than watch the next step fail for the previous step's reason.
 */
async function createRoom(page, { name, type: roomType }) {
  await click(page, "open-house-tab-rooms button", { nth: "Add room" });
  await sleep(400);
  await type(page, '[placeholder="Kitchen"]', name);
  await type(page, '[placeholder="kitchen"]', roomType);
  await click(page, "open-house-tab-rooms button", { nth: "Create room" });
  // Writing a subentry reloads the config entry, and the entry has no host
  // while it reloads. The panel asks again on its own when it is told the house
  // is `not_ready` (`client.ts`), so this wait is the walk's own paranoia and
  // not a workaround: it also catches a refusal, which is a real answer and one
  // the panel is right not to retry.
  for (let attempt = 0; attempt < 20; attempt += 1) {
    await sleep(1000);
    const settled = await page.evaluate(() => {
      const failed = window.__deepAll("open-house-tab-rooms .banner.error").length > 0;
      const showing =
        window.__deepAll("open-house-tab-rooms tbody tr").length > 0 ||
        window.__deepAll("open-house-room-settings").length > 0;
      return !failed && showing;
    });
    if (settled) break;
  }
  if (await page.evaluate(() => window.__deepAll("open-house-room-settings").length > 0)) {
    await click(page, "open-house-room-settings button", { nth: "Back" });
    await sleep(1200);
    return true;
  }
  const refusal = await text(page, "open-house-tab-rooms .banner");
  await click(page, "open-house-tab-rooms button", { nth: "Cancel" }).catch(() => {});
  await sleep(500);
  return refusal ?? "the form closed without opening the room";
}

/**
 * Open one room's settings page from the room list, by the name in its row.
 *
 * Returns `true` only once the page has finished reading the room, because
 * "the element is in the DOM" and "the element has rendered the room" are two
 * different moments and every caller here wants the second. The page fetches
 * the room and shows "Reading the room..." until it lands, and a read taken in
 * that window sees a room-settings element with no bindings in it -- which is
 * indistinguishable from a room that has none, and is how this walk reported a
 * room it had just listed as bound as having nothing bound at all.
 */
async function openRoom(page, name) {
  const target = await page.evaluate((wanted) => {
    const rows = window.__deepAll("open-house-tab-rooms tbody tr");
    const row = rows.find((r) => window.__deepText(r).trim().startsWith(wanted));
    const link = row?.querySelector("a");
    if (!link) return null;
    link.scrollIntoView({ block: "center" });
    const r = link.getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
  }, name);
  if (!target) return false;
  await page.mouse.click(target.x, target.y);
  const settled = () =>
    page.evaluate(() => {
      const settings = window.__deepAll("open-house-room-settings")[0];
      if (!settings) return false;
      // Settled means one of the three things this page can finish on: the
      // room (a Devices heading), a refusal, or "no longer in the house".
      const text = window.__deepText(settings);
      if (text.includes("Reading the room")) return false;
      return (
        window.__deepAll("open-house-room-settings h2").some(
          (h) => (h.textContent ?? "").trim() === "Devices",
        ) ||
        window.__deepAll("open-house-room-settings .banner").length > 0 ||
        text.includes("no longer in the house")
      );
    });
  // The answer is *whether the page settled*, and this walk used to throw it
  // away -- it returned "the element is in the document", which is true the
  // instant the click lands, so a page that was still reading answered as an
  // open room and every read after it was taken against a blank one. It also
  // has to be patient: the first commands a freshly booted panel sends are
  // answered late rather than never, and this page needs two of them.
  for (let attempt = 0; attempt < 120; attempt += 1) {
    await sleep(500);
    if (await settled()) return true;
  }
  return false;
}

/** Leave the room settings page and come back to the room list. */
async function leaveRoom(page) {
  await click(page, "open-house-room-settings button", { nth: "Back" });
  await sleep(1500);
}

/**
 * Every slot row of the open room: label, bound-or-not, and its actions.
 *
 * Scoped to the Devices card rather than to every table on the page. A room
 * with a module installed has two tables -- the slots and the modules -- and
 * reading both would put a module's row in the middle of the slot list, where
 * it would answer a question about a slot that was never asked about it.
 */
const slotRows = (page) =>
  page.evaluate(() => {
    const card = window
      .__deepAll("open-house-room-settings .card")
      .find((c) => (c.querySelector("h2")?.textContent ?? "").trim() === "Devices");
    if (!card) return [];
    return [...card.querySelectorAll("tbody tr")].map((row) => ({
      label: window.__slotLabel(row),
      text: window.__deepText(row).replace(/\s+/g, " ").trim(),
      actions: [...row.querySelectorAll("button")].map((b) =>
        (b.textContent ?? "").trim(),
      ),
    }));
  });

/**
 * Bind one slot by clicking its own button and taking the first candidate.
 *
 * The button's label says what the row is: `Bind` when empty, `Replace` when
 * filled. `slot` picks the row, so a page that re-orders its rows cannot make
 * this bind the wrong device.
 */
async function bindSlot(page, slotLabel) {
  const opened = await page.evaluate((label) => {
    const card = window
      .__deepAll("open-house-room-settings .card")
      .find((c) => (c.querySelector("h2")?.textContent ?? "").trim() === "Devices");
    const rows = card ? [...card.querySelectorAll("tbody tr")] : [];
    const row = rows.find((r) => window.__slotLabel(r) === label);
    if (!row) return "no-row";
    const button =
      [...row.querySelectorAll("button")].find((b) => (b.textContent ?? "").trim() === "Bind") ??
      [...row.querySelectorAll("button")].find((b) => (b.textContent ?? "").trim() === "Replace");
    if (!button) return "no-button";
    button.scrollIntoView({ block: "center" });
    const r = button.getBoundingClientRect();
    window.__bindAt = { x: r.x + r.width / 2, y: r.y + r.height / 2 };
    return "ok";
  }, slotLabel);
  if (opened !== "ok") return opened;
  const at = await page.evaluate(() => window.__bindAt);
  await page.mouse.click(at.x, at.y);
  await sleep(1200);

  // A page that has gone stale draws a backdrop over itself and refuses every
  // click, so "the picker did not open" and "this page has stopped answering"
  // are the same reading from here -- and they are not the same finding. Said
  // apart, because a hundred rooms' worth of "no-picker" is what a walk reports
  // for a page that was inert from the second row on.
  const inert = await page.evaluate(
    () => window.__deepAll(".stale-backdrop").length > 0,
  );
  if (inert) return "stale-page";

  // The picker is found by the heading it carries -- "Bind the X slot", or
  // "Replace the X slot" where something is already bound -- and it is Home
  // Assistant's own entity selector that does the choosing now. It used to be
  // this panel's own list with a "Search devices" box and a "Use this" button,
  // and the walk went on looking for those after the page stopped drawing them:
  // every row in every room came back "no-picker", which is a sentence about the
  // harness that reads exactly like a sentence about the panel.
  //
  // The choosing itself is driven without a real click, and says so: the search
  // box and the list are HA's component, and driving those is a test of that
  // component rather than of this panel. What the panel owns is the
  // `value-changed` event dispatched below, which is exactly what the selector
  // emits when a person picks something; everything after it -- the write, the
  // row, its status -- is read back off the server's own answer. `_roomwide.mjs`
  // takes the same step for the same reason.
  const picker = await page.evaluate(() => {
    const card = window.__deepAll("open-house-room-settings .card").find((c) => {
      const words = window.__deepText(c).replace(/\s+/g, " ").trim();
      return words.startsWith("Bind the") || words.startsWith("Replace the");
    });
    if (!card) return { present: false };
    const selector = window.__deepAll("ha-selector", card)[0];
    // The panel's own candidate list, in the panel's own order: the row's room
    // first and the rest of the house behind it. Taking its first entry is what
    // "pick the first thing offered" meant, without a second guess at what the
    // selector happens to be showing.
    const includes = selector?.selector?.entity?.include_entities ?? [];
    const cancel = [...card.querySelectorAll("button")].find(
      (b) => (b.textContent ?? "").trim() === "Cancel",
    );
    let at = null;
    if (cancel) {
      cancel.scrollIntoView({ block: "center" });
      const r = cancel.getBoundingClientRect();
      at = { x: r.x + r.width / 2, y: r.y + r.height / 2 };
    }
    return {
      present: true,
      offers: [...includes],
      cancel: at,
      text: window.__deepText(card).replace(/\s+/g, " ").trim().slice(0, 160),
    };
  });
  if (!picker.present) return "no-picker";
  if (picker.offers.length === 0) {
    if (picker.cancel) await page.mouse.click(picker.cancel.x, picker.cancel.y);
    return `no-candidate (${picker.text})`;
  }
  await page.evaluate((entity) => {
    const card = window.__deepAll("open-house-room-settings .card").find((c) => {
      const words = window.__deepText(c).replace(/\s+/g, " ").trim();
      return words.startsWith("Bind the") || words.startsWith("Replace the");
    });
    const selector = window.__deepAll("ha-selector", card)[0];
    selector.dispatchEvent(
      new CustomEvent("value-changed", {
        detail: { value: entity },
        bubbles: true,
        composed: true,
      }),
    );
  }, picker.offers[0]);
  await sleep(2200);
  return "bound";
}

/** The Modules card of the open room: one row per installed module.
 *
 * One card per module, found by the pack it names (`data-pack`) rather than by
 * a card headed "Modules": that heading is a section, and the cards under it
 * each name their pack, which is also how a person's click is aimed at one
 * module rather than at a position in a list.
 *
 * **Read off the card itself, because the card has no table.** This used to
 * look for `tbody tr` inside each card and for the module's list of behaviours
 * in `.chip`s. Neither exists any more: a module card is a heading, a button or
 * two, a `<details>` whose summary counts the behaviours, and one `label.toggle`
 * per behaviour. The old reader therefore matched nothing and returned an empty
 * list for a room that held a module -- which did not fail the walk, it made it
 * *skip* the whole "turn the module on and watch it change" half of step 4
 * without a word, because an empty list reads the same as "the room already had
 * none". A walk that silently drops its most important assertion is worse than
 * one that fails it.
 *
 * Only the room's own cards count. A module placed in the whole house is drawn
 * on this page too, so that a person can see it reaches here, but it carries no
 * Remove button -- it is not this room's to remove -- and the steps below are
 * about what this room was given. The Remove button is what tells the two
 * apart -- it is offered only for a module this room can remove.
 */
const moduleRows = (page) =>
  page.evaluate(() => {
    const buttons = (card) =>
      [...card.querySelectorAll("button")].map((b) => (b.textContent ?? "").trim());
    const cards = window.__deepAll("open-house-room-settings .card[data-pack]");
    if (cards.length === 0) return null;
    return cards
      .filter((card) => buttons(card).includes("Remove"))
      .map((card) => ({
        pack: card.dataset.pack,
        text: window.__deepText(card.querySelector("h3") ?? card)
          .replace(/\s+/g, " ")
          .trim(),
        actions: buttons(card),
        behaviours: [...card.querySelectorAll("label.toggle")].map((label) => ({
          label: (label.querySelector("span")?.textContent ?? "").trim(),
          on: label.querySelector("input[type=checkbox]")?.checked === true,
        })),
      }));
  });

/**
 * Install a module the room can actually take, through the dialog.
 *
 * The dialog names each offer's pack in `data-pack`, so the choice is read off
 * the page rather than guessed from a label that i18n can change.
 *
 * The pick is a pack that *declares behaviours*, and that is the point of the
 * step: what this walk is here to prove is that a room can be given something
 * to do and then does it. The dialog lists modules only -- a room template
 * declares what a room *has* and the manifest schema refuses it a behaviour, so
 * it was never a thing a room could be given and is no longer offered (`panel/
 * src/tabs/add-module.ts`) -- but the preference is still written out rather
 * than assumed: a pack that declares no behaviours produces a module row with
 * no way to switch it on and nothing it could ever run, and this step would
 * then be testing a form instead of the product. A pack with behaviours says so
 * in a `<details>` summary, and that is what is read off the page.
 *
 * `wanted` names the pack to prefer, and the step that turns the light on is
 * what needs it. "Any pack with behaviours" was the old rule, and it made the
 * two halves of this walk talk past each other: the module it happened to
 * install was a climate one -- behaviours, yes, but nothing here reacts to
 * somebody walking in -- while the step watching for the room to act waited for
 * a motion-triggered light that no installed module would ever run. A walk that
 * picks by *shape* rather than by *what it is about to claim* is a walk whose
 * expectation is whatever the catalog happened to offer first.
 */
async function installFirstModule(page, wanted = null) {
  await click(page, "open-house-room-settings button", { nth: "Add module to room" });
  await sleep(1800);
  const choice = await page.evaluate((preferred) => {
    const cards = window.__deepAll("open-house-dialog .card[data-pack]");
    const installable = (card) =>
      [...card.querySelectorAll("button")].some(
        (b) => (b.textContent ?? "").trim() === "Install" && !b.disabled,
      );
    const hasBehaviours = (card) =>
      [...card.querySelectorAll("summary")].some((s) =>
        /\bbehaviours?\b/i.test(s.textContent ?? ""),
      );
    const pick =
      (preferred === null
        ? undefined
        : cards.find((card) => card.dataset.pack === preferred && installable(card))) ??
      cards.find((card) => installable(card) && hasBehaviours(card)) ??
      cards.find(installable);
    return {
      pack: pick?.dataset.pack ?? null,
      hasBehaviours: pick ? hasBehaviours(pick) : false,
      offers: cards.length,
    };
  }, wanted);
  if (!choice.pack) {
    await click(page, "open-house-dialog button", { nth: "Close" }).catch(() => {});
    await page.keyboard.press("Escape");
    await sleep(600);
    return { installed: null, offers: choice.offers, hasBehaviours: false };
  }
  // Through Playwright's own locator rather than a mouse coordinate. The
  // locator pierces shadow roots, scrolls the button into view, waits for it
  // to settle, and -- the part that matters -- fails loudly when something
  // intercepts the click, naming the element that did. Driving this by
  // coordinate is what let a dialog whose content sat under its own backdrop
  // report a successful install it had never performed.
  await page
    .locator(`open-house-dialog .card[data-pack="${choice.pack}"] button`)
    .first()
    .click();
  await sleep(2500);
  return {
    installed: choice.pack,
    offers: choice.offers,
    hasBehaviours: choice.hasBehaviours,
  };
}

const { browser, page, events } = await openPanel();
try {
  // -- step 1: the rooms ---------------------------------------------------
  log("\n== step 1: rooms");
  if (!(await toRoomList(page))) throw new Error("the Rooms tab never rendered a table");
  let present = await roomRows(page);
  log(`  the house starts with ${present.length} room(s)`);
  for (const room of ROOMS) {
    if (present.some((row) => row.startsWith(`${room.name} `))) {
      log(`  have ${room.name}`);
      continue;
    }
    const outcome = await createRoom(page, room);
    log(`  created ${room.name} (${room.type})${outcome === true ? "" : ` -- refused: ${outcome}`}`);
    if (outcome !== true) fail(`creating ${room.name}: ${outcome}`);
    present = await roomRows(page);
    if (!present.some((row) => row.startsWith(`${room.name} `))) {
      fail(`${room.name} is not in the list after being created`);
    }
  }
  present = await roomRows(page);
  log(`  the house now has ${present.length} room(s):`);
  for (const row of present) log(`    ${row}`);
  if (present.length < 5) fail(`only ${present.length} rooms; the walk needs at least 5`);

  // -- step 2: bind every slot --------------------------------------------
  log("\n== step 2: bind every slot of every room");
  const summary = [];
  for (const room of ROOMS) {
    const names = await roomRows(page);
    if (!names.some((row) => row.startsWith(`${room.name} `))) continue;
    if (!(await openRoom(page, room.name))) {
      fail(`${room.name}: clicking its name did not open its settings`);
      continue;
    }

    for (let pass = 0; pass < 3; pass += 1) {
      const rows = await slotRows(page);
      const unbound = rows.filter((row) => row.actions.includes("Bind"));
      if (unbound.length === 0) break;
      for (const row of unbound) {
        const outcome = await bindSlot(page, row.label);
        log(`    ${room.name} / ${row.label}: ${outcome}`);
        if (outcome !== "bound" && !outcome.startsWith("no-candidate")) {
          fail(`${room.name} / ${row.label}: ${outcome}`);
        }
      }
    }

    const final = await slotRows(page);
    const bound = final.filter((row) => !row.actions.includes("Bind")).length;
    summary.push({ room: room.name, bound, total: final.length });
    log(`  ${room.name}: ${bound}/${final.length} bound`);
    await leaveRoom(page);
  }

  const totalBound = summary.reduce((sum, entry) => sum + entry.bound, 0);
  const totalSlots = summary.reduce((sum, entry) => sum + entry.total, 0);
  log(`\n  bound ${totalBound} of ${totalSlots} slots across ${summary.length} rooms`);
  if (totalBound < 25) fail(`only ${totalBound} entities bound; the walk needs at least 25`);

  // -- step 3: the house, as the panel reports it --------------------------
  log("\n== step 3: the room list after setup");
  if (!(await toRoomList(page))) fail("could not get back to the room list");
  for (const row of await roomRows(page)) log(`    ${row}`);

  // -- step 4: a module, installed and turned on through the panel ---------
  //
  // Binding says which device a slot means; a module says what the room does
  // with it. Both halves are needed before anything in this house can act, and
  // this is the half no earlier step touched.
  log("\n== step 4: install a module and turn it on");
  // The room step 7 walks into, and the module that will make it act. The two
  // are one story -- a module that turns a light on when somebody walks in is
  // installed in the living room, somebody walks in, the light comes on, and the
  // panel says why -- so the module is installed *here*, in the room that is
  // watched, and not in a room chosen for whatever the dialog happened to offer.
  //
  // The pack is named for the same reason, and by its behaviour rather than by
  // its shape. `example_pack` is the catalog's own "Motion turns the light on":
  // it triggers on `motion_sensor`, conditions on `ambient_light_sensor` being
  // below the threshold, and acts on `light_group` -- the three slots step 7
  // binds and reads, and the same three the room's page lists. A pick by shape
  // instead took whichever offer the catalog happened to put first (a `climate`
  // pack, which declares behaviours and acts on no light), and the walk then
  // failed a claim it had never arranged to be true.
  //
  // `lighting` is deliberately *not* named. It is the obvious pack for the
  // story and the wrong one for the walk: the house already holds it, so the
  // dialog draws it with an "installed" chip and no button, and a walk that
  // asked for it would be asking the panel to install a pack it holds.
  const MODULE_ROOM = ACT_ROOM;
  const MODULE_PACK = "example_pack";
  if (!(await openRoom(page, MODULE_ROOM))) {
    fail(`${MODULE_ROOM}: could not be opened to install a module`);
  } else {
    // Start clean, through the panel's own Remove button.
    //
    // This walk is run against a house that keeps what it is given: the
    // installed set is stored and survives a restart, so a second run finds the
    // module the first one installed, skips the install, and ends up testing a
    // record it did not make -- which is how a stale module, written before a
    // fix, can keep a run failing after the fix is in. Removing first is what a
    // person would do, it exercises the other half of the module verbs, and it
    // makes the step mean the same thing on every run.
    for (let attempt = 0; attempt < 6; attempt += 1) {
      const held = await moduleRows(page);
      if (!held || held.length === 0) break;
      await click(page, "open-house-room-settings .card[data-pack] button", {
        nth: "Remove",
      });
      await sleep(1800);
    }
    let rows = await moduleRows(page);
    if (rows === null) fail("the room settings page has no Modules card");
    if (rows && rows.length === 0) {
      const offer = await installFirstModule(page, MODULE_PACK);
      log(`  the dialog offered ${offer.offers} module(s)`);
      if (!offer.installed) fail(`nothing could be installed in the ${MODULE_ROOM}`);
      else
        log(
          `  installed ${offer.installed}` +
            (offer.hasBehaviours ? " (it declares behaviours)" : " (it declares none)"),
        );
      // Wait for the Modules card to catch up with the install rather than
      // reading it on a fixed delay: the panel reloads the room after an
      // install, so a read taken too early sees the room as it was and reports
      // a module that did install as one that did not.
      for (let attempt = 0; attempt < 12; attempt += 1) {
        rows = await moduleRows(page);
        if (rows && rows.length > 0) break;
        await sleep(1000);
      }
    } else if (rows) {
      log(`  the ${MODULE_ROOM} already had ${rows.length} module(s)`);
    }
    if (rows && rows.length > 0) {
      const row = rows[0];
      log(`  ${row.text}`);
      log(`  behaviours: ${row.behaviours.map((b) => `${b.label}${b.on ? " (on)" : " (off)"}`).join(", ") || "(none declared)"}`);
      if (!row.actions.includes("Enable") && !row.actions.includes("Disable")) {
        fail(`the module row offers neither Enable nor Disable: ${row.actions.join(", ")}`);
      } else {
        const wasEnabled = row.actions.includes("Disable");
        // A pack installs disabled -- "installation is not activation" -- so a
        // module that arrives already enabled is a defect, not a convenience.
        if (!wasEnabled) {
          await click(page, "open-house-room-settings .card[data-pack] button", {
            nth: "Enable",
          });
          await sleep(2200);
          const after = (await moduleRows(page))[0];
          log(`  after Enable: ${after.text}`);
          if (!after.actions.includes("Disable")) {
            fail("turning the module on did not change the row");
          }
          const onBehaviours = after.behaviours.filter((b) => b.on).length;
          log(`  ${onBehaviours} of ${after.behaviours.length} behaviours now on`);
          if (after.behaviours.length === 0) {
            fail(
              "the module turned on but declares no behaviours, so nothing in the room can ever act",
            );
          } else if (onBehaviours === 0) {
            fail("the module is on but none of its behaviours are");
          }
        } else {
          log("  the module was already on; leaving it on");
        }
      }
    }
    await leaveRoom(page);
  }

  // -- step 5: a profile, activated for the house --------------------------
  log("\n== step 5: profiles");
  await click(page, "#tab-profiles");
  await sleep(2200);
  const profilePage = await page.evaluate(() => ({
    here: window.__deepAll("open-house-tab-profiles").length > 0,
    error: window.__deepAll("open-house-tab-profiles .banner.error").length > 0,
    headings: window.__deepAll("open-house-tab-profiles h2").map((h) => (h.textContent ?? "").trim()),
    activate: window.__deepAll("open-house-tab-profiles button").filter(
      (b) => (b.textContent ?? "").trim().startsWith("Activate"),
    ).length,
    active: window.__deepAll("open-house-tab-profiles .chip.ok").map((c) => (c.textContent ?? "").trim()),
    // Which profile reads as in force *before* the click, by name. Read because
    // the claim below is that the click *moved* the house, and "the profile I
    // clicked is in force afterwards" is also true of a click that did nothing
    // on a house that was already on it -- see the check.
    force: window
      .__deepAll("open-house-tab-profiles .card")
      .filter((card) => card.querySelector("h3") && !card.querySelector(".card"))
      .filter((card) =>
        [...card.querySelectorAll(".chip")].some((chip) => /in force/i.test(chip.textContent ?? "")),
      )
      .map((card) => (card.querySelector("h3")?.textContent ?? "").trim()),
    selects: window.__deepAll("open-house-tab-profiles select").length,
  }));
  log(`  sections: ${profilePage.headings.join(", ") || "(none)"}`);
  log(`  activate buttons: ${profilePage.activate}; per-room axes: ${profilePage.selects}; active: ${profilePage.active.join(", ") || "(none)"}`);
  log(`  in force before the click: ${profilePage.force.join(", ") || "(nothing)"}`);
  if (!profilePage.here) fail("the Profiles tab did not render");
  if (profilePage.error) fail("the Profiles tab answered with an error");
  if (profilePage.activate > 0) {
    // *Which* profile the walk is putting in force, read off the card the button
    // sits in. "Something is marked in force afterwards" is not the claim and
    // would not test one: this house may already be on a profile, so a chip that
    // was there before the click passes a check about the click. What is asked
    // is that this profile -- and not the one before it -- is the one in force,
    // which is also the only reading that can fail.
    const chosen = await page.evaluate(() => {
      const button = window
        .__deepAll("open-house-tab-profiles button")
        .find((b) => (b.textContent ?? "").trim() === "Activate for the house");
      if (!button) return null;
      const card = button.closest(".card");
      return (card?.querySelector("h3")?.textContent ?? "").trim() || null;
    });
    await click(page, "open-house-tab-profiles button", { nth: "Activate for the house" });
    // Wait for the *panel*, not for a number of milliseconds.
    //
    // A profile taken from a house carries that house, so putting the house on
    // one is a whole-house restore -- every room's bindings written through its
    // configuration subentry and every hosted module built again -- and it is
    // slow: measured at 6-10 s on this 15-room house. The screen knows, because
    // every button on it is disabled for the duration, so the idle flag is what
    // is waited on. A fixed 2500 ms was shorter than the restore and read the
    // card list from *before* the click, which is how this step came to report
    // "activating Test did not put it in force" over a house that had already
    // been put on Test.
    await page
      .waitForFunction(
        () => window.__deepAll("open-house-tab-profiles")[0]?.busy === false,
        { timeout: 120000, polling: 500 },
      )
      .catch(() => fail("the Profiles tab never finished activating a profile"));
    await sleep(500);
    // What the *server* says, read through the page that just asked it.
    //
    // A card and the house disagreeing is the whole of this step's failure, and
    // which of the two is wrong is not something the screen can say: the panel
    // may not have redrawn, or the write may not have landed. So both are read
    // and both are printed, and the failure line carries the evidence rather
    // than an accusation. The screen's own two fields come with it -- a tab that
    // is still busy is a tab that has not read the answer yet, and one holding
    // an error is a tab that was told no.
    const house = await page.evaluate(async () => {
      const tab = window.__deepAll("open-house-tab-profiles")[0];
      const panel = window.__deepAll("open-house-panel")[0];
      const answer = await panel.hass.callWS({ type: "open_house/profiles/list" });
      return {
        busy: tab?.busy,
        refused: tab?.error === null || tab?.error === undefined ? null : tab.error,
        list: (answer.profiles ?? [])
          .filter((profile) => profile.kind === "house")
          .map((profile) => `${profile.name}${profile.active ? " (in force)" : ""}`),
      };
    });
    log(`  the house says: ${house.list.join(", ") || "(none)"} [busy: ${house.busy}, refused: ${house.refused === null ? "no" : JSON.stringify(house.refused)}]`);
    const after = await page.evaluate(() =>
      window
        .__deepAll("open-house-tab-profiles .card")
        // The innermost cards only. A card that *contains* cards is the
        // section around them ("House profiles"), and `querySelector("h3")`
        // descends, so it answers with whichever profile it holds first -- the
        // section counted as a profile, and the same name read twice. A
        // profile card holds no other card, which is what tells them apart.
        .filter((card) => card.querySelector("h3") && !card.querySelector(".card"))
        .map((card) => ({
          name: (card.querySelector("h3")?.textContent ?? "").trim(),
          // The panel's word for a profile that is the one in force; the chip is
          // how a person reads it, so the chip is what is asserted.
          inForce: [...card.querySelectorAll(".chip")].some((chip) =>
            /in force/i.test(chip.textContent ?? ""),
          ),
        })),
    );
    const inForce = after.filter((row) => row.inForce).map((row) => row.name);
    log(`  after activating: ${inForce.join(", ") || "(nothing reads as in force)"}`);
    if (chosen === null) {
      fail("could not tell which house profile that button belongs to");
    } else if (inForce.length !== 1 || inForce[0] !== chosen) {
      // **Exactly the one that was clicked, and nothing else.** Asking only
      // "is the clicked profile in force" cannot fail when the click did
      // nothing and the card list is reading a house that was already on it --
      // and a card left saying the *old* profile is still in force is the
      // screen disagreeing with the house, which is the whole of this step's
      // failure. One chip, naming the profile the button was under.
      fail(
        `activating ${chosen} left ${inForce.join(", ") || "nothing"} in force` +
          ` (was: ${profilePage.force.join(", ") || "nothing"})`,
      );
    }
  }

  // -- step 6: every screen renders ----------------------------------------
  //
  // Every tab there is, and the walk has been through four of them as a side
  // effect. This visits them all in the order the sidebar lists them and reports
  // what each one answered, because "the tab throws" is exactly the sort of break
  // a walk that only exercises rooms would never see. The list is the sidebar's
  // (`panel/src/tabs/types.ts`), so a tab added or taken away there belongs here
  // too -- the Modules tab was removed, and a walk still clicking `#tab-modules`
  // would report a tab that no longer exists as one that renders nothing.
  log("\n== step 6: every tab");
  const TABS = ["overview", "rooms", "house", "profiles", "activity", "health", "store", "dev"];
  for (const tab of TABS) {
    await click(page, `#tab-${tab}`);
    await sleep(1800);
    const state = await page.evaluate((id) => {
      const root = window.__deepAll(`open-house-tab-${id}`)[0];
      if (!root) return { rendered: false };
      return {
        rendered: true,
        error: window.__deepText(
          window.__deepAll(`open-house-tab-${id} .banner.error`)[0] ?? document.createElement("i"),
        ).trim(),
        heading: (window.__deepAll(`open-house-tab-${id} h1`)[0]?.textContent ?? "").trim(),
        rows: window.__deepAll(`open-house-tab-${id} tbody tr`).length,
        empty: window.__deepAll(`open-house-tab-${id} .empty`).length,
      };
    }, tab);
    if (!state.rendered) fail(`the ${tab} tab rendered nothing`);
    else if (state.error) fail(`the ${tab} tab: ${state.error}`);
    else log(`  ${tab}: "${state.heading}" ${state.rows} row(s)${state.empty ? ", empty state shown" : ""}`);
  }

  // -- step 7: the house acts ----------------------------------------------
  //
  // Everything so far has been configuration: rooms made, slots bound, a module
  // turned on. None of it is the product. The product is that a room notices
  // somebody walk in and the light comes on, and this is the step that asks for
  // exactly that -- with the observation made in the panel, where a person would
  // make it, and Home Assistant's own state read alongside as corroboration.
  log("\n== step 7: somebody walks in, and the house acts");
  const slug = ACT_ROOM.toLowerCase().replace(/[^a-z0-9]+/g, "_");
  let watched = null;
  if (!(await toRoomList(page))) {
    fail("could not get back to the room list");
  } else if (!(await openRoom(page, ACT_ROOM))) {
    fail(`${ACT_ROOM}: could not be opened to read its light`);
  } else {
    const bound = await slotRows(page);
    const light = bound.find((row) => row.label === "Light group");
    const motion = bound.find((row) => row.label === "Motion sensor");
    // The light the room is actually bound to, read off the row the panel
    // renders -- not assumed from the fixture's naming. If the two disagree,
    // the walk should watch what the room watches.
    watched = (light?.text.match(/\blight\.[a-z0-9_]+/) ?? [])[0] ?? null;
    log(`  the ${ACT_ROOM} shows ${bound.length} slot(s): ${bound.map((r) => r.label).join(", ")}`);
    log(`  the ${ACT_ROOM}'s light is ${watched ?? "(nothing bound)"}`);
    if (motion === undefined) fail(`the ${ACT_ROOM} has no motion sensor bound`);
    if (watched === null) fail(`the ${ACT_ROOM} has no light bound`);
    await leaveRoom(page);
  }

  if (watched !== null) {
    // The room starts dark and empty: a low lux reading is what "dark" means to
    // the engine when a lux sensor is bound, and relying on the sun's actual
    // elevation would make this step pass or fail by the hour.
    world("set", "lux", slug, "5");
    world("set", "motion", slug, "off");
    world("set", "light", slug, "off");
    // Waiting, not fighting. After motion stops the engine holds the light for
    // its quiet timeout and turns it back on if it goes off, so publishing
    // "off" harder only proves the hold works. The way to an empty room is to
    // let the engine finish -- the same thing a person does, which is nothing.
    let settled = await haState(watched);
    for (let waited = 0; settled !== "off" && waited < 360; waited += 15) {
      if (waited === 0) {
        log(`  the room is held from earlier motion (${watched} = ${settled}); letting it go quiet`);
      }
      await sleep(15000);
      settled = await haState(watched);
      if (settled === "off") log(`  quiet after about ${waited + 15}s`);
    }
    log(`  before: Home Assistant holds ${watched} = ${settled}`);
    if (settled !== "off") {
      fail(`the ${ACT_ROOM} did not settle to off; it reads ${settled}`);
    } else {
      await click(page, "#tab-activity");
      await sleep(2000);
      const known = await activityRows(page);

      log("  the world: somebody walks into the room");
      world("set", "motion", slug, "on");

      let seen = null;
      for (let attempt = 0; attempt < 12; attempt += 1) {
        await sleep(5000);
        await click(page, "open-house-tab-activity button", { nth: "Refresh" });
        await sleep(600);
        const row = await activityAction(page, known);
        const now = await haState(watched);
        log(`    t+${(attempt + 1) * 5}s  panel: ${row ? "recorded" : "nothing yet"}  Home Assistant: ${now}`);
        if (row && seen === null) {
          // The entity a decision was about is not in the collapsed row. That
          // row names the behaviour, the action and the outcome, and the entity
          // sits one click away behind "Why" -- the click a person makes to ask
          // exactly that question. So the walk makes it, rather than matching on
          // an entity id the collapsed row never shows.
          const about = await activityEntity(page);
          log(`  the panel recorded: ${row}`);
          log(`    and it was about: ${about ?? "(no entity in the detail)"}`);
          if (about === watched) seen = row;
        }
        if (seen !== null && now === "on") break;
      }

      const final = await haState(watched);
      if (seen) {
        log(`  the panel recorded: ${seen}`);
      } else {
        fail("the Activity tab never showed the house acting on that light");
      }
      if (final !== "on") {
        fail(`the light did not come on; it reads ${final}`);
      } else {
        log(`  the ${ACT_ROOM}'s light is on, and the panel says why`);
      }
      log(`  (the device's own topic still reads: ${world("read", `light.${slug}`)} -- Open House writes Home Assistant's state, so the panel and Home Assistant agree while the physical device was never commanded)`);
    }
  }

  log(`\n== events\n${events.slice(-8).map((e) => `    ${e}`).join("\n") || "    (none)"}`);
  log(failures === 0 ? "\nE2E: PASS" : `\nE2E: ${failures} FAILURE(S)`);
} catch (error) {
  // A walk that dies says what it died of *and* what the page recorded on the
  // way, because the two together are the finding: the error names the step and
  // the console names the reason. Thrown away, a crash here reads as a broken
  // page and reads as nothing at all about why.
  log(`\n== the walk stopped: ${error}`);
  log(`\n== events\n${events.slice(-12).map((e) => `    ${e}`).join("\n") || "    (none)"}`);
  failures += 1;
} finally {
  await page.screenshot({ path: "e2e-house.png", fullPage: true }).catch(() => {});
  await browser.close();
}
process.exitCode = failures === 0 ? 0 : 1;
