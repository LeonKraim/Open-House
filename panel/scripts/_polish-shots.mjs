// Scratch: photograph the screens this pass is polishing, before and after.
//
// The served bundle at /open_house/panel.js is built from a `dist/` that
// this pass must not touch (`npm run build` is off-limits), so the fresh sources
// are built to a scratch directory and served in place of it for the walk only.
// Nothing shared is written.
//
// Run: node scripts/_polish-shots.mjs <tag>   (writes into SHOTS, printed at the end)
import { execFileSync } from 'node:child_process'
import { readFileSync, mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

const BASE = process.env.HA_BASE_URL ?? 'http://localhost:8123'
const CHROME = process.env.CHROME_PATH ?? `${process.env.LOCALAPPDATA}/ms-playwright/chromium-1234/chrome-win64/chrome.exe`
const HERE = fileURLToPath(new URL('.', import.meta.url))
const PANEL = fileURLToPath(new URL('..', import.meta.url)).replace(/[\\/]$/, '')
const TOKENS = fileURLToPath(new URL('../../.local/ha_tokens.json', import.meta.url))
const SCRATCH = `${process.env.TEMP ?? '/tmp'}/oh-scratch-dist`
const TAG = process.argv[2] ?? 'before'
const SHOTS = `${process.env.TEMP ?? '/tmp'}/open-house-polish`
mkdirSync(SHOTS, { recursive: true })

// Build the sources as they are right now, to the scratch dir.
console.log('building sources to scratch...')
execFileSync(
  process.execPath,
  [`${PANEL}/node_modules/vite/bin/vite.js`, 'build', '--outDir', SCRATCH, '--emptyOutDir'],
  { cwd: PANEL, stdio: 'inherit' },
)
const BUNDLE = readFileSync(`${SCRATCH}/open-house-panel.js`)

const deepScript = `
  window.__deepAll = (s, r = document) => {
    const f = r.matches?.(s) ? [r] : []
    f.push(...r.querySelectorAll(s))
    for (const h of [r, ...r.querySelectorAll('*')]) if (h.shadowRoot) f.push(...window.__deepAll(s, h.shadowRoot))
    return f
  }
  window.__deepText = (n) => {
    if (n.nodeType === 3) return n.textContent ?? ''
    if (n.nodeType === 1 && ['STYLE','SCRIPT'].includes(n.tagName)) return ''
    return [...(n.shadowRoot ?? n).childNodes].map(window.__deepText).join(' ')
  }
`

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const tokens = readFileSync(TOKENS, 'utf8')
const browser = await chromium.launch({ executablePath: CHROME })
// The HA frontend registers a service worker that answers the panel module's
// import from its own cache; page.route never sees that request, so the walk
// would run the *old* served bundle while the log shows one unrelated preload
// being fulfilled. Blocking service workers forces every fetch to the network.
const context = await browser.newContext({
  viewport: { width: 1600, height: 1100 },
  serviceWorkers: 'block',
})
const page = await context.newPage()
const events = []
let lastNav = 0
page.on('framenavigated', (f) => { if (f === page.mainFrame()) { lastNav = Date.now() } })
page.on('pageerror', (e) => events.push(`PAGEERROR ${String(e).slice(0, 200)}`))
page.on('console', (m) => { if (m.type() === 'error') events.push(`CONSOLE ${m.text().slice(0, 200)}`) })
// Serve the freshly built bundle wherever the panel asks for it.
await page.route(/\/open_house\/panel\.js/, (route) => {
  console.log('intercepted panel module:', route.request().url().slice(0, 90))
  return route.fulfill({ status: 200, contentType: 'text/javascript', body: BUNDLE })
})
await page.addInitScript((b) => { window.localStorage.setItem('hassTokens', b) }, tokens)
await page.addInitScript(deepScript)
await page.goto(`${BASE}/open-house`, { waitUntil: 'domcontentloaded' })
await page.waitForFunction(() => window.__deepAll('open-house-panel').length > 0, { timeout: 60000 })
for (;;) { await sleep(1000); if (Date.now() - lastNav > 4000) break }
try {
  await page.waitForFunction(async () => {
    const panel = window.__deepAll('open-house-panel')[0]
    if (!panel?.hass?.callWS) return false
    const prompt = (ms) => Promise.race([
      panel.hass.callWS({ type: 'open_house/capabilities' }).then(() => true),
      new Promise((resolve) => setTimeout(() => resolve(false), ms)),
    ])
    return (await prompt(2000)) && (await prompt(2000))
  }, { timeout: 90000, polling: 1000 })
} catch (error) {
  events.push(`SLOW: ${String(error).slice(0, 120)}`)
}

const read = (fn, arg = null) => page.evaluate(fn, arg)
const click = async (sel, { nth = null, wait = 1200 } = {}) => {
  await page.waitForFunction((s) => window.__deepAll(s).length > 0, sel, { timeout: 30000 })
  const box = await page.evaluate(([s, f]) => {
    let list = window.__deepAll(s)
    if (f) list = list.filter((e) => (e.textContent ?? '').trim() === f)
    const el = list[0]
    if (!el) return null
    el.scrollIntoView({ block: 'center' })
    const r = el.getBoundingClientRect()
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 }
  }, [sel, nth])
  if (!box) throw new Error(`no element for ${sel}`)
  await page.mouse.click(box.x, box.y)
  await sleep(wait)
}
const shot = async (file) => { await page.screenshot({ path: `${SHOTS}/${file}.png` }); console.log(`${SHOTS}/${file}.png`) }

await page.waitForFunction(() => window.__deepAll('#tab-overview').length > 0, { timeout: 60000 })
await sleep(6000)
await shot(`${TAG}-shell`)

await click('#tab-activity'); await sleep(600); await shot(`${TAG}-activity`)
await click('#tab-rooms'); await sleep(800); await shot(`${TAG}-rooms`)
await click('#tab-house'); await sleep(1000); await shot(`${TAG}-house`)
await click('#tab-health'); await sleep(600); await shot(`${TAG}-health`)

console.log('marker app-bar:', await read(() => window.__deepAll('.app-bar').length))
console.log('marker hint:', await read(() =>
  window.__deepAll('.tabs .tab')[0]?.getAttribute('title') ?? '(none)'))
console.log('tabs:', await read(() =>
  window.__deepAll('.tabs .tab').map((t) => window.__deepText(t).replace(/\s+/g, ' ').trim()).join(' | ')))

await page.setViewportSize({ width: 480, height: 900 })
await click('#tab-health', { wait: 800 }); await sleep(400); await shot(`${TAG}-health-narrow`)
await click('#tab-activity', { wait: 800 }); await sleep(400); await shot(`${TAG}-activity-narrow`)

console.log(events.filter((e) => !e.startsWith('NAV')).join('\n'))
await browser.close()
