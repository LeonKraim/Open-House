// Scratch: does the slot-rule *watcher* settle at all?
//
// The store holds a rule (`slot.<slot>.kind`, `.rule`) and no `slot.<slot>.entity`,
// which is the key `settle_slot_rule` writes when a rule works out an answer --
// the same key a person picking a device writes. So either the watcher never
// builds a listener, or it builds one and the listener never fires.
//
// Those two are told apart by moving something: a rule written on entities that
// *are* available in this house, and the light the template reads actually
// turned on and off. A listener that exists moves the slot on the state change
// even if the initial render never came; a listener that does not exist moves
// nothing at all.
import { openPanel, BASE, sleep } from './_ui.mjs'

const ROOM = 'living_room'
const PACK = 'critic_round3'
const SLOT = 'light_group'

const { browser, page, events } = await openPanel()
await page.goto(`${BASE}/open-house`, { waitUntil: 'domcontentloaded' })
await sleep(4000)
const readAll = (fn, arg = null) => page.evaluate(fn, arg)
const ws = (m) => readAll((msg) => window.__deepAll('open-house-panel')[0].hass.callWS(msg), m)
const call = (domain, service, data) =>
  readAll(
    ([d, s, payload]) =>
      window.__deepAll('open-house-panel')[0].hass.callService(d, s, payload).then(
        () => ({ ok: true }),
        (f) => ({ failed: String(f?.message ?? f) }),
      ),
    [domain, service, data],
  )
const state = (id) =>
  readAll((entity) => {
    const s = window.__deepAll('open-house-panel')[0].hass.states[entity]
    return s ? String(s.state) : null
  }, id)

const slotNow = async () => {
  const listed = await ws({ type: 'open_house/modules/list' })
  const module = (listed.modules ?? []).find((one) => one.pack === PACK)
  return (module?.slots ?? []).find((one) => one.slot === SLOT) ?? null
}
/** Just the facts this walk is about, so the output stays readable. */
const brief = (row) =>
  row && {
    entity_id: row.entity_id,
    overridden: row.overridden,
    rule_kind: row.rule_kind,
    rule_device: row.rule_device,
  }

// Only lights that are actually answering: an unavailable device cannot be
// turned on, so a rule reading one can never be given anything to react to.
const live = async () =>
  readAll(() =>
    Object.entries(window.__deepAll('open-house-panel')[0].hass.states)
      .filter(([id, s]) => id.startsWith('light.') && s.state !== 'unavailable' && s.state !== 'unknown')
      .map(([id]) => id)
      .sort(),
  )

const lights = await live()
console.log(`lights that are answering:\n  ${lights.join('\n  ')}`)
if (lights.length < 2) {
  console.log('\nSKIP  fewer than two lights are answering in this house')
  for (const event of events) console.log(`  ${event}`)
  await browser.close()
  process.exit(0)
}
const [switchLight, other] = lights
const TEMPLATE = `{{ '${other}' if is_state('${switchLight}', 'on') else '${switchLight}' }}`
console.log(`switch:   ${switchLight} (${await state(switchLight)})`)
console.log(`other:    ${other} (${await state(other)})`)
console.log(`template: ${TEMPLATE}`)

await call('logger', 'set_level', {
  'custom_components.open_house.slot_rules': 'debug',
})

console.log(`\nbefore: ${JSON.stringify(brief(await slotNow()))}`)
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
console.log(`set -> ${set?.error ? JSON.stringify(set) : 'accepted'}`)
for (const wait of [2000, 4000]) {
  await sleep(wait)
  console.log(`  +${wait}ms: ${JSON.stringify(brief(await slotNow()))}`)
}

console.log('\n-- the initial render never settled it; now move the world --')
console.log(`turn_on -> ${JSON.stringify(await call('light', 'turn_on', { entity_id: switchLight }))}`)
await sleep(5000)
console.log(`switch is ${await state(switchLight)}; slot: ${JSON.stringify(brief(await slotNow()))}`)

console.log(`\nturn_off -> ${JSON.stringify(await call('light', 'turn_off', { entity_id: switchLight }))}`)
await sleep(5000)
console.log(`switch is ${await state(switchLight)}; slot: ${JSON.stringify(brief(await slotNow()))}`)

for (const event of events) console.log(`  ${event}`)
await browser.close()
