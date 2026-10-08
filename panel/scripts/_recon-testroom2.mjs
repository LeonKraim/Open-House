// Scratch recon: every slot control on the testroom page, not only inside a card.
import { openPanel, BASE, sleep } from './_ui.mjs'

const { browser, page, events } = await openPanel()
await page.setViewportSize({ width: 1440, height: 1200 })
const readAll = (fn, arg = null) => page.evaluate(fn, arg)

await page.goto(`${BASE}/open-house?open_house_tab=rooms&open_house_room=testroom`, {
  waitUntil: 'domcontentloaded',
})
await sleep(6000)

const shape = await readAll(() => {
  const page1 = window.__deepAll('[data-slot-rule]').map((el) => {
    let line = el
    for (let n = el; n; n = n.parentElement) {
      if (window.__deepAll('code', n).length > 0) {
        line = n
        break
      }
    }
    const slot = el.getAttribute('data-slot-rule')
    const part = window.__deepAll(`[data-slot-part="${slot}"]`)[0]
    return {
      slot,
      kind: el.value,
      line: window.__deepText(line).replace(/\s+/g, ' ').trim().slice(0, 150),
      part: part
        ? { value: part.value, options: [...part.options].map((o) => `${o.value}|${o.textContent.trim()}`) }
        : null,
    }
  })
  const parts = window.__deepAll('[data-slot-part]').map((s) => ({
    slot: s.getAttribute('data-slot-part'),
    value: s.value,
    options: [...s.options].map((o) => `${o.value}|${o.textContent.trim()}`),
  }))
  const headings = window.__deepAll('h2, h3, h4').map((h) =>
    window.__deepText(h).replace(/\s+/g, ' ').trim().slice(0, 80),
  )
  return { slotRows: page1, parts, headings, url: window.location.search }
})

console.log(`url ${shape.url}`)
console.log(`\nheadings: ${JSON.stringify([...new Set(shape.headings)])}`)
console.log(`\nslot rows: ${shape.slotRows.length}`)
for (const r of shape.slotRows) {
  console.log(`   ${r.slot} kind=${JSON.stringify(r.kind)} part=${JSON.stringify(r.part)}\n      ${r.line}`)
}
console.log(`\npart selects: ${JSON.stringify(shape.parts, null, 1)}`)

await page.screenshot({ path: 'S:/OS Folders/Desktop/HAOS_Open_House/shots/testroom-page-full.png', fullPage: true })
console.log('\nwrote shots/testroom-page-full.png')
for (const event of events) console.log(`  ${event}`)
await browser.close()
