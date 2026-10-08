// Scratch: put a house back the way it was, after a walk that did not finish.
//
// A part left bound in a room cannot be taken away (that is the refusal the walk
// is proving), so the cleanup is two calls in the right order: unbind each bound
// part's own key in the room, then drop the parts from the record. A room's
// bindings come back as rows with the parts folded into the row they belong to,
// which is why the parts are read out of `row.parts` rather than the row's own
// `slot`.
import { openPanel, sleep } from './_ui.mjs'

const ROOM = 'Kitchen'
const SLOT = 'light_group'

const { browser, page, events } = await openPanel()
await page.waitForFunction(
  () => window.__deepAll('open-house-panel')[0]?.hass?.callWS !== undefined,
  { timeout: 60000 },
)

const call = async (message) => {
  for (let attempt = 0; attempt < 12; attempt += 1) {
    const answer = await page.evaluate((msg) => {
      const panel = window.__deepAll('open-house-panel')[0]
      return panel.hass
        .callWS(msg)
        .then((value) => ({ ok: value }))
        .catch((error) => ({ failed: String(error?.message ?? error) }))
    }, message)
    if (answer.ok !== undefined) return answer.ok
    if (!answer.failed.includes('reloading')) throw new Error(answer.failed)
    await sleep(5000)
  }
  throw new Error('the integration never came back')
}

const rooms = await call({ type: 'open_house/rooms/list' })
const room = rooms.rooms.find((r) => r.name === ROOM)
const detail = await call({ type: 'open_house/rooms/get', room_id: room.id })
const row = detail.bindings.find((b) => b.slot === SLOT)
console.log(`parts before: ${JSON.stringify((row.parts ?? []).map((p) => `${p.slot}=${p.entity_id}`))}`)

for (const part of row.parts ?? []) {
  if (part.entity_id !== null) {
    console.log(
      `unbind ${part.slot}: ${JSON.stringify(
        await call({ type: 'open_house/rooms/unbind', room_id: room.id, slot: part.slot }).catch(
          (e) => String(e),
        ),
      )}`,
    )
  }
}
for (const part of row.parts ?? []) {
  console.log(
    `remove ${part.name}: ${JSON.stringify(
      await call({
        type: 'open_house/slots/set_parts',
        slot: SLOT,
        action: 'remove',
        name: part.name,
        new_name: '',
      }).catch((e) => String(e)),
    ).slice(0, 200)}`,
  )
}

const after = await call({ type: 'open_house/rooms/get', room_id: room.id })
const afterRow = after.bindings.find((b) => b.slot === SLOT)
console.log(
  `after: entity=${afterRow.entity_id} parts=${JSON.stringify((afterRow.parts ?? []).map((p) => p.slot))}`,
)
for (const event of events) console.log(`  ${event}`)
await browser.close()
