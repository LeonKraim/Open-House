// Scratch: how does the devices dashboard let you select rows?
import { openPanel, sleep } from './_ui.mjs'
const { browser, page } = await openPanel()
try {
  await page.goto('http://localhost:8123/config/devices/dashboard', { waitUntil: 'domcontentloaded' })
  await sleep(10000)
  const dump = await page.evaluate(() => {
    const list = window.__deepAll('ha-grouped-list')[0]
    const rows = list ? [...list.querySelectorAll('*')].filter((e) => e.tagName.toLowerCase().startsWith('ha-') && window.__deepText(e).includes('demo')) : []
    return {
      listPresent: !!list,
      listText: list ? window.__deepText(list).replace(/\s+/g,' ').trim().slice(0, 400) : null,
      rowTags: [...new Set(rows.map((e) => e.tagName.toLowerCase()))],
      anyCheckbox: window.__deepAll('input, ha-checkbox, wa-checkbox, [role=checkbox]').length,
      bodyText: window.__deepText(document.body).replace(/\s+/g,' ').trim().slice(0, 700),
    }
  })
  console.log(JSON.stringify(dump, null, 1))
} finally { await browser.close() }
