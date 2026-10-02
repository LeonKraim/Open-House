// Scratch: create one room and dump whatever the panel says about it.
import { openPanel, text, click, type, sleep } from './_ui.mjs'
const [name = 'Bathroom', roomType = 'bathroom'] = process.argv.slice(2)
const { browser, page, events } = await openPanel()
try {
  await click(page, '#tab-rooms')
  await click(page, 'open-house-tab-rooms button', { nth: 'Add room' })
  await sleep(500)
  await type(page, '[placeholder="Kitchen"]', name)
  await type(page, '[placeholder="kitchen"]', roomType)
  const filled = await page.evaluate(() => window.__deepAll('open-house-tab-rooms input').map((i) => i.value))
  console.log('FILLED:', JSON.stringify(filled))
  await click(page, 'open-house-tab-rooms button', { nth: 'Create room' })
  await sleep(3000)
  console.log('AFTER CREATE:', await text(page, 'open-house-tab-rooms'))
  console.log('BANNER:', await text(page, 'open-house-tab-rooms .banner'))
  console.log('SETTINGS?', await page.evaluate(() => window.__deepAll('open-house-room-settings').length))
  console.log('EVENTS:', events.slice(-6))
} finally { await browser.close() }
