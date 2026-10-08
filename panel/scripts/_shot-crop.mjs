import { openPanel, BASE, sleep } from './_ui.mjs'
const { browser, page, events } = await openPanel()
await page.setViewportSize({ width: 1440, height: 1200 })
await page.goto(`${BASE}/open-house?open_house_tab=rooms&open_house_room=testroom`, {
  waitUntil: 'domcontentloaded',
})
await sleep(6000)
const OUT = 'S:/OS Folders/Desktop/HAOS_Open_House/shots'
// The whole-house slots table, and the room's own devices table.
const boxes = await page.evaluate(() => {
  const out = {}
  const pick = (text) =>
    window.__deepAll('h2, h3').find((h) => window.__deepText(h).trim().startsWith(text)) ?? null
  for (const [key, label] of [
    ['house', 'Whole house'],
    ['devices', 'Devices'],
    ['modules', 'Modules'],
  ]) {
    const head = pick(label)
    if (!head) continue
    let n = head
    for (; n.parentElement; n = n.parentElement) {
      if (n.getBoundingClientRect().height > 150) break
    }
    n.scrollIntoView({ block: 'start' })
    const r = n.getBoundingClientRect()
    out[key] = { x: Math.max(0, r.x - 8), y: Math.max(0, r.y - 6), width: Math.min(1420, r.width + 12), height: Math.min(1180, r.height + 12) }
  }
  return out
})
console.log(JSON.stringify(boxes, null, 1))
for (const [key, box] of Object.entries(boxes)) {
  await page.screenshot({ path: `${OUT}/testroom-${key}.png`, clip: box })
  console.log(`wrote ${OUT}/testroom-${key}.png`)
}
for (const event of events) console.log(`  ${event}`)
await browser.close()
