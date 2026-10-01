// Drive the built panel in a real browser against the running Home Assistant.
//
// The route returning 200 only proves the bundle is *served*. This proves it
// *mounts*: the custom element registers, Home Assistant's frontend gives it a
// sidebar entry, it renders inside its shadow root, and nothing throws on the
// way. Those are four different failures that look identical from curl.
//
// The session comes from a token blob minted by `.local/ha_tokens.py`, which the
// frontend reads from `localStorage.hassTokens` -- driving the login form itself
// would mean reaching through Home Assistant's own shadow DOM, which is a test of
// their markup rather than of our panel.
//
// Everything here pierces shadow roots. Home Assistant renders the panel inside
// its own shadow DOM, and the panel renders its tabs inside a second one, so a
// plain `document.querySelector` finds nothing even when the panel is perfectly
// mounted -- which is exactly the false alarm this file first produced.
//
// Run: `.venv/Scripts/python.exe ../.local/ha_tokens.py && node scripts/browser-check.mjs`

import { readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

/** A repo path from a URL relative to this file. `new URL(...).pathname` is not
 *  it: on Windows it keeps the leading slash before the drive letter. */
const local = (relative) => fileURLToPath(new URL(relative, import.meta.url))

const BASE = process.env.HA_BASE_URL ?? 'http://localhost:8123'
const tokens = readFileSync(local('../../.local/ha_tokens.json'), 'utf8')

// The driver that resolves from `panel/node_modules` expects a browser build
// this machine does not have, and the ones it does have are the full chromium
// rather than the headless shell. Pointing at one that exists beats downloading
// a second copy of the same browser.
const CHROME = process.env.CHROME_PATH ??
  `${process.env.LOCALAPPDATA}/ms-playwright/chromium-1234/chrome-win64/chrome.exe`

// Injected into every page. `deepAll` walks shadow roots recursively; the panel
// is two levels down, so nothing in this file may use a plain query.
const DEEP = `
  window.__deepAll = (selector, root = document) => {
    const found = [...root.querySelectorAll(selector)]
    for (const host of root.querySelectorAll('*')) {
      if (host.shadowRoot) found.push(...window.__deepAll(selector, host.shadowRoot))
    }
    return found
  }
  window.__deepText = (node) => {
    if (node.nodeType === 3) return node.textContent ?? ''
    // Styles and scripts are text nodes too, and a panel's own CSS dwarfs its
    // labels -- leaving them in makes every text assertion pass vacuously.
    if (node.nodeType === 1 && ['STYLE', 'SCRIPT'].includes(node.tagName)) return ''
    return [...(node.shadowRoot ?? node).childNodes].map(window.__deepText).join(' ')
  }
`

const browser = await chromium.launch({ executablePath: CHROME })
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })

const consoleErrors = []
const pageErrors = []
page.on('console', (message) => {
  if (message.type() === 'error') consoleErrors.push(message.text())
})
page.on('pageerror', (error) => pageErrors.push(String(error)))

await page.addInitScript((blob) => {
  window.localStorage.setItem('hassTokens', blob)
}, tokens)
await page.addInitScript(DEEP)

const report = { steps: [], failures: [] }
const step = (name, ok, detail) => {
  report.steps.push({ name, ok, detail })
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${name}${detail ? ` -- ${detail}` : ''}`)
  if (!ok) report.failures.push(name)
}

await page.goto(`${BASE}/open-house`, { waitUntil: 'domcontentloaded' })
step('the panel route loads', page.url().includes('/open-house'), page.url())

// The sidebar entry: Home Assistant only renders it for a registered panel, so
// this is the assertion that `async_register_panel` actually ran.
try {
  await page.waitForSelector('a[href="/open-house"]', { timeout: 60000 })
  step('Home Assistant shows the sidebar entry', true)
} catch {
  step('Home Assistant shows the sidebar entry', false, 'no a[href="/open-house"]')
}

// The element itself must be defined and mounted -- inside Home Assistant's
// shadow root, which is why this waits on the injected deep walk.
try {
  await page.waitForFunction(
    () => Boolean(customElements.get('open-house-panel')) && window.__deepAll('open-house-panel').length > 0,
    { timeout: 60000 },
  )
  step('the open-house-panel element is defined and mounted', true)
} catch (error) {
  step('the open-house-panel element is defined and mounted', false, String(error))
}

const shadowText = () =>
  page.evaluate(() => {
    const host = window.__deepAll('open-house-panel')[0]
    return host ? window.__deepText(host).replace(/\s+/g, ' ').trim() : ''
  })

let text = ''
try {
  await page.waitForFunction(
    () => {
      const host = window.__deepAll('open-house-panel')[0]
      return Boolean(host) && window.__deepText(host).trim().length > 0
    },
    { timeout: 60000 },
  )
  text = await shadowText()
} catch {
  /* reported below */
}

const expected = ['Overview', 'Rooms', 'Modules', 'Profiles', 'Activity', 'Health']
const missing = expected.filter((label) => !text.includes(label))
step(
  'the shadow root renders its tabs',
  missing.length === 0,
  missing.length ? `missing ${missing.join(', ')}; got "${text.slice(0, 200)}"` : `${text.length} chars`,
)

// A non-admin must not see Store or Import/Export, so their presence here means
// we are looking at the admin view -- which is the one this session has.
step(
  'the admin-only tabs are present for an admin',
  text.includes('Store') && text.includes('Import'),
  text.includes('Store') ? '' : `no Store tab in "${text.slice(0, 200)}"`,
)

// The overview tab is the one that proves the panel reached the backend rather
// than rendering an empty shell: its cards are built from rooms the integration
// published, so text here means the entity round-trip worked.
step(
  'the overview tab rendered content',
  await page.evaluate(() => window.__deepAll('open-house-tab-overview').length > 0),
  '',
)

const shot = local('../../.local/panel-open-house.png')
await page.screenshot({ path: shot, fullPage: true })
step('a screenshot was written', true, '.local/panel-open-house.png')

step('no uncaught page errors', pageErrors.length === 0, pageErrors.join(' | ').slice(0, 600))

const realErrors = consoleErrors.filter((line) => !/favicon|404 \(Not Found\)/i.test(line))
step('no console errors', realErrors.length === 0, realErrors.join(' | ').slice(0, 600))

writeFileSync(local('../../.local/panel-browser-report.json'), JSON.stringify(report, null, 2))
await browser.close()

console.log(report.failures.length ? `\n${report.failures.length} FAILURE(S)` : '\nall browser assertions hold')
process.exit(report.failures.length ? 1 : 0)
