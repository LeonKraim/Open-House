// Scratch: the browser-driving helpers, shared by the recon scripts.
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

const local = (r) => fileURLToPath(new URL(r, import.meta.url))
export const BASE = process.env.HA_BASE_URL ?? 'http://localhost:8123'
const CHROME = process.env.CHROME_PATH ?? `${process.env.LOCALAPPDATA}/ms-playwright/chromium-1234/chrome-win64/chrome.exe`
const DEEP = `
  // The root itself is scanned for a shadow root as well as its descendants, so
  // a search scoped to a custom element -- see the "within" option on the
  // helpers below -- reaches everything that element renders. A custom element
  // keeps its content in its own shadow root and in no light DOM at all, so a
  // search that only looked at descendants would find an empty element.
  window.__deepAll = (s, r = document) => {
    // The root itself counts when it matches. querySelectorAll only ever looks
    // at descendants, so a search scoped to one element -- a row's own boolean
    // switch, say -- came back empty for the very element it was pointed at.
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
  // A slot row's first cell carries the slot's label and then a
  // required/optional chip, so the cell's textContent reads "Light group
  // required". The label is what names the row to a person and to this walk;
  // the chip is a second fact about it. Reading the cell's whole text and
  // comparing that to "Light group" never matches, which is how a room that
  // the room list had just shown as 4 of 5 bound was reported as having
  // nothing bound at all.
  window.__slotLabel = (row) => {
    const cell = row.querySelector('td')
    if (!cell) return ''
    // The label is the cell's first span -- the slot's own name -- and not the
    // cell. Everything drawn after it is about the slot rather than the name of
    // it: the required/optional chip, the modules that reach it, and the parts
    // block with its "Add a part" control. Reading the whole cell put "Add a
    // part" on the end of every label, so a walk binding a row called "Fan"
    // looked for "Fan Add a part", found none, and reported the house unbound.
    //
    // The chip is taken out rather than the label being read around it: the
    // span holds the label *and* the chip, so reading the span's own text nodes
    // returned "Light group required", which compares equal to no label at all.
    const first = cell.querySelector('div.stack > span') ?? cell
    const copy = first.cloneNode(true)
    for (const chip of copy.querySelectorAll('.chip')) chip.remove()
    // The backslash is doubled because this whole block is one plain template
    // literal: inside one, an escape the language does not know is dropped and
    // the character itself is kept, so a single backslash here ships as a bare
    // \`s\` and the expression reads /s+/g -- *replace every run of the letter s
    // with a space*. It did: "Ambient light sensor" arrived at the walk as
    // "Ambient light  en or", every room's every row failed to match the label
    // it was looked up by, and a 117-failure run was read as a broken panel
    // when the panel had rendered the label correctly all along. Any regex that
    // belongs to the *page* rather than to this file has to survive the same
    // trip, so it is written for the page's eyes and not for the editor's.
    return (copy.textContent ?? '').replace(/\\s+/g, ' ').trim()
  }
  window.__boots = Number(sessionStorage.getItem('probeBoots') ?? 0) + 1
  sessionStorage.setItem('probeBoots', String(window.__boots))
`
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

/**
 * Open the panel and wait until it can be driven.
 *
 * Two waits, and the second is the one that matters. Home Assistant reloads the
 * page once when `hassTokens` was injected (see `image-rule` and the memory note
 * on it), so the first wait is for that reload to be behind us. That is not
 * enough: while Home Assistant is still booting -- parsing its own state, its
 * panels and its themes on the page's single thread -- a command sent to it is
 * answered *late*, not never, and a walk that read a screen during that window
 * found it mid-load and called it empty. It reported "0 blueprints" for a
 * blueprint picker that filled a few seconds later, and blamed the panel.
 *
 * So the second wait asks the connection itself, twice: a command that answers
 * inside two seconds is one that answered *now*, and asking twice is what
 * separates a prompt connection from one that was still working through a queue
 * of boot-time traffic. It is a probe of the harness's own readiness, not of the
 * panel -- which is why it is here, in the helpers, and not in any walk.
 */
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
  try {
    await page.waitForFunction(async () => {
      const panel = window.__deepAll('open-house-panel')[0]
      if (!panel?.hass?.callWS) return false
      const prompt = (ms) =>
        Promise.race([
          panel.hass.callWS({ type: 'open_house/capabilities' }).then(() => true),
          new Promise((resolve) => setTimeout(() => resolve(false), ms)),
        ])
      return (await prompt(2000)) && (await prompt(2000))
    }, { timeout: 90000, polling: 1000 })
  } catch (error) {
    events.push(`SLOW CONNECTION: ${String(error).slice(0, 120)}`)
  }
  return { browser, page, events }
}

/**
 * Every element matching a selector, shadow roots included.
 *
 * `within` scopes the search to one element's own subtree -- and to its shadow
 * root, because that is where a custom element keeps what it renders. Without
 * it, a selector like `button.tab` finds the panel's tabs, the Dev tab's job
 * tabs and the import screen's kind tabs alike, and only their position in the
 * document tells them apart.
 */
