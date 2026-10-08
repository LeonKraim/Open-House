// Scratch: "Set it to" on a *slot* row -- logic held by the slot, not by an input.
//
// The claim under test is demand four: the same four casts a module's input rows
// offer can be put on a slot row, and the slot then acts on the device the logic
// works out rather than on the device the room bound. The adapter tests prove the
// rule (`ha_adapter.slot_rules`, `live_modules.settle_slot_rule`); what they
// cannot prove is that a person can reach it and that the house follows -- that
// the menu is on the row, that "Set it" writes it, and that the module's device
// really moves when the thing the rule watches changes.
//
// Run: node scripts/_slotrule.mjs
//
// **The row and the device are the house's, not this file's.** Which room holds a
// slot pointing at a light is a fact about the house; the walk reads the page for
// the first slot row that is bound to a light and has no rule on it yet, and asks
// the server for that module's own record to check what it acts on.
//
// **What moves the slot is a real service call, not a stub.** The template reads
// the state of a light this walk turns on and off through Home Assistant's own
// `light.turn_on` / `light.turn_off` -- so the watcher is driven by the same
// thing a person's switch would drive it with, and nothing is created in the
// house that has to be cleaned up.
//
// **One step is driven without a real click, and it says so.** The template box
// is Home Assistant's own `ha-selector-template` (its editor is CodeMirror): what
// is dispatched is exactly the `value-changed` event the form emits when a person
// types one. The menu is a plain `<select>` and *is* clicked, and so is "Set it".
import { mkdirSync } from 'node:fs'
import { openPanel, all, click, sleep, BASE } from './_ui.mjs'

const SHOTS = `${process.env.TEMP ?? '/tmp'}/open-house-shots`

const FAILURES = []
const ok = (label, pass, detail = '') => {
  console.log(`${pass ? 'ok  ' : 'FAIL'} ${label}${detail ? ` -- ${detail}` : ''}`)
  if (!pass) FAILURES.push(label)
}

const { browser, page, events } = await openPanel()
const readAll = (fn, arg = null) => page.evaluate(fn, arg)
const waitFor = (fn, arg = null, ms = 60000) =>
  page.waitForFunction(fn, arg, { timeout: ms, polling: 400 })
mkdirSync(SHOTS, { recursive: true })
const shot = (name) => page.screenshot({ path: `${SHOTS}/slotrule-${name}.png` })

/** One `open_house/*` command over the panel's own websocket connection. */
const ws = (message) =>
  readAll((msg) => {
    const panel = window.__deepAll('open-house-panel')[0]
    return panel.hass.callWS(msg)
  }, message)

/** A Home Assistant service call -- how a person's switch moves the house. */
const service = (domain, name, data) =>
  readAll(
    ([d, s, payload]) => {
      const panel = window.__deepAll('open-house-panel')[0]
      return panel.hass.callService(d, s, payload).then(
        () => ({ ok: true }),
        (failure) => ({ failed: String(failure?.message ?? failure) }),
      )
    },
    [domain, name, data],
  )

const stateOf = (entityId) =>
  readAll((id) => {
    const state = window.__deepAll('open-house-panel')[0].hass.states[id]
    return state === undefined ? null : String(state.state)
  }, entityId)

/**
 * What the *page* shows on one slot row, and the row's handle on it.
 *
 * The target is marked in the page (`data-walk-target`) rather than remembered by
 * index: a room's page holds a slot row per module and the same slot name appears
 * on several of them, so a selector that named only the slot would drive
 * whichever row came first in the document rather than the one being read.
 */
const targetRow = () =>
  readAll(() => {
    const el = window.__deepAll('[data-walk-target]')[0]
    if (!el) return { error: 'the walk has lost the row it marked' }
    const want = el.getAttribute('data-slot-rule')
    const row = el.closest('.stack')?.parentElement ?? el.parentElement
    return {
      kind: el.value,
      chip: row.querySelector(`[data-slot-rule-chip="${want}"]`)?.textContent?.trim() ?? null,
      summary:
        row
          .querySelector(`[data-slot-rule-summary="${want}"]`)
          ?.textContent?.replace(/\s+/g, ' ')
          .trim() ?? null,
      error:
        row
          .querySelector(`[data-slot-rule-error="${want}"]`)
          ?.textContent?.replace(/\s+/g, ' ')
          .trim() ?? null,
      device: window.__deepText(row).replace(/\s+/g, ' ').trim().slice(0, 200),
    }
  })

/** The module's own record: what it acts on, and what kind of rule it holds. */
const moduleOn = async (pack, slot) => {
  const listed = await ws({ type: 'open_house/modules/list' })
  const module = (listed.modules ?? []).find((one) => one.pack === pack)
  return (module?.slots ?? []).find((one) => one.slot === slot) ?? null
}

