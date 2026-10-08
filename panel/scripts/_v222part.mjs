// Scratch: the last hop of the v222 question -- put a module on a slot *part*.
//
// The question was "can dynamic lighting for better sleep use only the
// lightmodule subslot of light_group in testroom". The server fix carries the
// part through `modulesSettings`; the card draws the Part select. What neither
// proves is the whole chain on a live house: bind the part to a device, choose
// that part on the card, and read back that the module's slot reports that part
// *and* the device the part was bound to.
//
// Run: node scripts/_v222part.mjs
//
// Choosing an entity in HA's own selector is *dispatched* rather than clicked,
// as `_parts.mjs` and `_roomwide.mjs` disclose: what is dispatched is the exact
// `value-changed` event the selector emits. Everything after it is a real click
// or a read of the server's own answer.
import { mkdirSync } from 'node:fs'
import { openPanel, BASE, sleep } from './_ui.mjs'

const SLUG = 'dynamic_lighting_for_better_sleep_v222_testroom'
const ROOM = 'testroom'
const SLOT = 'light_group'
const PART = 'lightmodule'
const OUT = 'S:/OS Folders/Desktop/HAOS_Open_House/shots'
mkdirSync(OUT, { recursive: true })

const FAILURES = []
const ok = (label, pass, detail = '') => {
  console.log(`${pass ? 'ok  ' : 'FAIL'} ${label}${detail ? ` -- ${detail}` : ''}`)
  if (!pass) FAILURES.push(label)
}

const { browser, page } = await openPanel()
await page.setViewportSize({ width: 1440, height: 1150 })
const readAll = (fn, arg = null) => page.evaluate(fn, arg)
const waitFor = (fn, arg = null, ms = 90000) =>
  page.waitForFunction(fn, arg, { timeout: ms, polling: 500 })

const ws = (message) =>
  readAll((msg) => window.__deepAll('open-house-panel')[0].hass.callWS(msg), message)

const BUSY = (wanted) => {
  const room = window.__deepAll('open-house-room-settings')[0]
  const card = window.__deepAll('open-house-hosted-module').find((c) =>
    String(c.module?.slug ?? '').includes('v222'),
  )
  const busy = Boolean(room?.busy) || Boolean(card?.busy)
  return wanted ? busy : !busy
}
const settle = async (ms = 60000) => {
  try {
    await waitFor(BUSY, true, 3000)
  } catch {
    /* a write can finish before the first look */
  }
  await waitFor(BUSY, false, ms)
  await sleep(500)
}

const shot = (name) => page.screenshot({ path: `${OUT}/v222part-${name}.png` })

