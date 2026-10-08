// Scratch: splitting a slot into parts, driven by real clicks on a live house.
//
// The claim under test is demand six -- a role can be divided, each half is bound
// to its own device, and the halves are still *one role with one name*. What the
// adapter and session tests prove is that the record, the settings and the engine
// agree; what they cannot prove is that a person can reach any of it: that "Add a
// part" is on the row, that the half it makes can be given a device, that
// renaming it carries the device with it, that a bound half cannot be deleted out
// from under its binding, and that the page says all of it.
//
// Run: node scripts/_parts.mjs
//
// **One thing is driven without a real click, and it says so.** Choosing an
// entity in Home Assistant's own selector: its search box and its list are HA's
// component, and driving those is a test of that component rather than of this
// panel -- the same disclosure `_roomwide.mjs` makes. What is dispatched is
// exactly the `value-changed` event the selector emits when a person picks
// something, and everything after it (the write, the row, the device, the status)
// is read back off the server's own answer.
//
// Every page-side function spells its selectors out in full. A closure over a
// Node-side constant is not there in the browser: `SCREEN` written inside a
// `page.evaluate` is a ReferenceError that reads like a broken page.
import { mkdirSync } from 'node:fs'
import { openPanel, all, click, sleep, type } from './_ui.mjs'

// The room the parts are made in, and the role: the Kitchen carries a light of its
// own on `light_group`, so the parts have something to be told apart from.
const ROOM = 'Kitchen'
const SLOT = 'light_group'
const SHOTS = `${process.env.TEMP ?? '/tmp'}/open-house-shots`

const FAILURES = []
const ok = (label, pass, detail = '') => {
  console.log(`${pass ? 'ok  ' : 'FAIL'} ${label}${detail ? ` -- ${detail}` : ''}`)
  if (!pass) FAILURES.push(label)
}
const skip = (label, why) => console.log(`SKIP  ${label} -- ${why}`)

const { browser, page, events } = await openPanel()
const readAll = (fn, arg = null) => page.evaluate(fn, arg)
const waitFor = (fn, arg = null, ms = 90000) =>
  page.waitForFunction(fn, arg, { timeout: ms, polling: 500 })

mkdirSync(SHOTS, { recursive: true })
const shot = (name) => page.screenshot({ path: `${SHOTS}/parts-${name}.png` })

/** One `open_house/*` command over the panel's own websocket connection. */
const ws = (message) =>
  readAll((msg) => {
    const panel = window.__deepAll('open-house-panel')[0]
    return panel.hass.callWS(msg)
  }, message)

/**
 * Click a button inside the nearest box an anchor element sits in.
 *
 * The anchor is what a person *sees* -- the button that opens the draft, the box
 * they are typing in -- and the box the button belongs to is found by climbing
 * from the anchor outwards until one holds a button with that name. Climbing
 * from the anchor's *parent* instead picks up the wrong box one level up: a part
 * row is itself the anchor, so its parent is the block holding every part, and
 * the first "Delete" in there belongs to another half -- which deletes that half
 * and reads, on the page, as a delete that did nothing. Aimed by coordinates and
 * hit-tested, so a control that renders behind something is reported rather than
 * silently missed.
 */
const press = (anchorSel, label) =>
  readAll(
    ([sel, text]) => {
      const anchor = window.__deepAll(sel)[0]
      if (!anchor) return { error: `no anchor for ${sel}` }
      // `!button` rather than `button === null`: `find` answers `undefined` when
      // nothing matches, and comparing against `null` would stop the climb after
      // the anchor's own (empty) subtree and report a button that is one step up.
      let button = null
      for (let box = anchor; box !== null && !button; box = box.parentElement) {
        button = window.__deepAll('button', box).find(
          (b) => (b.textContent ?? '').trim() === text,
        )
      }
      if (!button) return { error: `no button ${text}` }
      button.scrollIntoView({ block: 'center' })
      const r = button.getBoundingClientRect()
      const x = r.x + r.width / 2
      const y = r.y + r.height / 2
      const hit = document.elementFromPoint(x, y)
      const holds = (outer, node) => {
        for (let n = node; n; ) {
          if (n === outer) return true
          n = n.parentNode || n.host || null
        }
        return false
      }
      return {
        x,
        y,
        reaches: !!hit && (holds(button, hit) || holds(hit, button)),
        hit: hit ? hit.tagName.toLowerCase() : 'nothing',
      }
    },
    [anchorSel, label],
  )

const pressOrFail = async (anchorSel, label, { writes = false } = {}) => {
  const at = await press(anchorSel, label)
  if (at.error) throw new Error(`${at.error} (for ${anchorSel})`)
  if (!at.reaches) throw new Error(`${label} is behind ${at.hit}, so no click reaches it`)
  await page.mouse.click(at.x, at.y)
  if (writes) await settle()
  else await sleep(1300)
}

