// Scratch: what the live house actually offers a parts walk, before writing one.
//
// The parts block is drawn per slot row, and which rows a page has depends on the
// house: a room's page lists the roles its own modules reach, and the House tab
// lists only the roles a *house-eligible* module reaches. A walk that hardcoded a
// role would be a walk that reports the house's shape as the code's fault. This
// prints the shape instead.
//
// Every page-side function spells its selectors out in full: a closure over a
// Node-side constant is not there in the browser, and `SCREEN` written inside a
// `page.evaluate` is a ReferenceError that reads like a broken page.
import { openPanel, click, sleep } from './_ui.mjs'

const { browser, page, events } = await openPanel()
const readAll = (fn, arg = null) => page.evaluate(fn, arg)
const waitFor = (fn, arg = null, ms = 90000) =>
  page.waitForFunction(fn, arg, { timeout: ms, polling: 500 })

const shape = () =>
  readAll(() => {
    const tables = window.__deepAll('table').map((t) => ({
      head: window.__deepText(t.querySelector('thead') ?? t).replace(/\s+/g, ' ').trim().slice(0, 60),
      rows: window.__deepAll('tbody tr', t).map((r) =>
        window.__deepText(r).replace(/\s+/g, ' ').trim().slice(0, 70),
      ),
    }))
    return {
      anchors: window.__deepAll('[data-add-part]').map((a) => a.getAttribute('data-add-part')),
      parts: window.__deepAll('[data-slot-part-row]').map((a) => a.getAttribute('data-slot-part-row')),
      tables,
    }
  })

try {
  await click(page, '#tab-rooms', { wait: 2000 })
  const rooms = await readAll(() =>
    window.__deepAll('open-house-tab-rooms tbody tr').map((r) =>
      window.__deepText(r).replace(/\s+/g, ' ').trim().slice(0, 80),
    ),
  )
  console.log('ROOMS:\n  ' + rooms.join('\n  '))

  await waitFor(() => {
    const link = window.__deepAll('open-house-tab-rooms tbody tr a')[0]
    if (!link) return false
    link.scrollIntoView({ block: 'center' })
    const b = link.getBoundingClientRect()
    window.__at = { x: b.x + b.width / 2, y: b.y + b.height / 2 }
    return true
  })
  const at = await readAll(() => window.__at)
  await page.mouse.click(at.x, at.y)
  await waitFor(() => {
    const s = window.__deepAll('open-house-room-settings')[0]
    return s && !window.__deepText(s).includes('Reading the room')
  })
  const room = await shape()
  console.log('\nFIRST ROOM PAGE:')
  console.log(`  anchors: ${JSON.stringify(room.anchors)}  parts: ${JSON.stringify(room.parts)}`)
  for (const t of room.tables) console.log(`  table [${t.head}] rows: ${JSON.stringify(t.rows)}`)
  console.log(
    '  words: ' +
      (await readAll(() =>
        window
          .__deepText(window.__deepAll('open-house-room-settings')[0])
          .replace(/\s+/g, ' ')
          .trim()
          .slice(0, 500),
      )),
  )

  await click(page, '#tab-house', { wait: 3000 })
  const house = await shape()
  console.log('\nHOUSE TAB:')
  console.log(`  anchors: ${JSON.stringify(house.anchors)}  parts: ${JSON.stringify(house.parts)}`)
  for (const t of house.tables) console.log(`  table [${t.head}] rows: ${JSON.stringify(t.rows)}`)

  const scope = await readAll(() => {
    const panel = window.__deepAll('open-house-panel')[0]
    return panel.hass.callWS({ type: 'open_house/house/scope' }).then((s) => ({
      slots: s.slots.map(
        (r) => `${r.slot}=${r.label} (${r.entity_id ?? 'unbound'}) parts=${(r.parts ?? []).length}`,
      ),
      modules: s.modules.map((m) => `${m.pack}@${m.room ?? '-'}`),
    }))
  })
  console.log(`  scope slots: ${JSON.stringify(scope.slots, null, 0)}`)
  console.log(`  scope modules: ${JSON.stringify(scope.modules)}`)
} catch (error) {
  console.log('RECON FAILED: ' + String(error).slice(0, 400))
}
if (events.length) console.log('EVENTS: ' + events.slice(0, 8).join(' | '))
await sleep(200)
await browser.close()
