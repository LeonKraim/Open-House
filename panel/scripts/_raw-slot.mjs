import { openPanel } from './_ui.mjs'
const { browser, page } = await openPanel()
const listing = await page.evaluate(() =>
  window.__deepAll('open-house-panel')[0].hass.callWS({ type: 'open_house/modules/hosted' }),
)
const mod = (listing.modules ?? []).find((m) => m.slug === 'two_rooms_bedroom')
console.log(JSON.stringify(mod?.slots ?? 'no slots', null, 1).slice(0, 2200))
await browser.close()
