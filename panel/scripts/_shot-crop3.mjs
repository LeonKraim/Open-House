import { openPanel, BASE, sleep } from './_ui.mjs'
const { browser, page, events } = await openPanel()
await page.setViewportSize({ width: 1440, height: 900 })
await page.goto(`${BASE}/open-house?open_house_tab=rooms&open_house_room=testroom`, {
  waitUntil: 'domcontentloaded',
})
await sleep(6000)
const OUT = 'S:/OS Folders/Desktop/HAOS_Open_House/shots'
const info = await page.evaluate(() => {
  const rows = window.__deepAll('.stack, tbody tr, .slot')
  const hit = rows.find((n) => {
    const t = window.__deepText(n).replace(/\s+/g, ' ').trim()
    return /^Light group/.test(t) && t.length < 400
  })
  if (!hit) return { found: false }
  hit.scrollIntoView({ block: 'start' })
  return { found: true, text: window.__deepText(hit).replace(/\s+/g, ' ').trim().slice(0, 400) }
})
console.log(JSON.stringify(info, null, 1))
await sleep(800)
await page.screenshot({ path: `${OUT}/testroom-lightgroup.png` })
console.log(`wrote ${OUT}/testroom-lightgroup.png`)
for (const event of events) console.log(`  ${event}`)
await browser.close()
