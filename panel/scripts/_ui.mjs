// Scratch: the browser-driving helpers, shared by the recon scripts.
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

const local = (r) => fileURLToPath(new URL(r, import.meta.url))
export const BASE = process.env.HA_BASE_URL ?? 'http://localhost:8123'
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
  // A slot row's first cell carries the slot's label and then a
  // required/optional chip, so the cell's textContent reads "Light group
  // required". The label is what names the row to a person and to this walk;
  // the chip is a second fact about it. Reading the cell's whole text and
  // comparing that to "Light group" never matches, which is how a room that
  // the room list had just shown as 4 of 5 bound was reported as having
  // nothing bound at all.
  window.__slotLabel = (row) =>
    [...(row.querySelector('td')?.childNodes ?? [])]
      .filter((n) => n.nodeType === 3)
      .map((n) => n.textContent ?? '')
      .join('')
      .trim()
  window.__boots = Number(sessionStorage.getItem('probeBoots') ?? 0) + 1
  sessionStorage.setItem('probeBoots', String(window.__boots))
`
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

/** Open the panel and wait until HA's one-shot reload is behind us. */
export async function openPanel() {
  const tokens = readFileSync(local('../../.local/ha_tokens.json'), 'utf8')
  const browser = await chromium.launch({ executablePath: CHROME })
  const page = await browser.newPage({ viewport: { width: 1600, height: 1100 } })
  const events = []
  let lastNav = 0
  page.on('framenavigated', (f) => { if (f === page.mainFrame()) { lastNav = Date.now(); events.push(`NAV ${f.url()}`) } })
  page.on('pageerror', (e) => events.push(`PAGEERROR ${String(e).slice(0, 200)}`))
  page.on('console', (m) => { if (m.type() === 'error') events.push(`CONSOLE ${m.text().slice(0, 200)}`) })
  await page.addInitScript((b) => { window.localStorage.setItem('hassTokens', b) }, tokens)
  await page.addInitScript(DEEP)
  await page.goto(`${BASE}/open-house`, { waitUntil: 'domcontentloaded' })
  await page.waitForFunction(() => window.__deepAll('open-house-panel').length > 0, { timeout: 60000 })
  for (;;) { await sleep(1000); if (Date.now() - lastNav > 4000 && (await page.evaluate(() => window.__boots)) >= 2) break }
  return { browser, page, events }
}

/** Every element matching a selector, shadow roots included. */
export const all = (page, sel) => page.evaluate((s) => window.__deepAll(s), sel)

/** Flattened visible text of an element, shadow roots included. */
export const text = (page, sel) => page.evaluate((s) => {
  const n = window.__deepAll(s)[0]
  return n ? window.__deepText(n).replace(/\s+/g, ' ').trim() : null
}, sel)

/**
 * Click an element by real mouse coordinates, so nothing synthetic is faked.
 *
 * Before the click, it asks the page what a click at that point would actually
 * hit, and refuses to click when the answer is something else. Clicking at
 * coordinates is what makes this walk honest -- it is the only way to find a
 * control that is present, positioned, and covered -- but it is also silent:
 * a click delivered to an overlay lands, does nothing, and leaves the walk to
 * report whatever it reads next as if the control had been used. The
 * "Add module to room" dialog was exactly that: its content rendered beneath
 * its own backdrop, so every attempt to install a module closed the dialog
 * instead, and the walk called it "installed driveway".
 *
 * The relationship is checked in both directions because a control that
 * contains the hit (a button around an icon) and a control inside the hit (a
 * slotted node, whose flattened position is inside the hit's subtree) are both
 * reachable. Only an unrelated element in front is a failure.
 */
export async function click(page, sel, { index = 0, nth = null, wait = 800 } = {}) {
  const box = await page.evaluate(([s, i, filter]) => {
    let list = window.__deepAll(s)
    if (filter) list = list.filter((e) => (e.textContent ?? '').trim() === filter)
    const el = list[i]
    if (!el) return null
    el.scrollIntoView({ block: 'center' })
    const r = el.getBoundingClientRect()
    const x = r.x + r.width / 2
    const y = r.y + r.height / 2
    const root = el.getRootNode()
    const hit = (root.elementFromPoint ? root : document).elementFromPoint(x, y)
    return {
      x,
      y,
      n: list.length,
      reaches: !!hit && (hit === el || el.contains(hit) || hit.contains(el)),
      hit: hit
        ? `${hit.tagName.toLowerCase()}.${(hit.className ?? '').toString().split(' ')[0]}`
        : 'nothing at all',
    }
  }, [sel, index, nth])
  if (!box) throw new Error(`no element for ${sel} (${nth ?? index})`)
  if (!box.reaches) {
    throw new Error(
      `nothing of ${sel} (${nth ?? index}) would receive the click -- ${box.hit} is in front of it`,
    )
  }
  await page.mouse.click(box.x, box.y)
  await sleep(wait)
  return box
}

/** Type into a field the way a person does. */
export async function type(page, sel, value, { index = 0, nth = null } = {}) {
  const ok = await page.evaluate(([s, i, filter]) => {
    let list = window.__deepAll(s)
    if (filter) list = list.filter((e) => (e.placeholder ?? e.getAttribute('aria-label') ?? '') === filter)
    const el = list[i]
    if (!el) return false
    el.scrollIntoView({ block: 'center' })
    el.focus()
    return true
  }, [sel, index, nth])
  if (!ok) throw new Error(`no field for ${sel} (${nth ?? index})`)
  await page.keyboard.press('Control+A')
  await page.keyboard.type(value, { delay: 12 })
  await sleep(120)
}
