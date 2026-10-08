// Scratch: the Configuration bar on a hosted module card, driven by real clicks.
//
// `tools/ha/probe_hosting.py` proves the *server* side: that switching rebuilds
// the module into the same automation, that an edit lands in the configuration it
// was made in, that the last one cannot be dropped. It cannot see the screen --
// whether the bar names the configurations at all, whether the one running is the
// one selected, whether New and Rename take a name through a dialog a person can
// actually type into, and whether Delete is off when there is only one left.
//
// That is what this walks, with real mouse clicks and real keystrokes, so a
// control rendered behind something is a failure and not a pass. It hosts its own
// module through the panel's own websocket connection -- the same wire the panel
// commands on -- because the click-path to a hosted module is a dozen clicks the
// other walks already make, and what is in question here is one bar.
//
// Run: node scripts/_config.mjs
import { mkdirSync } from 'node:fs'
import { openPanel, click, type, sleep } from './_ui.mjs'

const SHOTS = `${process.env.TEMP ?? '/tmp'}/open-house-shots`
const BLUEPRINT = 'MarqBarq/dynamic-lighting.yaml'
const LUX = 'sensor.open_house_mock_fleet_minimal_living_room_lux'
const LIGHT = 'light.open_house_mock_fleet_minimal_living_room'
const TITLE = 'Config walk'
const MODULE = 'config_walk'