export const all = (page, sel, { within = null } = {}) =>
  page.evaluate(([s, root]) => {
    const scope = root ? window.__deepAll(root)[0] : document
    return scope ? window.__deepAll(s, scope) : []
  }, [sel, within])

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
export async function click(page, sel, { index = 0, nth = null, wait = 800, within = null } = {}) {
  const box = await page.evaluate(([s, i, filter, root]) => {
    const scope = root ? window.__deepAll(root)[0] : document
    let list = scope ? window.__deepAll(s, scope) : []
    if (filter) list = list.filter((e) => (e.textContent ?? '').trim() === filter)
    const el = list[i]
    if (!el) return null
    el.scrollIntoView({ block: 'center' })
    const r = el.getBoundingClientRect()
    const x = r.x + r.width / 2
    const y = r.y + r.height / 2
    const own = el.getRootNode()
    const hit = (own.elementFromPoint ? own : document).elementFromPoint(x, y)
    return {
      x,
      y,
      n: list.length,
      reaches: !!hit && (hit === el || el.contains(hit) || hit.contains(el)),
      hit: hit
        ? `${hit.tagName.toLowerCase()}.${(hit.className ?? '').toString().split(' ')[0]}`
        : 'nothing at all',
    }
  }, [sel, index, nth, within])
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

/**
 * The control for one row of an `ha-form`, found by the label its row shows.
 *
 * Home Assistant's `ha-form` renders one `ha-selector` per schema item and the
 * control *inside* it, in that selector's shadow root. A person reads a row by
 * its label, which is the `ha-selector`'s own text -- so the control is found
 * from the label outwards rather than by an index into the form's fields, which
 * would silently point at a different row the moment the schema gained a field.
 *
 * `control` is matched inside the row, deeply: `input` reaches the text field a
 * number is typed into and the box a boolean is ticked in, whichever the row
 * happens to be. The coordinates come back verified, the same way `click`
 * verifies them, because a control that renders behind something is a control a
 * person cannot use and a walk must not pretend to have used.
 */
async function findField(page, { form, contains = null, control, label }) {
  const spot = await page.evaluate(([formSel, controlSel, innerSel, want]) => {
    const formEl = window.__deepAll(formSel)[0]
    if (!formEl) return { error: `no form for ${formSel}` }
    for (const row of window.__deepAll('ha-selector', formEl.shadowRoot)) {
      const text = window.__deepText(row).replace(/\s+/g, ' ').trim()
      // A label of `null` means the form holds one field and its label is
      // rendered outside the form -- the settings editor, whose rows carry no
      // label of their own because the name is written above them.
      if (want !== null && !text.includes(want)) continue
      // The row's own selector names what kind of control the row holds, and it
      // is what tells a row's text box from another row's: every row of this
      // form is labelled with an input's title, and two of them answer the same
      // question about it.
      const scope = innerSel ? row.shadowRoot.querySelector(innerSel) : row.shadowRoot
      const el = scope ? window.__deepAll(controlSel, scope)[0] : null
      if (!el) continue
      el.scrollIntoView({ block: 'center' })
      const r = el.getBoundingClientRect()
      const x = r.x + r.width / 2
      const y = r.y + r.height / 2
      const own = el.getRootNode()
      const hit = (own.elementFromPoint ? own : document).elementFromPoint(x, y)
      // Containment is asked of the flattened tree: `contains` alone stops at a
      // shadow boundary, and a switch keeps its thumb in its own shadow root --
      // so the element that receives the click is a descendant the browser's
      // own `contains` denies, and the walk would refuse a click that works.
      const holds = (outer, node) => {
        for (let n = node; n; ) {
          if (n === outer) return true
          n = n.parentNode || n.host || null
        }
        return false
      }
      return {
        x,
        y,
        label: text.slice(0, 40),
        reaches: !!hit && (holds(el, hit) || holds(hit, el)),
        hit: hit
          ? `${hit.tagName.toLowerCase()}.${String(hit.className).split(' ')[0]}`
          : 'nothing at all',
      }
    }
    return { error: `no row labelled ${want}` }
  }, [form, control, contains, label])
  if (spot.error) throw new Error(`${spot.error} (in ${form})`)
  if (!spot.reaches) {
    throw new Error(
      `the ${label ?? "only"} control is behind ${spot.hit}, so nothing of it would receive a click`,
    )
  }
  return spot
}

/**
 * Tick, or untick, the `ha-form` row a label names.
 *
 * The control is the switch's knob and not the `input` behind it. A boolean row
 * renders as a switch whose thumb is drawn over that input, so aiming at the
 * input aims at something a person cannot reach -- and aiming at the row's
 * bounding box aims at whatever empty space the row's centre happens to fall
 * in, which is how a tick landed on the row and toggled nothing.
 */
export async function tickField(
  page,
  { form, contains = "ha-selector-boolean", control = "span.thumb", label },
) {
  const spot = await findField(page, { form, contains, control, label })
  await page.mouse.click(spot.x, spot.y)
  await sleep(400)
  return spot
}

/** Type into the `ha-form` row a label names, the way a person does. */
export async function typeField(
  page,
  { form, contains = "ha-selector-number", control = "input", label },
  value,
) {
  const spot = await findField(page, { form, contains, control, label })
  await page.mouse.click(spot.x, spot.y)
  await sleep(150)
  await page.keyboard.press("Control+A")
  await page.keyboard.type(value, { delay: 20 })
  await page.keyboard.press("Tab")
  await sleep(300)
  return spot
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