/**
 * Whether either page a part is edited on is still working, or has stopped.
 *
 * Written as a bare function with no closures, because it is handed to
 * `page.waitForFunction` and evaluated in the browser: it may only name things
 * the browser has, which is `window` and nothing from this file. The flag is an
 * *argument* for that reason -- `() => !BUSY()` would need `BUSY` to exist over
 * there, and it does not: it is a ReferenceError that reads like a broken page.
 */
const BUSY = (wanted) => {
  const room = window.__deepAll('open-house-room-settings')[0]
  const house = window.__deepAll('open-house-tab-house')[0]
  const busy = Boolean(room?.busy) || Boolean(house?.busy)
  return wanted ? busy : !busy
}

/**
 * Wait for the page to finish what the click set off, however long that takes.
 *
 * A part write is a room-subentry write, and a subentry write makes Home
 * Assistant reload the config entry -- so the page that wrote is, for some
 * seconds, reading a house that is not there. A fixed sleep is the wrong shape
 * for that: too short and it reads the page before the write is drawn, which
 * reads exactly like a page that refused the write. So this waits on the page's
 * own `busy` flag, which is the page saying it has stopped working.
 *
 * An error the page settles *into* is left on screen rather than waited away:
 * a refusal is a finished state, and whether it was right is the next check's
 * question, not this one's.
 */
const settle = async (ms = 60000) => {
  try {
    await waitFor(BUSY, true, 3000)
  } catch {
    // A write can finish before the first look; that is not a failure.
  }
  await waitFor(BUSY, false, ms)
  await sleep(500)
}

/** Every part row the page is drawing under one role, with its own words. */
const parts = (key) =>
  readAll((wanted) => {
    return window
      .__deepAll('[data-slot-part-row]')
      .filter((row) => (row.getAttribute('data-slot-part-row') ?? '').startsWith(`${wanted}__`))
      .map((row) => ({
        key: row.getAttribute('data-slot-part-row'),
        words: window.__deepText(row).replace(/\s+/g, ' ').trim(),
      }))
  }, key)

/** The refusal the page is showing, if it is showing one. */
const refusal = () =>
  readAll(() => {
    const banner = window.__deepAll('.banner.error')[0]
    return banner ? window.__deepText(banner).replace(/\s+/g, ' ').trim() : null
  })

/** What the open picker is holding: whether HA's selector came up, and its list. */
const picker = () =>
  readAll(() => {
    const settings = window.__deepAll('open-house-room-settings')[0]
    return {
      state: settings?.picker ?? null,
      selectors: window.__deepAll('open-house-room-settings ha-selector').length,
    }
  })

/**
 * Why the page has not moved, in the page's own words.
 *
 * A write that did not land leaves one of three traces and they need opposite
 * fixes, so the walk prints which rather than letting a stale DOM be read as a
 * product bug: the banner (`not_found`/`invalid_format` from the write itself),
 * `stale` (the house moved profile under the page, which is answered by
 * reloading rather than by asking again), or neither -- a page still busy, or
 * still waiting out a reload, which is a *timing* answer and not a refusal.
 * The revision the page is holding against the house's own is the number that
 * decides between them.
 */
const blame = () =>
  readAll(() => {
    const panel = window.__deepAll('open-house-panel')[0]
    const settings = window.__deepAll('open-house-room-settings')[0]
    const banner = window.__deepAll('.banner.error')[0]
    const held = settings?.revision ?? null
    const roomId = settings?.roomId ?? null
    return panel.hass
      .callWS({ type: 'open_house/rooms/get', room_id: roomId })
      .then(
        (detail) => ({
          error: banner ? window.__deepText(banner).replace(/\s+/g, ' ').trim() : null,
          stale: settings?.stale ?? null,
          busy: settings?.busy ?? null,
          held,
          house: detail.revision,
          roomId,
        }),
        (failure) => ({
          error: banner ? window.__deepText(banner).replace(/\s+/g, ' ').trim() : null,
          stale: settings?.stale ?? null,
          busy: settings?.busy ?? null,
          held,
          house: String(failure?.message ?? failure),
          roomId,
        }),
      )
  })

/** Open a room's settings page from the room list, and wait for it to be drawn. */
const openRoom = async (name) => {
  await click(page, '#tab-rooms', { wait: 1500 })
  if ((await all(page, 'open-house-room-settings')).length > 0) {
    await click(page, 'open-house-room-settings button', { nth: 'Back', wait: 2000 })
  }
  await waitFor((wanted) => {
    const row = window
      .__deepAll('open-house-tab-rooms tbody tr')
      .find((r) => window.__deepText(r).trim().startsWith(wanted))
    const link = row?.querySelector('a')
    if (!link) return false
    link.scrollIntoView({ block: 'center' })
    const box = link.getBoundingClientRect()
    window.__roomAt = { x: box.x + box.width / 2, y: box.y + box.height / 2 }
    return true
  }, name)
  const at = await readAll(() => window.__roomAt)
  await page.mouse.click(at.x, at.y)
  await waitFor(() => {
    const settings = window.__deepAll('open-house-room-settings')[0]
    if (!settings) return false
    if (window.__deepText(settings).includes('Reading the room')) return false
    return window
      .__deepAll('open-house-room-settings h2')
      .some((h) => (h.textContent ?? '').trim() === 'Devices')
  })
}

