// Scratch: what does HA's areas dashboard render, once it has settled?
import { openPanel, sleep } from './_ui.mjs'
const { browser, page } = await openPanel()
try {
  await page.goto('http://localhost:8123/config/areas/dashboard', { waitUntil: 'domcontentloaded' })
  await page.waitForFunction(() => window.__deepAll('ha-config-areas-dashboard').length > 0, { timeout: 30000 })
  await sleep(8000)
  const dump = await page.evaluate(() => {
    const dash = window.__deepAll('ha-config-areas-dashboard')[0]
    return {
      text: window.__deepText(dash).replace(/\s+/g, ' ').trim().slice(0, 1000),
      links: window.__deepAll('a').map((a) => `${a.getAttribute('href')} :: ${window.__deepText(a).replace(/\s+/g,' ').trim().slice(0,40)}`).filter((s) => s.includes('/config/areas')),
      items: window.__deepAll('ha-list-item-button').map((i) => window.__deepText(i).replace(/\s+/g,' ').trim()),
      buttons: window.__deepAll('ha-config-areas-dashboard button, ha-config-areas-dashboard ha-button').map((b) => (b.textContent ?? '').trim()),
    }
  })
  console.log(JSON.stringify(dump, null, 1))
} finally { await browser.close() }
