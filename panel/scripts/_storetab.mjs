// Scratch: does the Store tab's published half actually draw?
//
// The server half of the published Store is walked by `_published.mjs` over the
// websocket; what that cannot say is whether a person *sees* any of it. Unit
// tests cover the pure helpers and `tsc` covers the types, but neither renders a
// template, and a row that draws nothing and a row that draws a wrong number
// look the same from here. So this opens the panel, clicks Store, and reads what
// is on the screen.
//
// Run against a stack that is up, with `store_url` set and a name claimed:
//   node scripts/_storetab.mjs
import { openPanel, all, click, text, sleep } from './_ui.mjs'

const TAB = '#tab-store'
const SCREEN = 'open-house-tab-store'

const failures = []
let checked = 0
function check(label, ok, detail = '') {
  checked += 1
  if (!ok) failures.push(`${label}${detail ? ` -- ${detail}` : ''}`)
  console.log(`${ok ? 'ok  ' : 'FAIL'}  ${label}${detail ? `  (${detail})` : ''}`)
}

const { browser, page, events } = await openPanel()
try {
  await click(page, TAB)
  await sleep(3000)

  const screens = await all(page, SCREEN)
  check('the Store tab draws', screens.length === 1, `found ${screens.length}`)

  const seen = await text(page, SCREEN)
  check('the tab is not empty', seen.trim().length > 40, seen.trim().slice(0, 120))

  // The name the house publishes under, drawn as a fact rather than a form.
  check('the claimed name is shown', /walkhouse/i.test(seen), seen.slice(0, 400))
  check('the claim form is not drawn once a name is held', !/Pick a name to publish under/.test(seen))

  // The two sides, and the search.
  check('the Installed control draws', (await all(page, '#store-side-installed')).length === 1)
  check('the Not installed control draws', (await all(page, '#store-side-not_installed')).length === 1)
  check('the published search draws', (await all(page, '#store-published-search')).length === 1)

  // The neighbour's module, from the Store, on the side it is on.
  check("the Store's module is listed", /Evening lighting/.test(seen), seen.slice(0, 600))
  check('its publisher is named', /marqbarq/.test(seen))

  // The publish button on the house's own offering.
  const publish = await all(page, '#store-publish-evening_lighting')
  check('the house can publish its own module', publish.length >= 1, `found ${publish.length}`)

  // Not installed: the neighbour is installed here, so this side should be empty
  // of it -- clicking it must not throw and must not lose the screen.
  await click(page, '#store-side-not_installed')
  await sleep(1200)
  const other = await text(page, SCREEN)
  check('switching sides keeps the screen drawn', other.trim().length > 40, other.trim().slice(0, 120))

  await click(page, '#store-side-installed')
  await sleep(1200)
  const back = await text(page, SCREEN)
  check('and coming back shows the module again', /Evening lighting/.test(back), back.slice(0, 400))

  const errors = events.filter((line) => /PAGEERROR|CONSOLE/.test(line))
  check('the browser logged no errors', errors.length === 0, errors.join(" | ").slice(0, 300))
} finally {
  await page.screenshot({ path: 'shots/store-tab.png', fullPage: false }).catch(() => {})
  await browser.close()
}

console.log(`\n${checked - failures.length}/${checked} checks passed`)
if (failures.length) {
  console.log('failed:')
  for (const line of failures) console.log(`  - ${line}`)
  process.exitCode = 1
}
