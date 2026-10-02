// Scratch: does the tab actually switch once the page stops settling?
//
// HA's own frontend reloads the document once, about five seconds after a page
// load, when `hassTokens` has been injected rather than produced by its login
// flow -- the stock dashboard does it too, so it is not a panel behaviour and
// the harness has to sit through it. Everything here happens after `boots`
// reaches 2 and the document has been still for a few seconds.
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'
const local = (r) => fileURLToPath(new URL(r, import.meta.url))
const BASE = process.env.HA_BASE_URL ?? 'http://localhost:8123'
const tokens = readFileSync(local('../../.local/ha_tokens.json'), 'utf8')
const CHROME = process.env.CHROME_PATH ?? `${process.env.LOCALAPPDATA}/ms-playwright/chromium-1234/chrome-win64/chrome.exe`
const DEEP = `
  window.__deepAll = (s, r = document) => {
    const f = [...r.querySelectorAll(s)]
    for (const h of r.querySelectorAll('*')) if (h.shadowRoot) f.push(...window.__deepAll(s, h.shadowRoot))
    return f
  }
  window.__boots = Number(sessionStorage.getItem('probeBoots') ?? 0) + 1
  sessionStorage.setItem('probeBoots', String(window.__boots))
`
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const browser = await chromium.launch({ executablePath: CHROME })
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
let lastNav = 0
page.on('framenavigated', (f) => { if (f === page.mainFrame()) { lastNav = Date.now(); console.log('NAV ->', f.url()) } })
page.on('pageerror', (e) => console.log('PAGEERROR', String(e).slice(0, 300)))
await page.addInitScript((b) => { window.localStorage.setItem('hassTokens', b) }, tokens)
await page.addInitScript(DEEP)
await page.goto(`${BASE}/open-house`, { waitUntil: 'domcontentloaded' })
await page.waitForFunction(() => window.__deepAll('open-house-panel').length > 0, { timeout: 60000 })
// Sit through HA's one-shot reload and any settling.
for (;;) {
  await sleep(1000)
  const quiet = Date.now() - lastNav > 4000
  const boots = await page.evaluate(() => window.__boots)
  if (quiet && boots >= 2) break
}
console.log('settled at boots=', await page.evaluate(() => window.__boots))
const read = () => page.evaluate(() => ({
  boots: window.__boots,
  active: window.__deepAll('open-house-panel')[0]?.activeTab,
  panel: window.__deepAll('[role="tabpanel"]')[0]?.id,
}))
console.log('before:', JSON.stringify(await read()))
const box = await page.evaluate(() => { const r = window.__deepAll('#tab-rooms')[0].getBoundingClientRect(); return { x: r.x + r.width/2, y: r.y + r.height/2 } })
await page.mouse.click(box.x, box.y)
for (const ms of [300, 900, 1500, 2500, 5000]) { await sleep(ms); console.log(`+${ms}ms:`, JSON.stringify(await read())) }
await browser.close()
