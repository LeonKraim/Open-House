import { openPanel, BASE, sleep } from './_ui.mjs'
const { browser, page, events } = await openPanel()
await page.setViewportSize({ width: 1440, height: 1200 })
await page.goto(`${BASE}/open-house?open_house_tab=rooms&open_house_room=testroom`, {
  waitUntil: 'domcontentloaded',
})
await sleep(6000)
const OUT = 'S:/OS Folders/Desktop/HAOS_Open_House/shots'
// The Light group row and its parts, in the room's Devices table.
const boxes = await page.evaluate(() => {
  const out = {}
  const rows = window.__deepAll('tbody tr, .slot-row, .stack')
  const find = (label) =>
    window.__deepAll('*').find(
      (n) =>
        n.children.length === 0 &&
        window.__deepText(n).trim() === label &&
        n.getBoundingClientRect().height > 0,
    )
  for (const [key, label] of [
    ['lightgroup', 'Light group'],
    ['lightgroup2', 'Light group'],
  ]) {
    const hits = window
      .__deepAll('*')
      .filter(
        (n) => n.children.length === 0 && window.__deepText(n).trim() === label && n.getBoundingClientRect().height > 0,
      )
    if (!hits.length) continue
    const head = key === 'lightgroup' ? hits[hits.length - 1] : hits[hits.length - 1]
    let n = head
    for (; n.parentElement; n = n.parentElement) {
      const r = n.getBoundingClientRect()
      if (r.height > 90) break
    }
    n.scrollIntoView({ block: 'center' })
    const r = n.getBoundingClientRect()
    out[key] = {
      x: Math.max(0, r.x - 8),
      y: Math.max(0, r.y - 8),
      width: Math.min(1420, r.width + 16),
      height: Math.min(1180, r.height + 16),
    }
    break
  }
  // Every "Light group" label's position, so the crop can be judged.
  out.labels = window
    .__deepAll('*')
    .filter((n) => n.children.length === 0 && window.__deepText(n).trim() === 'Light group')
    .map((n) => {
      const r = n.getBoundingClientRect()
      return { y: Math.round(r.y), h: Math.round(r.height), x: Math.round(r.x) }
    })
  return out
})
console.log(JSON.stringify(boxes, null, 1))
if (boxes.lightgroup) {
  await page.screenshot({ path: `${OUT}/testroom-lightgroup.png`, clip: boxes.lightgroup })
  console.log(`wrote ${OUT}/testroom-lightgroup.png`)
}
for (const event of events) console.log(`  ${event}`)
await browser.close()
