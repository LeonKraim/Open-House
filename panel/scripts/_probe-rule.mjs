// Scratch: is a slot rule stored *and* does it move the slot? Set one straight
// down the socket with a known template, then flip the light the template reads.
import { openPanel, BASE, sleep } from './_ui.mjs'

const ROOM = 'living_room'
const PACK = 'critic_round3'
const SLOT = 'light_group'
const SWITCH = 'light.open_house_mock_fleet_minimal_living_room'
const ON = 'light.garage_demo_garage'
const OFF = 'light.open_house_mock_fleet_demo_bathroom'
const TEMPLATE = `{{ '${ON}' if is_state('${SWITCH}', 'on') else '${OFF}' }}`

const { browser, page } = await openPanel()
await page.goto(`${BASE}/open-house`, { waitUntil: 'domcontentloaded' })
await sleep(4000)
const readAll = (fn, arg = null) => page.evaluate(fn, arg)
const ws = (m) => readAll((msg) => window.__deepAll('open-house-panel')[0].hass.callWS(msg), m)

const slotNow = async () => {
  const listed = await ws({ type: 'open_house/modules/list' })
  const module = (listed.modules ?? []).find((one) => one.pack === PACK)
  return (module?.slots ?? []).find((one) => one.slot === SLOT) ?? null
}
const lightState = (id) =>
  readAll((entity) => {
    const s = window.__deepAll('open-house-panel')[0].hass.states[entity]
    return s ? String(s.state) : null
  }, id)
const call = (domain, service, data) =>
  readAll(
    ([d, s, payload]) => window.__deepAll('open-house-panel')[0].hass.callService(d, s, payload),
    [domain, service, data],
  )

console.log(`before: ${JSON.stringify(await slotNow())}`)
console.log(`switch state: ${await lightState(SWITCH)}`)

const set = await ws({
  type: 'open_house/modules/set_slot_rule',
  room_id: ROOM,
  pack: PACK,
  slot: SLOT,
  kind: 'template',
  value: TEMPLATE,
  when: [],
  device: null,
})
console.log(`\nset rule -> ${JSON.stringify(set?.modules?.find?.((m) => m.pack === PACK)?.slots ?? set)}`)
await sleep(3000)
console.log(`after set: ${JSON.stringify(await slotNow())}`)

// Force the switch light on, so the template's first branch is the answer.
await call('light', 'turn_on', { entity_id: SWITCH })
await sleep(4000)
console.log(`\nswitch on -> ${await lightState(SWITCH)}`)
console.log(`slot now: ${JSON.stringify(await slotNow())}`)

await call('light', 'turn_off', { entity_id: SWITCH })
await sleep(4000)
console.log(`\nswitch off -> ${await lightState(SWITCH)}`)
console.log(`slot now: ${JSON.stringify(await slotNow())}`)

// Leave the house as it was found.
await ws({
  type: 'open_house/modules/set_slot_rule',
  room_id: ROOM,
  pack: PACK,
  slot: SLOT,
  kind: '',
  value: null,
  when: [],
  device: null,
})
await sleep(2500)
console.log(`\ncleared: ${JSON.stringify(await slotNow())}`)
await browser.close()
