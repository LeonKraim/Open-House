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
      // panel's word, so this is the word to match.
      if (!/\bapplied\b/.test(text)) continue;
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
    await sleep(900);
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
  for (let attempt = 0; attempt < 24; attempt += 1) {
    await sleep(500);
    const settled = await page.evaluate(() => {
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
    if (settled) break;
  }
  return page.evaluate(() => window.__deepAll("open-house-room-settings").length > 0);
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

  const picker = await page.evaluate(() => {
    const group = window.__deepAll('open-house-room-settings [role="group"]')[0];
    if (!group) return { present: false };
    const uses = [...group.querySelectorAll("button")].filter(
      (b) => (b.textContent ?? "").trim() === "Use this",
    );
    return {
      present: true,
      uses: uses.length,
      text: window.__deepText(group).replace(/\s+/g, " ").trim().slice(0, 160),
    };
  });
  if (!picker.present) return "no-picker";
  if (picker.uses === 0) {
    await click(page, 'open-house-room-settings [role="group"] button', { nth: "Cancel" });
    return `no-candidate (${picker.text})`;
  }
  await click(page, 'open-house-room-settings [role="group"] button', { nth: "Use this" });
  await sleep(2200);
  return "bound";
}

/** The Modules card of the open room: one row per installed module. */
const moduleRows = (page) =>
  page.evaluate(() => {
    const card = window
      .__deepAll("open-house-room-settings .card")
      .find((c) => (c.querySelector("h2")?.textContent ?? "").trim() === "Modules");
    if (!card) return null;
    return [...card.querySelectorAll("tbody tr")].map((row) => ({
      text: window.__deepText(row).replace(/\s+/g, " ").trim(),
      actions: [...row.querySelectorAll("button")].map((b) =>
        (b.textContent ?? "").trim(),
      ),
      behaviours: [...row.querySelectorAll(".chip")].map((chip) => ({
        label: (chip.textContent ?? "").trim(),
        on: chip.classList.contains("ok"),
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
 */
async function installFirstModule(page) {
  await click(page, "open-house-room-settings button", { nth: "Add module to room" });
  await sleep(1800);
  const choice = await page.evaluate(() => {
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
      cards.find((card) => installable(card) && hasBehaviours(card)) ??
      cards.find(installable);
    return {
      pack: pick?.dataset.pack ?? null,
      hasBehaviours: pick ? hasBehaviours(pick) : false,
      offers: cards.length,
    };
  });
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
  const MODULE_ROOM = "Kitchen";
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
      await click(page, "open-house-room-settings tbody button", { nth: "Remove" });
      await sleep(1800);
    }
    let rows = await moduleRows(page);
    if (rows === null) fail("the room settings page has no Modules card");
    if (rows && rows.length === 0) {
      const offer = await installFirstModule(page);
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
          await click(page, "open-house-room-settings tbody button", { nth: "Enable" });
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
    selects: window.__deepAll("open-house-tab-profiles select").length,
  }));
  log(`  sections: ${profilePage.headings.join(", ") || "(none)"}`);
  log(`  activate buttons: ${profilePage.activate}; per-room axes: ${profilePage.selects}; active: ${profilePage.active.join(", ") || "(none)"}`);
  if (!profilePage.here) fail("the Profiles tab did not render");
  if (profilePage.error) fail("the Profiles tab answered with an error");
  if (profilePage.activate > 0) {
    await click(page, "open-house-tab-profiles button", { nth: "Activate for the house" });
    await sleep(2500);
    const after = await page.evaluate(() =>
      window.__deepAll("open-house-tab-profiles .chip.ok").map((c) => (c.textContent ?? "").trim()),
    );
    log(`  after activating: ${after.join(", ") || "(nothing reads as active)"}`);
    if (!after.some((label) => label === "active")) {
      fail("activating a house profile did not mark anything active");
    }
  }

  // -- step 6: every screen renders ----------------------------------------
  //
  // Eight tabs, and the walk has been through four of them as a side effect.
  // This visits all eight in the order the sidebar lists them and reports what
  // each one answered, because "the tab throws" is exactly the sort of break a
  // walk that only exercises rooms would never see.
  log("\n== step 6: every tab");
  const TABS = ["overview", "rooms", "modules", "profiles", "store", "activity", "health", "import-export"];
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
  const ACT_ROOM = "Living Room";
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
} finally {
  await page.screenshot({ path: "e2e-house.png", fullPage: true }).catch(() => {});
  await browser.close();
}
process.exitCode = failures === 0 ? 0 : 1;
