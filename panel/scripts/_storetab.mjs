// Scratch: the Store tab, walked from an install that has never claimed a name.
//
// The server half of the published Store is walked by `_published.mjs` over the
// websocket; what that cannot say is whether a person *sees* any of it. Unit
// tests cover the pure helpers and `tsc` covers the types, but neither renders a
// template, and a row that draws nothing and a row that draws a wrong number
// look the same from here. So this opens the panel, clicks Store, and reads what
// is on the screen.
//
// This walk is about the **Publish button**, which is drawn on every module this
// house made and asks for whatever publishing still needs rather than being
// hidden until a person has found the settings screen. What it asks for is the
// publisher name -- once, ever -- and the description the Store row will carry,
// which is prefilled from the module and asked every time so it can be changed.
// **The Store address is not asked for at all**: it is the one this build ships
// with (`store.DEFAULT_URL`), so a fresh install already has a Store and the
// only thing missing is a name.
//
// Two setup facts, both about the Store the walk publishes to:
//
//   * The house talks to it from *inside* the Home Assistant container*, so the
//     address it uses is the container's name for it -- `DEFAULT_URL` in
//     `custom_components/open_house/store.py`, `http://host.docker.internal:8090`
//     for a Store running in `store/` beside the stack. The walk reaches the
//     same Store from the host at `STORE_ADMIN_URL`, which is where the name it
//     claims is cleared from first so that the walk reads the same twice.
//   * That clearing reads `store/.env`, the admin of the Store -- the account
//     that imports the schema. Publishers make their own accounts, and those are
//     what the walk claims through the panel.
//
//   cd store && docker compose up -d
//   node panel/scripts/_storetab.mjs
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { openPanel, all, click, text, type, sleep } from './_ui.mjs'

const TAB = '#tab-store'
const SCREEN = 'open-house-tab-store'
/** Any tab that is not Store, for leaving and coming back. */
const AWAY = '#tab-house'
/** The address this build ships pointed at, as the container reaches it. */
const SHIPPED = process.env.STORE_URL ?? 'http://host.docker.internal:8090'
/** The same Store, as this walk reaches it. */
const STORE_ADMIN_URL = process.env.STORE_ADMIN_URL ?? 'http://127.0.0.1:8090'
/** The name this walk claims for the house, and one somebody else holds. */
const PUBLISHER = 'walkhouse'
const TAKEN = 'marqbarq'
/** What the walk writes into the description box, to prove it travels. */
const BLURB = 'Walked end to end by the panel smoke test.'

const failures = []
let checked = 0
function check(label, ok, detail = '') {
  checked += 1
  if (!ok) failures.push(`${label}${detail ? ` -- ${detail}` : ''}`)
  console.log(`${ok ? 'ok  ' : 'FAIL'}  ${label}${detail ? `  (${detail})` : ''}`)
}

// -- the Store's own admin, for putting the walk back to the start ------------
//
// Everything the walk does through the panel is a person's act and stays that
// way; this is the one thing outside it, and it exists because a claimed name
// belongs to the Store rather than to this house. Dropping the name from a house
// leaves the Store still holding it, so a second run would be refused the name
// its first run claimed -- and "the name is taken" is a check this walk means to
// make on purpose, not to trip over.
function storeAdmin() {
  const text = readFileSync(
    fileURLToPath(new URL('../../store/.env', import.meta.url)),
    'utf8',
  )
  const value = (name) => {
    for (const line of text.split(/\r?\n/)) {
      const at = line.indexOf('=')
      if (at > 0 && line.slice(0, at).trim() === name) return line.slice(at + 1).trim()
    }
    throw new Error(`${name} is not in store/.env`)
  }
  return { email: value('PB_ADMIN_EMAIL'), password: value('PB_ADMIN_PASSWORD') }
}

async function authToken() {
  const { email, password } = storeAdmin()
  const reply = await fetch(
    `${STORE_ADMIN_URL}/api/collections/_superusers/auth-with-password`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ identity: email, password }),
    },
  )
  if (!reply.ok) throw new Error(`the Store refused its own admin (${reply.status})`)
  return (await reply.json()).token
}