/**
 * Pick an entity in the open picker the way the selector does it (see the header).
 *
 * The *shortest* card naming Cancel, because the picker is drawn inside the
 * section's own card and the outer one's text is the whole page.
 */
const chooseIn = async (entity) => {
  await waitFor(() => {
    const settings = window.__deepAll('open-house-room-settings')[0]
    return settings?.picker && settings.picker.loading === false
  })
  const list = await picker()
  await readAll(
    (id) => {
      const cards = window
        .__deepAll('open-house-room-settings .card')
        .filter((c) => window.__deepText(c).includes('Cancel'))
        .sort((a, b) => window.__deepText(a).length - window.__deepText(b).length)
      const selector = window.__deepAll('ha-selector', cards[0])[0]
      selector.dispatchEvent(
        new CustomEvent('value-changed', {
          detail: { value: id },
          bubbles: true,
          composed: true,
        }),
      )
    },
    entity,
  )
  await sleep(2800)
  return list
}

const ADD = `[data-add-part="${SLOT}"]`
const ADD_FIELD = "input[aria-label=\"The new part's name\"]"
const partRow = (name) => `[data-slot-part-row="${SLOT}__${name}"]`

try {
  await openRoom(ROOM)

  // -- The role, and the control that splits it -----------------------------
  const anchors = await readAll(() =>
    window.__deepAll('[data-add-part]').map((a) => a.getAttribute('data-add-part')),
  )
  ok(`the ${SLOT} row offers to split it`, anchors.includes(SLOT), JSON.stringify(anchors))
  const before = await parts(SLOT)
  ok('the role starts whole', before.length === 0, JSON.stringify(before))
  await shot('01-whole')

  // -- Split it, and name the first half ------------------------------------
  await click(page, ADD, { wait: 500 })
  await type(page, ADD_FIELD, 'a')
  await shot('02-draft')
  await pressOrFail(ADD_FIELD, 'Add it', { writes: true })
  const added = await parts(SLOT)
  ok(
    'a half is added and drawn under the role',
    added.length === 1 && added[0].key === `${SLOT}__a`,
    JSON.stringify(added),
  )
  ok(
    'and it says nothing is bound to it yet',
    (added[0]?.words ?? '').includes('nothing bound'),
    added[0]?.words,
  )

  // -- Give the half a device ----------------------------------------------
  const lights = await readAll(() => {
    const panel = window.__deepAll('open-house-panel')[0]
    return Object.keys(panel.hass.states).filter((id) => id.startsWith('light.'))
  })
  const house = await ws({ type: 'open_house/house/scope' })
  const kitchen = house.slots.find((row) => row.slot === SLOT)
  const first = lights.find((id) => id !== kitchen?.entity_id) ?? lights[0]
  const second = lights.find((id) => id !== first && id !== kitchen?.entity_id) ?? lights[1]
  if (first === undefined || second === undefined) {
    throw new Error(`only one light in the house: ${JSON.stringify(lights)}`)
  }

  await pressOrFail(partRow('a'), 'Bind')
  const offered = await chooseIn(first)
  ok(
    "the half's own picker lists the house's lights",
    (offered.state?.includes?.length ?? 0) > 0,
    `${offered.state?.includes?.length ?? 0} candidates, ${offered.selectors} selectors drawn`,
  )
  const bound = (await parts(SLOT))[0]
  ok(
    'the half holds the device it was given',
    (bound?.words ?? '').includes(first),
    bound?.words,
  )
  ok(
    'and the role itself is still the role, with its own device',
    (await readAll(
      (id) =>
        window
          .__deepAll('[data-add-part]')
          .filter((a) => a.getAttribute('data-add-part') === id)
          .map((a) => window.__deepText(a.closest('tr')).replace(/\s+/g, ' ').trim())[0] ?? '',
      SLOT,
    )).includes('Light group'),
  )
  await shot('03-bound')

  // -- Rename the half: the device has to come with it ----------------------
  await pressOrFail(partRow('a'), 'Rename')
  await type(page, 'input[aria-label="The new name for the A part"]', 'study')
  await pressOrFail('input[aria-label="The new name for the A part"]', 'Rename it', {
    writes: true,
  })
  const renamed = await parts(SLOT)
  ok(
    'the half is renamed and drawn under the new name',
    renamed.length === 1 && renamed[0].key === `${SLOT}__study`,
    JSON.stringify(renamed.map((p) => p.key)),
  )
  ok(
    'and it kept its device, because a rename moves the binding',
    (renamed[0]?.words ?? '').includes(first),
    renamed[0]?.words,
  )
  await shot('04-renamed')

  // -- A second half, on its own device ------------------------------------
  await click(page, ADD, { wait: 500 })
  await type(page, ADD_FIELD, 'b')
  await pressOrFail(ADD_FIELD, 'Add it', { writes: true })
  await pressOrFail(partRow('b'), 'Bind')
  await chooseIn(second)
  const both = await parts(SLOT)
  const study = both.find((p) => p.key === `${SLOT}__study`)
  const other = both.find((p) => p.key === `${SLOT}__b`)
  ok('the role is now in two halves', both.length === 2, JSON.stringify(both.map((p) => p.key)))
  ok(
    'each half holds its own device',
    study?.words.includes(first) === true && other?.words.includes(second) === true,
    `${study?.words} | ${other?.words}`,
  )
  await shot('05-two-halves')

  // -- A bound half cannot be deleted out from under its binding ------------
  await pressOrFail(partRow('study'), 'Delete', { writes: true })
  const refused = await refusal()
  ok(
    'deleting a bound half is refused, and the refusal says where the device is',
    refused !== null && refused.includes(ROOM),
    refused ?? 'no refusal drawn',
  )
  ok(
    'and the half is still there, still bound',
    (await parts(SLOT)).find((p) => p.key === `${SLOT}__study`)?.words.includes(first) ===
      true,
    JSON.stringify(await parts(SLOT)),
  )
  await shot('06-refused')

  // -- Unbind it, then it can go -------------------------------------------
  await pressOrFail(partRow('study'), 'Unbind', { writes: true })
  const cleared = (await parts(SLOT)).find((p) => p.key === `${SLOT}__study`)
  ok(
    'the half can be unbound, which is the way out of that refusal',
    (cleared?.words ?? '').includes('nothing bound'),
    cleared?.words,
  )
  await shot('07-unbound')

  // -- The House tab draws the same record ---------------------------------
  await click(page, '#tab-house', { wait: 3000 })
  const houseParts = await parts(SLOT)
  ok(
    'the House tab draws the same two halves, under the same names',
    houseParts.length === 2 &&
      houseParts.every((p) => [`${SLOT}__study`, `${SLOT}__b`].includes(p.key)),
    JSON.stringify(houseParts.map((p) => p.key)),
  )
  ok(
    'and the house has its own device for them, not the kitchen\'s',
    houseParts.every((p) => p.words.includes(first) === false),
    JSON.stringify(houseParts.map((p) => p.words)),
  )
  await shot('08-house-tab')

  // -- Take the halves away, on the House tab -------------------------------
  // `b` is still bound *in the room*, and this page refuses to take it away for
  // that reason -- which is worth proving rather than stepping around: the
  // refusal belongs to the part and not to the page the delete was asked from,
  // and a walk that only ever deleted a half from the page holding its device
  // could not tell those two apart.
  await pressOrFail(partRow('b'), 'Delete', { writes: true })
  const held = await refusal()
  ok(
    'the House tab refuses a half a room still binds, and says where',
    held !== null && held.includes(ROOM),
    held ?? 'no refusal drawn',
  )
  ok(
    'and the half is still there',
    (await parts(SLOT)).some((p) => p.key === `${SLOT}__b`),
    JSON.stringify((await parts(SLOT)).map((p) => p.key)),
  )
  // Unbind it where it is bound -- the room's own page -- and then it goes.
  await click(page, '#tab-rooms', { wait: 2500 })
  await openRoom(ROOM)
  await pressOrFail(partRow('b'), 'Unbind', { writes: true })
  await click(page, '#tab-house', { wait: 3000 })
  for (const name of ['study', 'b']) {
    await pressOrFail(partRow(name), 'Delete', { writes: true })
  }
  const left = await parts(SLOT)
  ok('unbound halves are deleted', left.length === 0, JSON.stringify(left.map((p) => p.key)))
  await click(page, '#tab-rooms', { wait: 2500 })
  await openRoom(ROOM)
  ok(
    'and the role is whole again on the room\'s page',
    (await parts(SLOT)).length === 0,
    JSON.stringify(await parts(SLOT)),
  )
  await shot('09-whole-again')
} catch (error) {
  ok(`the walk ran to the end (${String(error).slice(0, 200)})`, false)
}

for (const event of events) console.log(`  ${event}`)
console.log(FAILURES.length ? `\nFAILED: ${FAILURES.length}` : '\nall ok')
await browser.close()
process.exit(FAILURES.length ? 1 : 0)