/** Click a button named `label` inside the nearest box holding `anchorSel`. */
const press = (anchorSel, label) =>
  readAll(
    ([sel, text]) => {
      const anchor = window.__deepAll(sel)[0]
      if (!anchor) return { error: `no anchor for ${sel}` }
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

const partRow = `[data-slot-part-row="${SLOT}__${PART}"]`

const partWords = () =>
  readAll((sel) => {
    const row = window.__deepAll(sel)[0]
    return row ? window.__deepText(row).replace(/\s+/g, ' ').trim() : null
  }, partRow)

/** The v222 card's own view of its slots. */
const cardSlots = () =>
  readAll(() => {
    const card = window.__deepAll('open-house-hosted-module').find((c) =>
      String(c.module?.slug ?? '').includes('v222'),
    )
    if (!card) return { error: 'no v222 card on the page' }
    const rows = window.__deepAll('[data-hosted-slot]', card).map((row) => {
      const select = window.__deepAll('[data-slot-part]', row)[0]
      return {
        slot: row.getAttribute('data-hosted-slot'),
        text: window.__deepText(row).replace(/\s+/g, ' ').trim().slice(0, 160),
        part: select
          ? {
              value: select.value,
              options: [...select.options].map((o) => `${o.value}|${o.textContent.trim()}`),
            }
          : null,
      }
    })
    return { rows, slots: card.module?.slots ?? [] }
  })

/** The record's own answer, off the server. */
const hosted = async () => {
  const listing = await ws({ type: 'open_house/modules/hosted' })
  const record = (listing.modules ?? listing.records ?? []).find((m) => m.slug === SLUG)
  return { slots: record?.slots ?? null, bindings: record?.bindings ?? null }
}

/** Choose a part on the card, through the select's own change event. */
const choosePart = async (part) => {
  await readAll(
    ([slug, value]) => {
      const card = window.__deepAll('open-house-hosted-module').find((c) =>
        String(c.module?.slug ?? '').includes(slug),
      )
      const select = window.__deepAll('[data-slot-part]', card)[0]
      select.value = value
      select.dispatchEvent(new Event('change', { bubbles: true }))
    },
    ['v222', part],
  )
  await settle()
}

try {
  await page.goto(`${BASE}/open-house?open_house_tab=rooms&open_house_room=${ROOM}`, {
    waitUntil: 'domcontentloaded',
  })
  await page.waitForFunction(
    () =>
      window.__deepAll('open-house-room-settings').length > 0 &&
      window.__deepAll('open-house-hosted-module').some((c) =>
        String(c.module?.slug ?? '').includes('v222'),
      ),
    undefined,
    { timeout: 40000 },
  )
  await sleep(2500)
  await shot('00-page')

  // -- What the house already carries ---------------------------------------
  const rows = await readAll(
    (slot) =>
      window
        .__deepAll('[data-slot-part-row]')
        .filter((r) => (r.getAttribute('data-slot-part-row') ?? '').startsWith(`${slot}__`))
        .map((r) => ({
          key: r.getAttribute('data-slot-part-row'),
          words: window.__deepText(r).replace(/\s+/g, ' ').trim(),
        })),
    SLOT,
  )
  console.log(`part rows of ${SLOT}: ${JSON.stringify(rows, null, 1)}`)
  ok(`the ${SLOT} row carries a ${PART} part`, rows.some((r) => r.key === `${SLOT}__${PART}`), JSON.stringify(rows.map((r) => r.key)))

  const before = await cardSlots()
  console.log(`card before: ${JSON.stringify(before, null, 1)}`)
  const row = (before.rows ?? []).find((r) => r.slot === SLOT)
  ok(
    `the card offers the ${PART} part on its ${SLOT} row`,
    (row?.part?.options ?? []).some((o) => o.startsWith(`${PART}|`)),
    JSON.stringify(row?.part),
  )

  // -- Bind the part to a real light ----------------------------------------
  const lights = await readAll(() => Object.keys(window.__deepAll('open-house-panel')[0].hass.states).filter((id) => id.startsWith('light.')))
  const slotNow = await readAll((slot) => {
    const rows = window.__deepAll('open-house-room-settings tbody tr')
    const hit = rows.find((r) => window.__deepText(r).includes(slot))
    return hit ? window.__deepText(hit).replace(/\s+/g, ' ').trim() : null
  }, SLOT)
  console.log(`lights in the house: ${JSON.stringify(lights)}`)
  console.log(`the ${SLOT} row on the page: ${JSON.stringify(slotNow)}`)
  const pick = lights.find((id) => !(slotNow ?? '').includes(id)) ?? lights[0]
  if (!pick) throw new Error('no light in the house to bind a part to')

  if (!(await partWords())?.includes(pick)) {
    console.log(`binding ${PART} -> ${pick}`)
    await pressOrFail(partRow, 'Bind')
    await waitFor(() => window.__deepAll('open-house-room-settings ha-selector').length > 0)
    await readAll((id) => {
      const cards = window
        .__deepAll('open-house-room-settings .card')
        .filter((c) => window.__deepText(c).includes('Cancel'))
        .sort((a, b) => window.__deepText(a).length - window.__deepText(b).length)
      const selector = window.__deepAll('ha-selector', cards[0])[0]
      selector.dispatchEvent(
        new CustomEvent('value-changed', { detail: { value: id }, bubbles: true, composed: true }),
      )
    }, pick)
    await settle()
  }
  const bound = await partWords()
  console.log(`the ${PART} part now says: ${JSON.stringify(bound)}`)
  ok(`the ${PART} part holds ${pick}`, (bound ?? '').includes(pick), bound ?? 'no row')
  await shot('01-part-bound')

  // -- Put the module on that part -----------------------------------------
  await choosePart(PART)
  const after = await cardSlots()
  console.log(`card after: ${JSON.stringify(after, null, 1)}`)
  const chosen = (after.rows ?? []).find((r) => r.slot === SLOT)
  ok(
    `the card's ${SLOT} row selects the ${PART} part`,
    chosen?.part?.value === PART,
    JSON.stringify(chosen?.part),
  )
  ok(
    'and the row now names the device the part is bound to',
    (chosen?.text ?? '').includes(pick),
    chosen?.text,
  )
  const record = await hosted()
  console.log(`the record: ${JSON.stringify(record, null, 1)}`)
  const rec = (record.slots ?? []).find((s) => s.name === SLOT)
  ok(
    `the server reports part=${PART} bound=${pick}`,
    rec?.part === PART && rec?.bound === pick,
    JSON.stringify(rec),
  )
  await shot('02-module-on-part')

  // -- Put it back on the whole slot ---------------------------------------
  await choosePart('')
  const restored = (await hosted()).slots?.find((s) => s.name === SLOT)
  ok(
    'and it goes back on the whole slot',
    restored?.part === '' || restored?.part === undefined,
    JSON.stringify(restored),
  )
  await shot('03-restored')
} catch (failure) {
  console.error(`\n${failure?.stack ?? failure}`)
  FAILURES.push(String(failure?.message ?? failure))
} finally {
  console.log(`\n${FAILURES.length === 0 ? 'all checks passed' : `${FAILURES.length} FAILED: ${FAILURES.join(' | ')}`}`)
  await browser.close()
  process.exitCode = FAILURES.length === 0 ? 0 : 1
}
