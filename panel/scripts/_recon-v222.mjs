// Scratch recon: the testroom / v222 module -- what its slot rows carry.
import { openPanel, all, BASE, sleep } from './_ui.mjs'

const SLUG = 'dynamic_lighting_for_better_sleep_v222_testroom'
const { browser, page, events } = await openPanel()
const readAll = (fn, arg = null) => page.evaluate(fn, arg)

const listing = await readAll(() =>
  window.__deepAll('open-house-panel')[0].hass.callWS({ type: 'open_house/modules/list' }),
)
const module = (listing.modules ?? []).find((one) => one.slug === SLUG)
console.log(`\n== module ${SLUG}`)
console.log(`   pack=${module?.pack} room=${JSON.stringify(module?.room_id)} automaton=${module?.automation_id}`)
for (const slot of module?.slots ?? []) {
  console.log(
    `   slot ${slot.slot}  entity=${JSON.stringify(slot.entity_id)} rule=${JSON.stringify(slot.rule_kind)} ` +
      `separate=${slot.separate} accepts=${JSON.stringify(slot.accepts_domains)}`,
  )
}

await page.goto(`${BASE}/open-house?open_house_tab=rooms&open_house_room=testroom`, {
  waitUntil: 'domcontentloaded',
})
await page.waitForFunction(
  () => window.__deepAll('[data-slot-rule]').length > 0,
  undefined,
  { timeout: 30000 },
)
await sleep(1500)

const shape = await readAll(() => {
  const rows = window.__deepAll('[data-slot-rule]').map((el) => {
    let line = el
    for (let n = el; n; n = n.parentElement) {
      if (window.__deepAll('code', n).length > 0) {
        line = n
        break
      }
    }
    let title = null
    for (let n = line; n && !title; n = n.parentElement) {
      const head = window.__deepAll('h2, h3, h4', n)[0]
      if (head) title = window.__deepText(head).replace(/\s+/g, ' ').trim().slice(0, 70)
    }
    const select = window.__deepAll(`[data-slot-part="${el.getAttribute('data-slot-rule')}"]`)[0]
    return {
      slot: el.getAttribute('data-slot-rule'),
      kind: el.value,
      title,
      line: window.__deepText(line).replace(/\s+/g, ' ').trim().slice(0, 140),
      part: select
        ? {
            value: select.value,
            options: [...select.options].map((o) => `${o.value}:${o.textContent.trim()}`),
          }
        : null,
    }
  })
  const cards = window.__deepAll('open-house-room-settings h3, open-house-room-settings strong')
    .map((n) => window.__deepText(n).replace(/\s+/g, ' ').trim())
    .filter((t) => t.length > 2 && t.length < 80)
  return { rows, cards: [...new Set(cards)].slice(0, 40) }
})

console.log(`\n== rows on the testroom page`)
for (const row of shape.rows) {
  console.log(
    `   ${row.slot} kind=${JSON.stringify(row.kind)} title=${JSON.stringify(row.title)}\n      part=${JSON.stringify(row.part)}\n      ${row.line}`,
  )
}

for (const event of events) console.log(`  ${event}`)
await browser.close()
