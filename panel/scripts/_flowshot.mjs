// Scratch: the module card with a flow that really exists, as an image.
//
// `_shot.mjs` photographs the *import* screen, where no flow exists yet and the
// embedded editor lands on Node-RED's own home page. This is the other half of
// the claim: host a module whose row is answered by a flow, draw its card, and
// check the frame is opened **on that flow's tab** -- the id Node-RED hands back
// on the first save, which is the whole reason the card holds one.
//
// It hosts through the panel's own websocket connection rather than through the
// UI, because the UI path to a hosted module is a dozen clicks that the browser
// walk already makes; what is in question here is one property of one element.
// The module is taken back out again before this returns.
//
// Run: node scripts/_flowshot.mjs   (writes into SHOTS, printed at the end)
import { mkdirSync } from 'node:fs'
import { openPanel, click, sleep } from './_ui.mjs'

const SHOTS = `${process.env.TEMP ?? '/tmp'}/open-house-shots`
const BLUEPRINT = 'MarqBarq/dynamic-lighting.yaml'
const WATCHED = 'light.open_house_mock_fleet_minimal_living_room'
const LUX = 'sensor.open_house_mock_fleet_minimal_living_room_lux'
const TITLE = 'Flow shot'
const MODULE = 'flow_shot'

const { browser, page, events } = await openPanel()
const read = (fn, arg = null) => page.evaluate(fn, arg)
const waitFor = (fn, arg = null, ms = 90000) =>
  page.waitForFunction(fn, arg, { timeout: ms, polling: 500 })

mkdirSync(SHOTS, { recursive: true })

/** One command over the panel's own connection -- the same wire it commands on. */
const command = (message) =>
  read(async (msg) => {
    const panel = window.__deepAll('open-house-panel')[0]
    try {
      return { ok: true, reply: await panel.hass.callWS(msg) }
    } catch (error) {
      return { ok: false, reply: String(error?.message ?? error) }
    }
  }, message)

const bindings = {
  lux_sensor: { kind: 'entity', value: LUX },
  weather_entity: { kind: 'literal', value: 'weather.nowhere' },
  lights: { kind: 'entity', value: WATCHED },
  bypass_light: { kind: 'entity', value: WATCHED },
}

await command({ type: 'open_house/modules/unhost', module: MODULE })
const hosted = await command({
  type: 'open_house/modules/host',
  kind: 'blueprint',
  key: BLUEPRINT,
  title: TITLE,
  bindings,
  outputs: [],
  settings: ['bypass_light'],
  flows: ['bypass_light'],
})
console.log('hosted:', JSON.stringify(hosted).slice(0, 300))
await sleep(2000)

// The card for a module in no room lives on the House tab.
await waitFor(() => window.__deepAll('#tab-house').length > 0)
await click(page, '#tab-house', { wait: 2500 })

const card = await read((slug) => {
  const el = window
    .__deepAll('open-house-hosted-module')
    .find((node) => window.__deepText(node).includes(slug))
  if (!el) return null
  const embed = window.__deepAll('open-house-node-red', el)[0]
  const frame = embed ? window.__deepAll('iframe', embed)[0] : null
  return {
    text: window.__deepText(el).replace(/\s+/g, ' ').slice(0, 400),
    src: frame ? frame.getAttribute('src') : null,
  }
}, TITLE)
console.log('the card:', JSON.stringify(card))

if (card?.src) {
  await read((slug) => {
    const el = window
      .__deepAll('open-house-hosted-module')
      .find((node) => window.__deepText(node).includes(slug))
    window.__deepAll('open-house-node-red', el)[0]?.scrollIntoView({
      block: 'start',
    })
  }, TITLE)
  // The frame is Node-RED's whole frontend; give it the load.
  await sleep(6000)
  await page.screenshot({ path: `${SHOTS}/card-editor.png` })
  console.log(`the card's embedded editor: ${SHOTS}/card-editor.png`)
}

const gone = await command({ type: 'open_house/modules/unhost', module: MODULE })
console.log('unhosted:', JSON.stringify(gone).slice(0, 200))
console.log(events.filter((e) => !e.startsWith('NAV')).join('\n'))
await browser.close()
