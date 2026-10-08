// Scratch: the switch beside a cast, driven by real clicks on a live house.
//
// The claim under test is demand two -- a row answered with logic can be
// *exposed as an entity any automation may read*, through a toggle. What the
// adapter tests prove is the rule (`module_definitions.published_picks`); what
// they cannot prove is that a person can reach it, and that what comes out is
// real: an entity Home Assistant itself knows, at the name the card says, which
// goes away again when the switch is turned off.
//
// Run: node scripts/_publish.mjs
//
// **The row is chosen by the server, not by this file.** Which module holds a
// cast, and which of its rows has one, is a fact about the house that cannot be
// written down in advance; asking `open_house/modules/hosted` and driving the
// first row that answers yes is what keeps this walk from being a script about
// one house's names.
//
// Every page-side function spells its selectors out in full: a closure over a
// Node-side constant is not there in the browser.
import { mkdirSync } from 'node:fs'
import { openPanel, all, click, sleep, BASE } from './_ui.mjs'

const SHOTS = `${process.env.TEMP ?? '/tmp'}/open-house-shots`

const FAILURES = []
const ok = (label, pass, detail = '') => {
  console.log(`${pass ? 'ok  ' : 'FAIL'} ${label}${detail ? ` -- ${detail}` : ''}`)
  if (!pass) FAILURES.push(label)
}

const { browser, page, events } = await openPanel()
const readAll = (fn, arg = null) => page.evaluate(fn, arg)
const waitFor = (fn, arg = null, ms = 90000) =>
  page.waitForFunction(fn, arg, { timeout: ms, polling: 500 })
mkdirSync(SHOTS, { recursive: true })
const shot = (name) => page.screenshot({ path: `${SHOTS}/publish-${name}.png` })

/** One `open_house/*` command over the panel's own websocket connection. */
const ws = (message) =>
  readAll((msg) => {
    const panel = window.__deepAll('open-house-panel')[0]
    return panel.hass.callWS(msg)
  }, message)

/** Whether Home Assistant itself has an entity by that name, and what it reads. */
const stateOf = (entityId) =>
  readAll((id) => {
    const panel = window.__deepAll('open-house-panel')[0]
    const state = panel.hass.states[id]
    return state === undefined ? null : String(state.state)
  }, entityId)

/** The module as the server reports it now, by slug and room. */
const hosted = async (slug, roomId) => {
  const listing = await ws({ type: 'open_house/modules/hosted' })
  return listing.modules.find(
    (one) => one.slug === slug && one.room_id === roomId,
  )
}

/** A row holding logic, per the server, and the module hosting it. */
const candidate = async () => {
  const listing = await ws({ type: 'open_house/modules/hosted' })
  for (const module of listing.modules) {
    for (const setting of module.settings) {
      const holds =
        setting.cast != null ||
        Boolean(setting.script_id) ||
        Boolean(setting.flow_id)
      // One already published is skipped rather than taken: the house may be
      // holding one from an earlier run, and a walk that only ever tested the
      // second half of the switch would be a walk about one direction. What is
      // left behind is put back, so the house ends as it was found.
      if (holds && !setting.published_key) return { module, setting }
    }
  }
  return null
}

/**
 * The switch on one row, found and hit-tested.
 *
 * Scoped to the row's own field and not to the card: a card carries other
 * checkboxes -- a boolean setting, the configurations block -- and the first one
 * in the card is not this row's. Aimed by coordinates and hit-tested, so a
 * control that renders behind something is reported rather than silently missed.
 */
const boxAt = (slug, name) =>
  readAll(
    ([want, setting]) => {
      const card = window
        .__deepAll('open-house-hosted-module')
        .find((one) => (one.module?.slug ?? '') === want)
      if (!card) return { error: `no card for ${want}` }
      const form = window.__deepAll(`ha-form[data-setting="${setting}"]`, card)[0]
      if (!form) return { error: `no row called ${setting}` }
      const box = window.__deepAll(
        'label.check input[type="checkbox"]',
        form.parentElement ?? form,
      )[0]
      if (!box) return { error: `the row ${setting} has no switch` }
      box.scrollIntoView({ block: 'center' })
      const r = box.getBoundingClientRect()
      const x = r.x + r.width / 2
      const y = r.y + r.height / 2
      const hit = document.elementFromPoint(x, y)
      const holds = (outer, node) => {
        for (let n = node; n; ) {
          if (n === outer) return true
          n = n.parentNode || n.host || null
        }
        return false
      }
      return {
        checked: box.checked,
        label: window.__deepText(box.parentElement).replace(/\s+/g, ' ').trim(),
        x,
        y,
        reaches: !!hit && (holds(box, hit) || holds(hit, box)),
        hit: hit ? hit.tagName.toLowerCase() : 'nothing',
      }
    },
    [slug, name],
  )

