import { openPanel, BASE, sleep } from './_ui.mjs'
const { browser, page, events } = await openPanel()
await page.setViewportSize({ width: 1440, height: 900 })
await page.goto(`${BASE}/open-house?open_house_tab=rooms&open_house_room=testroom`, {
  waitUntil: 'domcontentloaded',
})
await sleep(6000)
const OUT = 'S:/OS Folders/Desktop/HAOS_Open_House/shots'
const info = await page.evaluate(() => {
  const devHead = window.__deepAll('h2, h3').find((h) => window.__deepText(h).trim() === 'Devices')
  if (!devHead) return { found: false, why: 'no Devices heading' }
  // The section that holds it.
  let section = devHead
  for (; section.parentElement; section = section.parentElement) {
    if (section.getBoundingClientRect().height > 300) break
  }
  const blocks = window.__deepAll('.stack', section)
  const hit = blocks.find((n) =>
    /^Light group/.test(window.__deepText(n).replace(/\s+/g, ' ').trim()),
  )
  if (!hit) return { found: false, why: 'no Light group row in Devices', blocks: blocks.length }
  hit.scrollIntoView({ block: 'center' })
  return {
    found: true,
    text: window.__deepText(hit).replace(/\s+/g, ' ').trim().slice(0, 500),
  }
})
console.log(JSON.stringify(info, null, 1))
await sleep(800)
await page.screenshot({ path: `${OUT}/testroom-lightgroup.png` })
console.log(`wrote ${OUT}/testroom-lightgroup.png`)
for (const event of events) console.log(`  ${event}`)
await browser.close()
