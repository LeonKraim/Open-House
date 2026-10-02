// Scratch: shared settle helper + a rooms-table dump.
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
  window.__deepText = (n) => {
    if (n.nodeType === 3) return n.textContent ?? ''
    if (n.nodeType === 1 && ['STYLE','SCRIPT'].includes(n.tagName)) return ''
    return [...(n.shadowRoot ?? n).childNodes].map(window.__deepText).join(' ')
  }
  window.__boots = Number(sessionStorage.getItem('probeBoots') ?? 0) + 1
  sessionStorage.setItem('probeBoots', String(window.__boots))
`
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const browser = await chromium.launch({ executablePath: CHROME })
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
let lastNav = 0
page.on('framenavigated', (f) => { if (f === page.mainFrame()) lastNav = Date.now() })
page.on('pageerror', (e) => console.log('PAGEERROR', String(e).slice(0, 300)))
await page.addInitScript((b) => { window.localStorage.setItem('hassTokens', b) }, tokens)
await page.addInitScript(DEEP)
await page.goto(`${BASE}/open-house`, { waitUntil: 'domcontentloaded' })
await page.waitForFunction(() => window.__deepAll('open-house-panel').length > 0, { timeout: 60000 })
for (;;) { await sleep(1000); if (Date.now() - lastNav > 4000 && (await page.evaluate(() => window.__boots)) >= 2) break }

const clickTab = async (id) => {
  const b = await page.evaluate((i) => { const r = window.__deepAll(`#tab-${i}`)[0].getBoundingClientRect(); return { x: r.x + r.width/2, y: r.y + r.height/2 } }, id)
  await page.mouse.click(b.x, b.y)
  await sleep(1200)
}
await clickTab('rooms')
const rooms = await page.evaluate(() => {
  const host = window.__deepAll('open-house-tab-rooms')[0]
  const rows = window.__deepAll('open-house-tab-rooms tbody tr')
  const inputs = window.__deepAll('open-house-tab-rooms input, open-house-tab-rooms select')
  return {
    table: rows.map((r) => window.__deepText(r).replace(/\s+/g, ' ').trim()),
    fields: inputs.map((i) => `${i.tagName}:${i.type ?? ''}:${i.name ?? i.id ?? ''}:${i.placeholder ?? ''}`),
    buttons: window.__deepAll('open-house-tab-rooms button').map((b) => (b.textContent ?? '').trim()).filter(Boolean),
    text: window.__deepText(host).replace(/\s+/g, ' ').trim().slice(0, 1200),
  }
})
console.log('ROOM ROWS:'); rooms.table.forEach((r) => console.log('  ', r))
console.log('FIELDS:', JSON.stringify(rooms.fields))
console.log('BUTTONS:', JSON.stringify(rooms.buttons))
console.log('TEXT:', rooms.text)
await browser.close()
