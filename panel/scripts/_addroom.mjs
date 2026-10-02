// Scratch: does "Add room" open its form, and does a room name open settings?
import { openPanel, text, click, sleep } from './_ui.mjs'
const { browser, page, events } = await openPanel()
try {
  await click(page, '#tab-rooms')
  await click(page, 'open-house-tab-rooms button', { nth: 'Add room' })
  await sleep(600)
  console.log('AFTER ADD ROOM:', await text(page, 'open-house-tab-rooms'))
  console.log('cancel:', await (async () => { await click(page, 'open-house-tab-rooms button', { nth: 'Cancel' }); return text(page, 'open-house-tab-rooms') })())

  // Open the first room by clicking its name, the way a person would.
  await click(page, 'open-house-tab-rooms tbody tr a')
  await sleep(1500)
  console.log('AFTER ROOM CLICK:', (await text(page, 'open-house-room-settings'))?.slice(0, 400) ?? 'NO SETTINGS ELEMENT')
  console.log('EVENTS:', events.slice(-4))
} finally { await browser.close() }
