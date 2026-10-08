import { openPanel } from './_ui.mjs'
const { browser, page, events } = await openPanel()
const readAll = (fn, arg = null) => page.evaluate(fn, arg)
const listing = await readAll(() =>
  window.__deepAll('open-house-panel')[0].hass.callWS({ type: 'open_house/modules/hosted' }),
)
console.log('keys:', JSON.stringify(Object.keys(listing ?? {})))
for (const m of listing.modules ?? []) {
  console.log(`\n${m.slug} | ${m.title} | room=${JSON.stringify(m.room_id)} | auto=${JSON.stringify(m.automation_id)}`)
  for (const s of m.settings ?? []) {
    console.log(`   setting ${s.name} kind=${s.bound_kind} value=${JSON.stringify(s.value)?.slice(0,60)} cast=${JSON.stringify(s.cast)?.slice(0,60)}`)
  }
  for (const s of m.slots ?? []) {
    console.log(`   slot ${s.name} bound=${JSON.stringify(s.bound)}`)
  }
  for (const o of m.outputs ?? []) console.log(`   output ${JSON.stringify(o).slice(0,120)}`)
}
for (const event of events) console.log(`  ${event}`)
await browser.close()
