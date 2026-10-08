// Scratch: draw every screen, open everything that is collapsed, and report
// what the browser complained about.
//
// A screen that throws is not a screen that looks wrong: a Lit element whose
// render throws leaves the *previous* screen in place, so a person sees a tab
// that "does nothing when you click it" and no error anywhere they would look.
// The page's own error events are the only place it is said out loud, which is
// why this walk listens to them rather than asserting on any particular text.
//
// Nothing here clicks a control that could change the house: the deepest it
// goes is opening every `<details>`, which is disclosure and nothing else.
import { openPanel, BASE, sleep } from './_ui.mjs'

const TABS = ['overview', 'rooms', 'house', 'profiles', 'store', 'activity', 'health', 'dev']

const { browser, page } = await openPanel()
const readAll = (fn, arg = null) => page.evaluate(fn, arg)

/** Everything the page said was wrong, with the screen it was said on. */
const complaints = []
let where = ''
page.on('pageerror', (error) => complaints.push(`${where} PAGEERROR ${String(error).slice(0, 400)}`))
page.on('console', (message) => {
  if (message.type() !== 'error') return
  const text = message.text()
  // The two the harness itself causes: a 401 on the HA auth page and the
  // service-worker noise the frontend logs on every load.
  if (/Manifest|service worker|favicon/i.test(text)) return
  complaints.push(`${where} CONSOLE ${text.slice(0, 400)}`)
})

for (const tab of TABS) {
  where = tab
  await page.goto(`${BASE}/open-house?open_house_tab=${tab}`, { waitUntil: 'domcontentloaded' })
  // The panel draws from a websocket reply, so "loaded" is the DOM plus a beat
  // for the answer -- and the first screen after a navigation pays for the page
  // reload Home Assistant does when `hassTokens` was injected.
  await sleep(5000)
  // The element itself, not its shadow root: this panel renders into the light
  // DOM (`createRenderRoot` returns `this`), so asking for a shadow root
  // answered "never drew" on all eight tabs in a panel that had drawn every one
  // of them -- a check that failed for its own reason and would have read as
  // eight broken screens.
  const drawn = await readAll(() => window.__deepAll('open-house-panel').length > 0)
  const opened = await readAll(() => {
    const details = window.__deepAll('details')
    let count = 0
    for (const one of details) {
      if (!one.open) {
        one.open = true
        count += 1
      }
    }
    return count
  })
  await sleep(1200)
  const text = await readAll(() =>
    window.__deepText(window.__deepAll('open-house-panel')[0]).replace(/\s+/g, ' ').trim(),
  )
  console.log(
    `${tab.padEnd(9)} drawn=${drawn ? 'yes' : 'NO'} details-opened=${opened} ` +
      `text=${String(text).length} chars`,
  )
  if (!drawn) complaints.push(`${tab} the panel never drew`)
  if (String(text).length < 40) complaints.push(`${tab} drew almost nothing: ${JSON.stringify(String(text).slice(0, 200))}`)
}

console.log(`\n${complaints.length} complaints`)
for (const one of complaints) console.log(`  ${one}`)
await browser.close()