const flip = async (slug, name) => {
  const at = await boxAt(slug, name)
  if (at.error) throw new Error(at.error)
  if (!at.reaches) throw new Error(`the switch is behind ${at.hit}, so no click reaches it`)
  await page.mouse.click(at.x, at.y)
  await sleep(1200)
}

try {
  const found = await candidate()
  if (!found) {
    console.log('SKIP  no module in this house holds an unpublished cast')
    for (const event of events) console.log(`  ${event}`)
    await browser.close()
    process.exit(0)
  }
  const { module, setting } = found
  const answers = setting.script_id
    ? `a script (${setting.script_id})`
    : setting.flow_id
      ? `a flow (${setting.flow_id})`
      : 'a condition or a template'
  console.log(`the house offers ${module.slug} / ${setting.name}, answered by ${answers}`)

  // -- Go to the page that carries the module's card ------------------------
  // A card is drawn where the module *is*: the House tab filters its own list to
  // the house's own modules (`house.ts`), and a room's module is on that room's
  // page. Which page that is is the record's answer, not this walk's choice --
  // a walk that only ever looked on the House tab could not see a room's module
  // at all, and would call the card missing rather than look where it is.
  if (module.room_id === '') {
    await click(page, '#tab-house', { wait: 2500 })
  } else {
    await page.goto(
      `${BASE}/open-house?open_house_tab=rooms&open_house_room=${encodeURIComponent(module.room_id)}`,
      { waitUntil: 'domcontentloaded' },
    )
  }
  await waitFor(
    (slug) =>
      window
        .__deepAll('open-house-hosted-module')
        .some((one) => (one.module?.slug ?? '') === slug),
    module.slug,
  )
  const cards = await all(page, 'open-house-hosted-module')
  ok('the module has a card where it lives', cards.length > 0, `${cards.length} cards`)

  const before = await boxAt(module.slug, setting.name)
  if (before.error) throw new Error(before.error)
  ok('the row holding logic carries the switch, off', before.checked === false, before.label)
  ok(
    'and it says what it is for',
    before.label.includes('usable by any automation'),
    before.label,
  )
  await shot('01-off')

  // -- Switch it on ---------------------------------------------------------
  await flip(module.slug, setting.name)
  await waitFor(
    ([slug, key]) => {
      const card = window
        .__deepAll('open-house-hosted-module')
        .find((one) => (one.module?.slug ?? '') === slug)
      return (card?.module?.outputs ?? []).some((output) => output.key === key)
    },
    [module.slug, setting.name],
  ).catch(async (failure) => {
    throw new Error(`the module never grew that output: ${String(failure).slice(0, 120)}`)
  })

  const after = await hosted(module.slug, module.room_id)
  const published = (after?.outputs ?? []).find(
    (output) => output.key === setting.name,
  )
  ok(
    'switching it on publishes a value, named after the row it came from',
    published !== undefined,
    `${published?.key} (wanted ${setting.name})`,
  )
  const state = await stateOf(published?.entity_id ?? '')
  ok(
    'Home Assistant itself has that entity, so any automation can read it',
    state !== null,
    `${published?.entity_id} = ${state}`,
  )
  const onScreen = await readAll(
    ([slug, id]) => {
      const card = window
        .__deepAll('open-house-hosted-module')
        .find((one) => (one.module?.slug ?? '') === slug)
      return card ? window.__deepText(card).includes(id) : false
    },
    [module.slug, published.entity_id],
  )
  ok('and the card names it where the person is looking', onScreen, published.entity_id)
  const nowBox = await boxAt(module.slug, setting.name)
  ok('with the switch showing it is on', nowBox.checked === true, JSON.stringify(nowBox.checked))
  await shot('02-on')

  // -- Switch it off again, and the entity goes -----------------------------
  await flip(module.slug, setting.name)
  await sleep(3000)
  const back = await hosted(module.slug, module.room_id)
  ok(
    'switching it off takes the value away again',
    (back?.outputs ?? []).every((output) => output.key !== published.key),
    JSON.stringify((back?.outputs ?? []).map((output) => output.key)),
  )
  const gone = await stateOf(published.entity_id)
  ok(
    'and the entity is gone from Home Assistant with it',
    gone === null,
    gone === null ? 'absent' : `still there, reading ${gone}`,
  )
  const offBox = await boxAt(module.slug, setting.name)
  ok('with the switch showing it is off', offBox.checked === false, JSON.stringify(offBox.checked))
  await shot('03-off-again')
} catch (error) {
  ok(`the walk ran to the end (${String(error).slice(0, 300)})`, false)
}

for (const event of events) console.log(`  ${event}`)
console.log(FAILURES.length ? `\nFAILED: ${FAILURES.length}` : '\nall ok')
await browser.close()
process.exit(FAILURES.length ? 1 : 0)
