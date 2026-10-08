// Scratch probe: what the server answers for a part rename, and what it says after.
import { openPanel, sleep } from './_ui.mjs'

const { browser, page, events } = await openPanel()
await page.waitForFunction(
  () => window.__deepAll('open-house-panel')[0]?.hass?.callWS !== undefined,
  { timeout: 60000 },
)
const call = (m) =>
  page.evaluate((msg) => {
    const panel = window.__deepAll('open-house-panel')[0]
    return panel.hass
      .callWS(msg)
      .then((v) => ({ ok: v }))
      .catch((e) => ({ failed: String(e?.message ?? e) }))
  }, m)

const rooms = await call({ type: "open_house/rooms/list" })
const room = rooms.ok.rooms.find((r) => r.name === 'Kitchen')
const before = await call({ type: 'open_house/rooms/get', room_id: room.id })
const row = before.ok.bindings.find((b) => b.slot === 'light_group')
console.log('parts before:', JSON.stringify((row.parts ?? []).map((p) => p.slot)))

console.log(
  'rename a->study:',
  JSON.stringify(
    await call({
      type: 'open_house/slots/set_parts',
      slot: 'light_group',
      action: 'rename',
      name: 'a',
      new_name: 'study',
    }),
  ),
)
await sleep(3000)
const after = await call({ type: 'open_house/rooms/get', room_id: room.id })
console.log('rooms/get after:', after.ok ? 'ok' : after.failed)
if (after.ok) {
  const now = after.ok.bindings.find((b) => b.slot === 'light_group')
  console.log('parts after:', JSON.stringify((now.parts ?? []).map((p) => `${p.slot}=${p.entity_id}`)))
}
for (const event of events) console.log(`  ${event}`)
await browser.close()