/** Drop the walk's publisher from the Store, and the modules it published. */
async function clearPublisher(token) {
  const auth = { Authorization: token }
  const found = await fetch(
    `${STORE_ADMIN_URL}/api/collections/publishers/records?perPage=200&filter=` +
      encodeURIComponent(`name='${PUBLISHER}'`),
    { headers: auth },
  )
  if (!found.ok) throw new Error(`could not read publishers (${found.status})`)
  const publishers = (await found.json()).items ?? []
  const modules = await fetch(
    `${STORE_ADMIN_URL}/api/collections/modules/records?perPage=200`,
    { headers: auth },
  )
  if (!modules.ok) throw new Error(`could not read modules (${modules.status})`)
  const ids = new Set(publishers.map((row) => row.id))
  for (const row of (await modules.json()).items ?? []) {
    if (!ids.has(row.publisher)) continue
    const gone = await fetch(
      `${STORE_ADMIN_URL}/api/collections/modules/records/${row.id}`,
      { method: 'DELETE', headers: auth },
    )
    if (!gone.ok) throw new Error(`could not delete ${row.slug} (${gone.status})`)
  }
  for (const row of publishers) {
    const gone = await fetch(
      `${STORE_ADMIN_URL}/api/collections/publishers/records/${row.id}`,
      { method: 'DELETE', headers: auth },
    )
    if (!gone.ok) throw new Error(`could not delete ${PUBLISHER} (${gone.status})`)
  }
  return publishers.length + ids.size
}

/**
 * Set this house's Store address, over the panel's own client.
 *
 * Through the client and not `hass.callWS`, because writing the options reloads
 * the entry (`__init__._async_reload_entry`) and a command that lands during
 * that reload is answered `not_ready`. The client retries that one refusal and
 * `callWS` does not, which is the difference between this returning and the walk
 * dying on an object it cannot print.
 */
const reset = (page, url) =>
  page.evaluate(
    (address) =>
      window.__deepAll('open-house-panel')[0].client.publishedConfigure(address),
    url,
  )

/**
 * Put the house back to the state a fresh install is in: a shipped Store, no
 * claimed name.
 *
 * Two writes, because a write only forgets the name when the address it writes
 * *differs* from the one the house is holding (`store.with_url`). The first
 * moves the house off whatever it was on, the second puts it back to the address
 * the build ships with -- so this reads the same from any starting state, which
 * is what a walk that runs twice needs.
 */
async function fresh(page) {
  await reset(page, 'http://127.0.0.1:8090')
  return reset(page, '')
}

