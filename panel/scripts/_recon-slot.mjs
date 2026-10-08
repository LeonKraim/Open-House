// Scratch recon: what a room's page actually carries for its slot rows.
import { openPanel, BASE, sleep } from './_ui.mjs'

const { browser, page, events } = await openPanel()
const readAll = (fn, arg = null) => page.evaluate(fn, arg)

for (const room of ['kitchen', 'bedroom']) {
  await page.goto(`${BASE}/open-house?open_house_tab=rooms&open_house_room=${room}`, {
    waitUntil: 'domcontentloaded',
  })
  await sleep(3500)
  const shape = await readAll(() => {
    const rows = window.__deepAll('[data-slot-rule]').map((el) => {
      // The smallest ancestor that holds both this select and a device line.
      let line = el
      for (let n = el; n; n = n.parentElement) {
        if (window.__deepAll('code', n).length > 0) {
          line = n
          break
        }
      }
      // Walk up from there looking for the thing that names the module.
      let title = null
      for (let n = line; n && !title; n = n.parentElement) {
        const head = window.__deepAll('h2, h3, h4, .title, strong', n)[0]
        if (head) title = window.__deepText(head).replace(/\s+/g, ' ').trim().slice(0, 60)
      }
      return {
        slot: el.getAttribute('data-slot-rule'),
        kind: el.value,
        line: window.__deepText(line).replace(/\s+/g, ' ').trim().slice(0, 110),
        codes: window.__deepAll('code', line).map((c) => c.textContent.trim()),
        title,
      }
    })
    return {
      rules: rows,
      roomSelect: window.__deepAll('[data-room], #open-house-room, select[data-room-pick]').length,
      url: window.location.search,
    }
  })
  console.log(`\n== room ${room}  ${JSON.stringify(shape.url)}`)
  for (const row of shape.rules) {
    console.log(
      `  ${row.slot} kind=${JSON.stringify(row.kind)} codes=${JSON.stringify(row.codes)} ` +
        `title=${JSON.stringify(row.title)}\n      ${row.line}`,
    )
  }
}

for (const event of events) console.log(`  ${event}`)
await browser.close()
