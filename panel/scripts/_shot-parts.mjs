// Scratch: a picture of the Part control on the slot rows.
import { mkdirSync } from 'node:fs'
import { openPanel, BASE, sleep } from './_ui.mjs'

const OUT = 'S:/OS Folders/Desktop/HAOS_Open_House/shots'
mkdirSync(OUT, { recursive: true })
const { browser, page } = await openPanel()
await page.setViewportSize({ width: 1440, height: 1100 })
await page.goto(`${BASE}/open-house?open_house_tab=rooms&open_house_room=kitchen`, {
  waitUntil: 'domcontentloaded',
})
await sleep(4500)

const box = await page.evaluate(() => {
  const parts = window.__deepAll('[data-slot-part="light_group"]')
  const pick =
    parts.find((el) => {
      for (let n = el; n; n = n.parentElement) {
        const head = window.__deepAll('h3, h2, .title, strong', n)[0]
        if (head) return window.__deepText(head).includes('Dynamic Lighting for Better Sleep')
      }
      return false
    }) ?? parts[0]
  if (!pick) return null
  // Climb to the *card*: the first ancestor that names the module, so the
  // picture says which module the rows belong to.
  let card = pick
  for (let n = pick; n; n = n.parentElement) {
    const head = window.__deepAll('h3, h2, .title, strong', n)[0]
    if (head) {
      card = n
      break
    }
  }
  pick.scrollIntoView({ block: 'center' })
  const r = card.getBoundingClientRect()
  const top = Math.max(0, r.y - 10)
  return {
    x: Math.max(0, r.x - 10),
    y: top,
    width: Math.min(1400, r.width + 20),
    height: Math.min(1050 - top, r.height + 20),
  }
})
if (box) {
  await page.screenshot({ path: `${OUT}/slot-rows.png`, clip: box })
  console.log(`wrote ${OUT}/slot-rows.png  ${JSON.stringify(box)}`)
}
await page.screenshot({ path: `${OUT}/rooms-tab.png` })
console.log(`wrote ${OUT}/rooms-tab.png`)
await browser.close()