const { browser, page, events } = await openPanel()
try {
  const token = await authToken()
  const cleared = await clearPublisher(token)
  console.log(`(cleared ${cleared} of the walk's own records from the Store)`)

  const start = await fresh(page)
  check(
    'the house starts on the Store it ships with, and a name no one has claimed',
    start.url === SHIPPED && start.name === '',
    JSON.stringify(start),
  )

  await click(page, TAB)
  await sleep(3000)

  // -- the section, which has a Store before anybody asked for one ----------
  const screens = await all(page, SCREEN)
  check('the Store tab draws', screens.length === 1, `found ${screens.length}`)

  let seen = await text(page, SCREEN)
  check('the tab is not empty', seen.trim().length > 40, seen.trim().slice(0, 120))
  check(
    'it does not ask for an address it was built with',
    !/No Store is configured yet/.test(seen),
    seen.slice(0, 300),
  )
  check(
    'and the Connect button is not drawn',
    (await all(page, '#store-connect')).length === 0,
  )

  // -- the Publish button is drawn on every module this house made ----------
  const slugs = await page.evaluate(() =>
    window.__deepAll('[id^="store-publish-"]').map((b) =>
      b.id.replace('store-publish-', ''),
    ),
  )
  check(
    'a Publish button is drawn on a fresh install',
    slugs.length >= 2,
    `found ${slugs.length}`,
  )
  if (slugs.length < 2) {
    throw new Error(`this house offers ${slugs.length} module(s); the walk needs two`)
  }
  const [first, second] = slugs

  // -- pressing it asks for the name and the description, and nothing else ---
  await click(page, `#store-publish-${first}`)
  await sleep(1000)
  check('pressing Publish opens a dialog', (await all(page, 'open-house-dialog[open]')).length === 1)
  let dialog = await text(page, 'open-house-dialog')
  check('the dialog is headed for that module', /^Publish "/.test(dialog), dialog.slice(0, 80))
  check('it asks for the publisher name', (await all(page, '#store-setup-name')).length === 1)
  check(
    'it does not ask for a Store address',
    (await all(page, '#store-setup-url')).length === 0,
  )
  check(
    'it asks for the description',
    (await all(page, '#store-setup-summary')).length === 1,
  )
  await page.screenshot({ path: 'shots/store-publish-setup.png' })

  // The one box it must not have repeated: the section's own claim form is
  // drawn under the dialog, and the same question in two places is the question
  // twice.
  check(
    'the name is asked for in one place only',
    (await all(page, '#store-claim-name')).length === 0,
  )

  const disabled = () =>
    page.evaluate(() => window.__deepAll('#store-setup-submit')[0]?.disabled ?? null)
  check('the button is disabled while the name box is empty', (await disabled()) === true)

  // A name somebody else holds, which is the refusal this dialog exists to show.
  await type(page, '#store-setup-name', TAKEN)
  await type(page, '#store-setup-summary', BLURB)
  await sleep(200)
  check('a name arms the button', (await disabled()) === false)
  await click(page, '#store-setup-submit')
  await sleep(4000)

  check('a refused name leaves the dialog up', (await all(page, 'open-house-dialog[open]')).length === 1)
  // The sentence is read from the paragraph it is drawn in rather than from the
  // dialog's text, because everything inside the dialog is *slotted*: it is a
  // child of the element in the light tree, and the dialog's own text is its
  // chrome -- the heading and the Close button -- and nothing else.
  dialog = await page.evaluate(() =>
    window
      .__deepAll('open-house-dialog [role="alert"]')
      .map((n) => n.textContent ?? '')
      .join(' '),
  )
  check(
    'and shows the Store\'s own sentence',
    /taken/i.test(dialog) && /pick another name/i.test(dialog),
    dialog.slice(0, 300),
  )
  await page.screenshot({ path: 'shots/store-publish-refused.png' })
  check('the name it refused is still asked for', (await all(page, '#store-setup-name')).length === 1)
  check(
    'and the description typed so far is still there',
    (await page.evaluate(() => window.__deepAll('#store-setup-summary')[0]?.value ?? '')) === BLURB,
  )

  // -- the corrected name publishes ------------------------------------------
  await type(page, '#store-setup-name', PUBLISHER)
  await click(page, '#store-setup-submit')
  await sleep(5000)

  check('the dialog closes once it has worked', (await all(page, 'open-house-dialog')).length === 0)
  seen = await text(page, SCREEN)
  check(
    'the module is published, and the screen says so',
    /Published .* as walkhouse/.test(seen),
    seen.slice(0, 400),
  )
  check('the house now holds the name', /Publishing as\s*walkhouse/.test(seen), seen.slice(0, 600))
  check(
    'the claim form is gone, so the name is asked once ever',
    (await all(page, '#store-claim-name')).length === 0,
  )
  // It is on the Store now, so the row says that instead of offering to do it
  // again -- the button that was there is a statement once it has been acted on.
  check(
    'the module it published reads as published instead',
    (await all(page, `#store-publish-${first}`)).length === 0 &&
      (await all(page, `#store-published-${first}`)).length === 1,
  )

  // -- a second module is published, and asked only for its description ------
  await click(page, `#store-publish-${second}`)
  await sleep(1000)
  check('a second Publish opens the dialog', (await all(page, 'open-house-dialog[open]')).length === 1)
  check(
    'it does not ask for the name it holds',
    (await all(page, '#store-setup-name')).length === 0,
  )
  const prefilled = await page.evaluate(
    () => window.__deepAll('#store-setup-summary')[0]?.value ?? '',
  )
  check(
    'and the description is prefilled from the module',
    prefilled.length > 0,
    prefilled.slice(0, 80),
  )
  await type(page, '#store-setup-summary', BLURB)
  await click(page, '#store-setup-submit')
  await sleep(5000)
  check('answering it publishes', (await all(page, 'open-house-dialog')).length === 0)
  seen = await text(page, SCREEN)
  check(
    'with the name it holds',
    /Published .* as walkhouse/.test(seen),
    seen.slice(0, 400),
  )
  check(
    'and now both of them read as published',
    (await all(page, `#store-published-${first}`)).length === 1 &&
      (await all(page, `#store-published-${second}`)).length === 1,
  )
  check(
    'while a module nobody has published still offers to',
    (await all(page, '[id^="store-publish-"]')).length >= 1,
  )

  // -- the Store's own list, which is what the address bought ---------------
  await click(page, '#store-side-installed')
  await sleep(2500)
  seen = await text(page, SCREEN)
  check('the tab keeps drawing after all of that', seen.trim().length > 40)
  check(
    'the module it published is on the Store under its name',
    /walkhouse/.test(seen) && /yours/.test(seen),
    seen.slice(0, 800),
  )
  check(
    'and carries the description that was typed, not the module\'s own',
    seen.includes(BLURB),
    seen.slice(0, 800),
  )

  const errors = events.filter((line) => /PAGEERROR|CONSOLE/.test(line))
  check('the browser logged no errors', errors.length === 0, errors.join(' | ').slice(0, 300))
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
