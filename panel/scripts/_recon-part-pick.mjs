// Scratch recon: the "Part" control on a module's slot row -- what it offers.
import { openPanel, BASE, sleep } from './_ui.mjs'

const { browser, page } = await openPanel()
await page.goto(`${BASE}/open-house?open_house_tab=rooms&open_house_room=kitchen`, {
  waitUntil: 'domcontentloaded',
})
await sleep(4000)

const found = await page.evaluate(() => {
  const out = []
  for (const el of window.__deepAll('[data-slot-part]')) {
    // The card this row belongs to: the nearest ancestor that names a module.
    let title = null
    for (let n = el; n && !title; n = n.parentElement) {
      const head = window.__deepAll('h3, h2, .title, strong', n)[0]
      if (head) title = window.__deepText(head).replace(/\s+/g, ' ').trim().slice(0, 70)
    }
    out.push({
      title,
      slot: el.getAttribute('data-slot-part'),
      value: el.value,
      options: [...el.options].map((o) => ({ value: o.value, label: o.textContent.trim() })),
    })
  }
  return out
})
for (const row of found) {
  console.log(
    `${row.title}  [slot ${row.slot}] value=${JSON.stringify(row.value)} ` +
      `options=${JSON.stringify(row.options)}`,
  )
}
await browser.close()
