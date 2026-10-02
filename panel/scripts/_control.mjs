// Scratch control: does the document reload with NO interaction at all?
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'
const local = (r) => fileURLToPath(new URL(r, import.meta.url))
const BASE = process.env.HA_BASE_URL ?? 'http://localhost:8123'
const tokens = readFileSync(local('../../.local/ha_tokens.json'), 'utf8')
const CHROME = process.env.CHROME_PATH ?? `${process.env.LOCALAPPDATA}/ms-playwright/chromium-1234/chrome-win64/chrome.exe`
const DEEP = `
  window.__boots = Number(sessionStorage.getItem('probeBoots') ?? 0) + 1
  sessionStorage.setItem('probeBoots', String(window.__boots))
`
const browser = await chromium.launch({ executablePath: CHROME })
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
page.on('framenavigated', (f) => { if (f === page.mainFrame()) console.log('NAV ->', f.url()) })
page.on('console', (m) => console.log('CONSOLE', m.type(), m.text().slice(0, 200)))
page.on('request', (r) => { if (r.url().includes('/auth/')) console.log('REQ', r.method(), r.url()) })
page.on('response', (r) => { if (r.url().includes('/auth/')) console.log('RES', r.status(), r.url()) })
await page.addInitScript((b) => { window.localStorage.setItem('hassTokens', b) }, tokens)
await page.addInitScript(DEEP)
await page.goto(`${BASE}/open-house`, { waitUntil: 'domcontentloaded' })
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
for (const ms of [2000, 3000, 3000, 3000, 5000, 5000]) {
  await sleep(ms)
  console.log(`+${ms}ms boots=`, await page.evaluate(() => ({ boots: window.__boots, url: location.href })))
}
await browser.close()
