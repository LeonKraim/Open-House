// Scratch: does the part write land? Drive the card's own select, and if the
// record does not move, call the ws directly with the page's real revision, so a
// panel-side no-op and a server-side refusal are told apart.
import { openPanel, BASE, sleep } from './_ui.mjs'

const SLUG = 'dynamic_lighting_for_better_sleep_v222_testroom'
const { browser, page } = await openPanel()
const readAll = (fn, arg = null) => page.evaluate(fn, arg)

await page.goto(`${BASE}/open-house?open_house_tab=rooms&open_house_room=testroom`, {
  waitUntil: 'domcontentloaded',
})
await page.waitForFunction(() => window.__deepAll('open-house-hosted-module').length > 0, undefined, {
  timeout: 40000,
})
await sleep(2500)

const record = () =>
  readAll(
    (slug) =>
      window.__deepAll('open-house-panel')[0].hass
        .callWS({ type: 'open_house/modules/hosted' })
        .then((all) => (all.modules ?? []).find((m) => m.slug === slug)?.slots ?? null),
    SLUG,
  )

console.log(`before: ${JSON.stringify(await record())}`)

// -- What the card itself is holding, and what the page's revision is --------
const state = await readAll(() => {
  const card = window.__deepAll('open-house-hosted-module').find((c) =>
    String(c.module?.slug ?? '').includes('v222'),
  )
  const room = window.__deepAll('open-house-room-settings')[0]
  return {
    revision: card?.revision,
    roomRevision: room?.revision,
    busy: card?.busy,
    stale: card?.stale,
    error: card?.error,
    notice: card?.notice,
    admin: card?.admin,
  }
})
console.log(`card state: ${JSON.stringify(state)}`)

// -- Drive the select the way the card's own handler expects -----------------
const fired = await readAll(() => {
  const card = window.__deepAll('open-house-hosted-module').find((c) =>
    String(c.module?.slug ?? '').includes('v222'),
  )
  const select = window.__deepAll('[data-slot-part]', card)[0]
  if (!select) return 'no select'
  if (select.disabled) return 'select disabled'
  select.value = 'lightmodule'
  select.dispatchEvent(new Event('change', { bubbles: true }))
  return 'dispatched'
})
console.log(`select: ${fired}`)
await sleep(6000)
console.log(`card after: ${JSON.stringify(await readAll(() => {
  const card = window.__deepAll('open-house-hosted-module').find((c) =>
    String(c.module?.slug ?? '').includes('v222'),
  )
  return { busy: card?.busy, error: card?.error, notice: card?.notice, part: card?.module?.slots?.[0]?.part }
}))}`)
console.log(`record after: ${JSON.stringify(await record())}`)

// -- The same write, straight down the socket, with the page's real revision --
const direct = await readAll(
  ([slug, revision]) =>
    window.__deepAll('open-house-panel')[0].hass
      .callWS({
        type: 'open_house/modules/settings',
        module: slug,
        bindings: {
          lights_entities: { kind: 'slot', slot: 'light_group', scope: 'room', part: 'lightmodule' },
        },
        revision,
      })
      .then(
        (r) => `OK ${JSON.stringify((r.modules ?? []).find((m) => m.slug === slug)?.slots)}`,
        (e) => `ERR ${e?.message ?? e}`,
      ),
  [SLUG, state.revision],
)
console.log(`direct: ${direct}`)
console.log(`record final: ${JSON.stringify(await record())}`)

await browser.close()