const FAILURES = []
const check = (label, ok, detail = '') => {
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${label}${detail ? ` -- ${detail}` : ''}`)
  if (!ok) FAILURES.push(label)
}

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

/**
 * What the card's Configuration bar is showing.
 *
 * Read off the rendered DOM by the ids the bar itself uses, so a bar that drew
 * the wrong thing cannot answer as though it had drawn the right one: the names
 * come from the `<option>` elements, the running one from the `select`'s own
 * value, and the dialog from whether the sheet is in the page at all.
 */
const barState = () =>
  read((title) => {
    const card = window
      .__deepAll('open-house-hosted-module')
      .find((node) => window.__deepText(node).includes(title))
    if (!card) return null
    const select = window.__deepAll('select', card)[0]
    const asked = window.__deepAll('[data-config]', card)[0]
    const confirm = window.__deepAll('#config-confirm', card)[0]
    const remove = window.__deepAll('button', card).find((button) =>
      (button.id ?? '').startsWith('config-delete-'),
    )
    return {
      options: select
        ? [...select.options].map((option) => option.value)
        : null,
      selected: select
        ? [...select.options].find((option) => option.selected)?.value ?? select.value
        : null,
      config: asked ? asked.getAttribute('data-config') : null,
      dialog: confirm ? 'open' : 'closed',
      deleteOff: remove ? remove.disabled : null,
      notices: window
        .__deepAll('.banner', card)
        .map((banner) => (banner.textContent ?? '').replace(/\s+/g, ' ').trim()),
    }
  }, TITLE)

await command({ type: 'open_house/modules/unhost', module: MODULE })
await command({
  type: 'open_house/modules/host',
  kind: 'blueprint',
  key: BLUEPRINT,
  title: TITLE,
  bindings: {
    lux_sensor: { kind: 'entity', value: LUX },
    weather_entity: { kind: 'literal', value: 'weather.nowhere' },
    lights: { kind: 'entity', value: LIGHT },
    max_brightness_percent: { kind: 'literal', value: 100 },
  },
  outputs: [],
  settings: ['max_brightness_percent'],
  flows: [],
})
await sleep(2500)

// The card for a module in no room lives on the House tab.
await waitFor(() => window.__deepAll('#tab-house').length > 0)
await click(page, '#tab-house', { wait: 2500 })
await waitFor(
  (title) =>
    window
      .__deepAll('open-house-hosted-module')
      .some((node) => window.__deepText(node).includes(title)),
  TITLE,
)

let state = await barState()
check(
  'the card draws a Configuration bar naming the one it holds',
  state?.options?.join() === 'Default' && state?.selected === 'Default',
  JSON.stringify(state),
)

// -- New: a name typed into the panel's own sheet, and the module switched ----
await click(page, `#config-new-${MODULE}`)
state = await barState()
check('New opens a dialog to type a name into', state?.dialog === 'open', state?.dialog)
await type(page, '#config-name', 'Evening')
await click(page, '#config-confirm')
await sleep(2500)
state = await barState()
check(
  'the new configuration is made, and the module is running it',
  state?.options?.join() === 'Default,Evening' && state?.selected === 'Evening',
  JSON.stringify({ options: state?.options, selected: state?.selected }),
)
check(
  'and the settings rows are drawn as the running configuration',
  state?.config === 'Evening',
  state?.config,
)
check(
  'which the card says in words as well',
  (state?.notices ?? []).some((notice) => notice.includes('Evening')),
  JSON.stringify(state?.notices),
)

// -- Rename ----------------------------------------------------------------
await click(page, `#config-rename-${MODULE}`)
await type(page, '#config-name', 'Night')
await click(page, '#config-confirm')
await sleep(2500)
state = await barState()
check(
  'Rename renames it, and it is still the one running',
  state?.options?.join() === 'Default,Night' && state?.selected === 'Night',
  JSON.stringify({ options: state?.options, selected: state?.selected }),
)

// -- Switch: the other configuration becomes the running one -----------------
// A real `change` event on the select, which is what the card listens for. The
// popup itself is drawn by the OS rather than the page, so a click cannot open
// it -- the event is the contract either way.
await page.selectOption(`#config-${MODULE}`, 'Default')
await sleep(2500)
state = await barState()
check(
  'choosing another configuration switches the module to it',
  state?.selected === 'Default' && state?.config === 'Default',
  JSON.stringify({ selected: state?.selected, config: state?.config }),
)
check(
  'and the card says the automation and the outputs are the same ones',
  (state?.notices ?? []).some(
    (notice) => notice.includes('same automation') && notice.includes('Default'),
  ),
  JSON.stringify(state?.notices),
)

// -- Delete, and the refusal of the last one --------------------------------
await click(page, `#config-delete-${MODULE}`)
await sleep(2500)
state = await barState()
check(
  'Delete drops the configuration the module is not running',
  state?.options?.join() === 'Night',
  JSON.stringify({ options: state?.options, selected: state?.selected }),
)
check(
  'leaving the module running the other one',
  state?.selected === 'Night',
  state?.selected,
)
check(
  'and there is nothing left to delete, so the button is off',
  state?.deleteOff === true,
  JSON.stringify({ deleteOff: state?.deleteOff, notices: state?.notices }),
)
const before = JSON.stringify(state?.options)
await click(page, `#config-delete-${MODULE}`).catch(() => console.log('the button would not take a click'))
await sleep(1200)
state = await barState()
check(
  'and pressing it anyway leaves the last one in place',
  JSON.stringify(state?.options) === before && state?.options?.join() === 'Night',
  JSON.stringify(state?.options),
)

await read(
  (title) => {
    const card = window
      .__deepAll('open-house-hosted-module')
      .find((node) => window.__deepText(node).includes(title))
    card?.scrollIntoView({ block: 'start' })
  },
  TITLE,
)
await sleep(500)
await page.screenshot({ path: `${SHOTS}/card-configurations.png` })
console.log(`the card's bar: ${SHOTS}/card-configurations.png`)

const gone = await command({ type: 'open_house/modules/unhost', module: MODULE })
console.log('unhosted:', JSON.stringify(gone).slice(0, 160))
console.log(events.filter((e) => !e.startsWith('NAV')).join('\n'))
console.log()
if (FAILURES.length) {
  console.log(`${FAILURES.length} check(s) failed:`)
  for (const line of FAILURES) console.log(`  - ${line}`)
} else {
  console.log('every check passed')
}
await browser.close()
process.exit(FAILURES.length ? 1 : 0)
