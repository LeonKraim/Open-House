import { openPanel } from './_ui.mjs'
const { browser, page, events } = await openPanel()
const readAll = (fn, arg = null) => page.evaluate(fn, arg)
const listing = await readAll(() =>
  window.__deepAll('open-house-panel')[0].hass.callWS({ type: 'open_house/modules/list' }),
)
console.log(JSON.stringify(Object.keys(listing ?? {})))
const first = (listing.modules ?? [])[0]
console.log('first keys:', JSON.stringify(Object.keys(first ?? {})))
for (const m of listing.modules ?? []) {
  console.log(`${m.pack ?? m.slug} | ${m.name ?? m.title} | room=${JSON.stringify(m.room_id)} | slots=${(m.slots ?? []).length}`)
}
for (const event of events) console.log(`  ${event}`)
await browser.close()
