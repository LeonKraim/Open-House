// Scratch: what the Node-RED cast actually looks like, as an image.
//
// The walk in `_host.mjs` reads the same rows and prints them as text, which is
// the right shape for a check and the wrong shape for a person asking to see the
// screen. This drives the same two screens the same way -- real clicks, real
// form events -- and keeps the pixels.
//
// Run: node scripts/_shot.mjs   (writes into SHOTS, printed at the end)
import { mkdirSync } from 'node:fs'
import { openPanel, click, sleep } from './_ui.mjs'

const SCREEN = 'open-house-host-module'
const FORM = `${SCREEN} ha-form`
const SHOTS = `${process.env.TEMP ?? '/tmp'}/open-house-shots`
// A device the mock fleet really announces, so the row is answered with
// something the house has rather than a name invented for the picture.
const LIGHT = 'light.open_house_mock_fleet_minimal_living_room'

const { browser, page, events } = await openPanel()
const read = (fn, arg = null) => page.evaluate(fn, arg)
const waitFor = (fn, arg = null, ms = 90000) =>
  page.waitForFunction(fn, arg, { timeout: ms, polling: 500 })

mkdirSync(SHOTS, { recursive: true })

/** Photograph one element, whole, by scrolling it into view and clipping to it. */
const shoot = async (selector, file) => {
  const box = await read((sel) => {
    const node = window.__deepAll(sel)[0]
    if (!node) return null
    node.scrollIntoView({ block: 'center' })
    const r = node.getBoundingClientRect()
    return { x: r.x, y: r.y, width: r.width, height: r.height }
  }, selector)
  if (!box) throw new Error(`nothing matched ${selector}`)
  await sleep(400)
  const settled = await read((sel) => {
    const r = window.__deepAll(sel)[0].getBoundingClientRect()
    return { x: r.x, y: r.y, width: r.width, height: r.height }
  }, selector)
  await page.screenshot({
    path: `${SHOTS}/${file}`,
    clip: {
      x: Math.max(0, settled.x - 8),
      y: Math.max(0, settled.y - 8),
      width: settled.width + 16,
      height: settled.height + 16,
    },
  })
  return `${SHOTS}/${file}`
}

/** Answer a row's own field, then pick the flow in its menu -- what a person does. */
const cast = async (index, value, mode) => {
  await read(
    ([at, worth, how]) => {
      const form = window.__deepAll(`ha-form[data-input="${at}"]`)[0]
      if (!form) return
      form.dispatchEvent(
        new CustomEvent('value-changed', {
          detail: { value: { ...form.data, [`value_${at}`]: worth } },
          bubbles: true,
          composed: true,
        }),
      )
      if (!how) return
      const again = window.__deepAll(`ha-form[data-input="${at}"]`)[0]
      again?.dispatchEvent(
        new CustomEvent('value-changed', {
          detail: { value: { ...again.data, [`cast_mode_${at}`]: how } },
          bubbles: true,
          composed: true,
        }),
      )
    },
    [index, value, mode],
  )
}

// -- the import screen, where the cast is chosen ---------------------------
await waitFor(() => window.__deepAll('#tab-dev').length > 0)
await click(page, '#tab-dev', { wait: 1200 })
await click(page, '.tabs .tab', { nth: 'Import as a module', wait: 1200 })
await click(page, '.tab', { nth: 'Blueprint', wait: 800, within: SCREEN })
await waitFor(() => window.__deepAll('#host-blueprint').length > 0)
await read(() => {
  const select = window.__deepAll('#host-blueprint')[0]
  select.value = 'MarqBarq/dynamic-lighting.yaml'
  select.dispatchEvent(new Event('change', { bubbles: true }))
})
await sleep(400)
await click(page, 'button', { nth: 'Read this source', wait: 2500 })
await waitFor((sel) => window.__deepAll(sel).length > 0, FORM)
await sleep(900)

// The row whose own answer is one device -- the case a flow can be wired on.
// Read off the row's own `data-input`, not off its position in the list: the
// list holds the publishing section's rows too, so a position is not the number
// this row's form is addressed by.
const index = await read(() => {
  const row = window
    .__deepAll('[data-input]')
    .find((node) =>
      /Bypass Light/i.test(window.__deepText(node).replace(/\s+/g, ' ')),
    )
  return row ? Number(row.getAttribute('data-input')) : -1
})
if (index < 0) throw new Error('the bypass light row was not drawn')
await cast(index, LIGHT, null)
await sleep(700)
await cast(index, null, 'nodered')
// Long enough for the embedded editor to come up: the frame is Node-RED's whole
// frontend, which is a real page load rather than a repaint of this one.
await sleep(6000)

const importShot = await shoot(`[data-input="${index}"]`, 'import-screen.png')
console.log(`import screen: ${importShot}`)
console.log(
  await read(
    (at) =>
      window
        .__deepText(window.__deepAll(`[data-input="${at}"]`)[0])
        .replace(/\s+/g, ' ')
        .trim(),
    index,
  ),
)

// The editor itself, as the viewport sees it: the embed is taller than a clipped
// region is worth, and what a person is asking to see is the Node-RED canvas
// with this row's flow name on it. (No full-page shot since the embed landed:
// the page is now a 68vh frame of another application, and Chromium refuses the
// whole-page write at that size.)
await read(() => {
  window.__deepAll('[data-node-red]')[0]?.scrollIntoView({ block: 'start' })
})
await sleep(1500)
await page.screenshot({ path: `${SHOTS}/embedded-editor.png` })
console.log(`the embedded editor: ${SHOTS}/embedded-editor.png`)
console.log(
  'the frame is pointed at:',
  await read(
    () => window.__deepAll('iframe')[0]?.getAttribute('src') ?? null,
  ),
)
console.log(events.filter((e) => !e.startsWith('NAV')).join('\n'))
await browser.close()