try {
  // -- Find a slot row the house can be moved on ------------------------------
  // The **server** picks the slot and the module, because the pack behind a row
  // is not on the page: `modules/list` is the one answer that carries the pack, the
  // room it is placed in, and every slot it reaches with the device it acts on.
  const listed = await ws({ type: 'open_house/modules/list' })
  let picked = null
  for (const module of listed.modules ?? []) {
    for (const row of module.slots ?? []) {
      const entity = row.entity_id ?? ''
      // A light slot, on a light, with nothing deciding it yet -- so the rule this
      // walk writes is its first, and the device it moves to is one the walk
      // chose rather than a leftover of somebody else's.
      //
      // The *bound entity's* domain is the evidence that the slot takes a light,
      // and it is the only evidence available here: `accepts_domains` is `()` on
      // this command deliberately (`live_modules._slot_rows` says why -- the
      // vocabulary projection keeps only the facts a rule reads). Asking for it
      // anyway is how this walk came to find no target in a house full of them,
      // and to report that as nothing to test rather than as its own mistake.
      if (!entity.startsWith('light.')) continue
      if (row.rule_kind !== null) continue
      picked = { pack: module.pack, room: module.room_id, slot: row.slot, entity }
      break
    }
    if (picked) break
  }

  if (!picked) {
    console.log('SKIP  no module in this house has an unruled slot bound to a light')
    for (const event of events) console.log(`  ${event}`)
    await browser.close()
    process.exit(0)
  }
  const { pack } = picked
  console.log(
    `the house offers ${pack} (room ${JSON.stringify(picked.room)}) / slot ` +
      `${picked.slot}, on ${picked.entity}`,
  )

  // The page that draws this module's row. The Rooms tab lists every module the
  // house holds -- not only the room the address names -- so the row is found by
  // *what it is bound to* and not by which room the walk opened.
  await page.goto(
    `${BASE}/open-house?open_house_tab=rooms&open_house_room=${encodeURIComponent(picked.room)}`,
    { waitUntil: 'domcontentloaded' },
  )
  await page.waitForFunction(
    () => window.__deepAll('[data-slot-rule]').length > 0,
    undefined,
    { timeout: 30000 },
  )
  const marked = await readAll(
    ([slot, entity, wanted]) => {
      const holds = (el) => {
        let box = el
        for (let n = el; n; n = n.parentElement) {
          if (window.__deepAll('code', n).length > 0) {
            box = n
            break
          }
        }
        return window.__deepText(box).includes(entity)
      }
      // **Inside the module's own card**, because the page draws the same slot
      // twice: once in the room's Devices table and once on the card for every
      // module that reaches it (`tabs/room-settings.ts`), and both rows carry the
      // device. The rule the walk writes belongs on the card's row -- that is the
      // one addressed by the module -- so the card is searched first and the room
      // table is only a fallback for a page that does not draw cards.
      const cards = window.__deepAll(`[data-pack="${wanted}"]`)
      const inCard = cards.flatMap((card) =>
        window.__deepAll(`[data-slot-rule="${slot}"]`, card),
      )
      const hits = (inCard.length > 0 ? inCard : window.__deepAll(`[data-slot-rule="${slot}"]`))
        .filter(holds)
      // Exactly one: a row chosen from two would be a click on one module that the
      // walk then read off another, and a slot name repeats across modules.
      if (hits.length !== 1) return { count: hits.length }
      const el = hits[0]
      el.setAttribute('data-walk-target', '1')
      ;(el.closest('.stack') ?? el).setAttribute('data-walk-row', '1')
      return {
        count: 1,
        kind: el.value,
        row: window
          .__deepText(el.closest('.stack')?.parentElement ?? el)
          .replace(/\s+/g, ' ')
          .trim()
          .slice(0, 160),
      }
    },
    [picked.slot, picked.entity, picked.pack],
  )
  ok(
    'the module has a slot row on the page, bound to that device',
    marked?.count === 1,
    marked?.count === 1
      ? marked.row
      : `${marked?.count} rows matched ${picked.slot} on ${picked.entity}`,
  )

  // The two devices the rule will move between, and the switch that moves it.
  const others = await readAll(
    (current) =>
      Object.keys(window.__deepAll('open-house-panel')[0].hass.states)
        .filter((id) => id.startsWith('light.') && id !== current)
        .sort(),
    picked.entity,
  )
  if (others.length < 2) throw new Error('this house has fewer than two other lights')
  const [lightOn, lightOff] = others
  const switchLight = picked.entity
  // The template: while the switch light is on the slot is one device, and while
  // it is off the slot is another. Two answers, so a slot that moved can be told
  // from a slot that never moved at all.
  const template =
    `{{ '${lightOn}' if is_state('${switchLight}', 'on') else '${lightOff}' }}`
  const wasOn = (await stateOf(switchLight)) === 'on'
  console.log(`  the rule will read ${switchLight} and move between ${lightOn} / ${lightOff}`)

  const before = await moduleOn(pack, picked.slot)
  ok(
    'the module acts on the room\x27s own device to start with',
    before?.entity_id === picked.entity && before.rule_kind === null,
    `entity_id=${before?.entity_id}, rule=${JSON.stringify(before?.rule_kind)}`,
  )
  await shot('01-before')

  // -- Put the menu on a template, and write it -------------------------------
  const rowNow = await targetRow()
  ok(
    'the slot row carries the "Set it to" menu, on a device',
    (rowNow.kind ?? '') === '',
    `kind=${JSON.stringify(rowNow.kind)}, row=${rowNow.device?.slice(0, 90)}`,
  )
  // Dispatched rather than handed to `page.selectOption`: the walk's row lives in
  // a shadow root, and Playwright's selector engine reads the light DOM only -- it
  // waits out its timeout on a locator that will never match. The event is the one
  // the select emits when a person chooses, and everything after it is read back
  // off the page.
  const chose = await readAll(() => {
    const el = window.__deepAll('[data-walk-target]')[0]
    if (!el) return false
    el.value = 'template'
    el.dispatchEvent(new Event('change', { bubbles: true }))
    return true
  })
  if (!chose) throw new Error('the walk has lost the row it marked')
  await page.waitForFunction(
    () => window.__deepAll('[data-slot-rule-form]').length > 0,
    undefined,
    { timeout: 15000 },
  )
  const opened = await targetRow()
  ok(
    'choosing "A template" draws the rule\x27s own field and its Set it',
    (await all(page, '[data-slot-rule-save]')).length > 0,
    `summary=${JSON.stringify(opened.summary)}`,
  )

  // A person types into Home Assistant's own template box (see the header).
  const written = await readAll(
    ([slot, text]) => {
      const form = window.__deepAll(`[data-slot-rule-form="${slot}"]`)[0]
      if (!form) return false
      form.dispatchEvent(
        new CustomEvent('value-changed', {
          detail: { value: { ...form.data, value: text } },
          bubbles: true,
          composed: true,
        }),
      )
      return true
    },
    [picked.slot, template],
  )
  if (!written) throw new Error('the rule form never drew')
  await sleep(400)
  await shot('02-template-typed')

  // "Set it" -- a real click, hit-tested first.
  await click(page, `[data-slot-rule-save="${picked.slot}"]`, { wait: 2500 })

  // -- And the house follows --------------------------------------------------
  let row = null
  for (let attempt = 0; attempt < 30; attempt += 1) {
    row = await moduleOn(pack, picked.slot)
    if (row?.rule_kind === 'template' && row.entity_id === lightOff) break
    await sleep(1000)
  }
  ok(
    'the slot holds the rule, and acts on the device the template worked out',
    row?.rule_kind === 'template' && row.entity_id === lightOff,
    `rule=${JSON.stringify(row?.rule_kind)}, entity_id=${row?.entity_id} (wanted ${lightOff})`,
  )
  const onPage = await targetRow()
  ok(
    'and the row says so where the person is looking',
    (onPage.chip ?? '').includes('template') && (onPage.device ?? '').includes(lightOff),
    `chip=${JSON.stringify(onPage.chip)}, summary=${JSON.stringify(onPage.summary)}`,
  )
  await shot('03-rule-set')

  // -- Move the thing the rule watches, and the slot moves with it -------------
  await service('light', 'turn_on', { entity_id: switchLight })
  let moved = null
  for (let attempt = 0; attempt < 30; attempt += 1) {
    moved = await moduleOn(pack, picked.slot)
    if (moved?.entity_id === lightOn) break
    await sleep(1000)
  }
  ok(
    'turning the light the template reads moves the slot to its other device',
    moved?.entity_id === lightOn,
    `entity_id=${moved?.entity_id} (wanted ${lightOn})`,
  )
  await shot('04-moved')

  await service('light', 'turn_off', { entity_id: switchLight })
  let back = null
  for (let attempt = 0; attempt < 30; attempt += 1) {
    back = await moduleOn(pack, picked.slot)
    if (back?.entity_id === lightOff) break
    await sleep(1000)
  }
  ok(
    'and turning it back moves the slot back',
    back?.entity_id === lightOff,
    `entity_id=${back?.entity_id} (wanted ${lightOff})`,
  )
  if (wasOn) await service('light', 'turn_on', { entity_id: switchLight })

  // -- Take the rule off again ------------------------------------------------
  // "Clear" is drawn only while the menu is shut, and it is: the save took the
  // draft away and the page read the room again. A real click, inside the row
  // this walk marked -- a house may hold another rule on another row, and a
  // button found by its words alone would be that one.
  await click(page, 'button', {
    nth: 'Clear',
    within: '[data-walk-row]',
    wait: 2500,
  })
  let cleared = null
  for (let attempt = 0; attempt < 30; attempt += 1) {
    cleared = await moduleOn(pack, picked.slot)
    if (cleared?.rule_kind === null) break
    await sleep(1000)
  }
  ok(
    'taking the rule off puts the module back on the room\x27s own device',
    cleared?.rule_kind === null && cleared.entity_id === picked.entity,
    `rule=${JSON.stringify(cleared?.rule_kind)}, entity_id=${cleared?.entity_id} ` +
      `(wanted ${picked.entity})`,
  )
  await shot('05-cleared')
} catch (error) {
  ok(`the walk ran to the end (${String(error).slice(0, 300)})`, false)
}

for (const event of events) console.log(`  ${event}`)
console.log(FAILURES.length ? `\nFAILED: ${FAILURES.length}` : '\nall ok')
await browser.close()
process.exit(FAILURES.length ? 1 : 0)
