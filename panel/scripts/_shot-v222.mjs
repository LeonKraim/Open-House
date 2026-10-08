// Scratch: the testroom v222 module's card, with its slot rows.
import { mkdirSync } from 'node:fs'
import { openPanel, BASE, sleep } from './_ui.mjs'

const OUT = 'S:/OS Folders/Desktop/HAOS_Open_House/shots'
mkdirSync(OUT, { recursive: true })
const { browser, page } = await openPanel()
await page.setViewportSize({ width: 1440, height: 1150 })
await page.goto(`${BASE}/open-house?open_house_tab=rooms&open_house_room=testroom`, {
  waitUntil: 'domcontentloaded',
})
await page.waitForFunction(
  () => window.__deepAll('open-house-hosted-module').some((c) => String(c.module?.slug ?? '').includes('v222')),
  undefined,
  { timeout: 40000 },
)
await sleep(2500)

const seen = await page.evaluate(() => {
  const card = window.__deepAll('open-house-hosted-module').find((c) =>
    String(c.module?.slug ?? '').includes('v222'),
  )
  const rows = window.__deepAll('[data-hosted-slot]', card).map((row) => {
    const part = window.__deepAll('[data-slot-part]', row)[0]
    return {
      slot: row.getAttribute('data-hosted-slot'),
      text: window.__deepText(row).replace(/\s+/g, ' ').trim().slice(0, 160),
      part: part
        ? { value: part.value, options: [...part.options].map((o) => `${o.value}|${o.textContent.trim()}`) }
        : null,
    }
  })
  return { rows, slots: JSON.stringify(card?.module?.slots ?? []).slice(0, 600) }
})
console.log(JSON.stringify(seen, null, 1))

await page.evaluate(() => {
  const card = window.__deepAll('open-house-hosted-module').find((c) =>
    String(c.module?.slug ?? '').includes('v222'),
  )
  card.scrollIntoView({ block: 'start' })
})
await sleep(600)
await page.screenshot({ path: `${OUT}/v222-slot-rows.png` })
console.log(`wrote ${OUT}/v222-slot-rows.png`)
await browser.close()
