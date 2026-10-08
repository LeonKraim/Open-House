// Scratch: the script cast -- "HAOS script logic" -- and the editor it opens.
//
// The claim under test is demand one: a row can be answered by *a Home Assistant
// script that returns the value*, written in Home Assistant's own script editor
// **embedded in the row** rather than linked from it. The adapter tests prove the
// rule (`module_host.answer_with_scripts`, `script_calls`); what they cannot prove
// is that a person can reach it -- that the menu offers it on a row, that picking
// it draws the editor *in the page*, that the editor is the real one, and that
// the module which installs afterwards really calls the script and reads what it
// handed back.
//
// Run: node scripts/_script.mjs
//
// **The row is chosen by the server, not by this file.** Which module has a row
// that can be answered is a fact about the house that cannot be written down in
// advance.
//
// **One step is driven without a real click, and it says so.** Choosing an entry
// in the card's "Set it to" menu is Home Assistant's own `ha-form` select -- its
// menu and its option list are HA's component, and driving that would be a test
// of HA rather than of this panel. What is dispatched is exactly the
// `value-changed` event the form emits when a person picks something. Everything
// after it is read back off the page and off the server: the editor, the script
// that lands on the record, and the automation Home Assistant ends up running.
//
// The iframe's *contents* are Home Assistant's own page and are not driven: what
// is checked is that the frame is there, in the page, on Home Assistant's own
// script route -- that it is an embed and not a link.
import { mkdirSync } from 'node:fs'
import { openPanel, all, click, sleep, BASE } from './_ui.mjs'

const SHOTS = `${process.env.TEMP ?? '/tmp'}/open-house-shots`
const SCRIPT_ID = 'open_house_walk_script'
const SCRIPT_ENTITY = `script.${SCRIPT_ID}`

/** Put the house back. Replaced once the walk has picked the row to work on. */
let restore = async () => true

const FAILURES = []
const ok = (label, pass, detail = '') => {
  console.log(`${pass ? 'ok  ' : 'FAIL'} ${label}${detail ? ` -- ${detail}` : ''}`)
  if (!pass) FAILURES.push(label)
}

const { browser, page, events } = await openPanel()
const readAll = (fn, arg = null) => page.evaluate(fn, arg)
const waitFor = (fn, arg = null, ms = 90000) =>
  page.waitForFunction(fn, arg, { timeout: ms, polling: 400 })
mkdirSync(SHOTS, { recursive: true })
const shot = (name) => page.screenshot({ path: `${SHOTS}/script-${name}.png` })

/** One `open_house/*` command over the panel's own websocket connection. */
const ws = (message) =>
  readAll((msg) => {
    const panel = window.__deepAll('open-house-panel')[0]
    return panel.hass.callWS(msg)
  }, message)

/** A call to Home Assistant's own API -- the automation and script config views. */
const api = (method, path, body = undefined) =>
  readAll(
    ([verb, url, data]) => {
      const panel = window.__deepAll('open-house-panel')[0]
      return panel.hass.callApi(verb, url, data)
    },
    [method, path, body],
  )

/** The module as the server reports it now, by slug and room. */
const hosted = async (slug, roomId) => {
  const listing = await ws({ type: 'open_house/modules/hosted' })
  return listing.modules.find((one) => one.slug === slug && one.room_id === roomId)
}

/**
 * A row that can be answered with logic, and the module hosting it.
 *
 * `bound_kind` is what tells a row a cast can be written over from one it cannot
 * (`canCastSetting`): a row already answered by a condition or a flow is showing
 * that answer, and a row holding a template is showing the template box.
 */
const candidate = async () => {
  const listing = await ws({ type: 'open_house/modules/hosted' })
  const modules = listing.modules ?? []
  // A module Home Assistant is not running has no automation to read at the end
  // of the walk, so one that is running is preferred -- but a module stuck
  // waiting for an answer has rows just as castable, and is the fallback rather
  // than skipped over entirely.
  const ordered = [
    ...modules.filter((one) => one.automation_id),
    ...modules.filter((one) => !one.automation_id),
  ]
  for (const module of ordered) {
    for (const setting of module.settings ?? []) {
      const answered =
        setting.cast != null || Boolean(setting.flow_id) || Boolean(setting.script_id)
      // **A row the card draws as a sentence has no field to put a cast beside.**
      // `boundElsewhere` says which those are: another module publishing into it,
      // and the device its room binds for a slot. Both are shown rather than
      // edited, and `output`/`slot` are two of the four kinds that do it.
      const blocked =
        setting.bound_kind === 'condition' ||
        setting.bound_kind === 'flow' ||
        setting.bound_kind === 'output' ||
        setting.bound_kind === 'slot'
      if (answered || blocked) continue
      // A script cannot answer a row the trigger names -- a trigger's entity_id is
      // matched rather than rendered (`module_host.input_in_trigger`), and the
      // server refuses it, so a row that would be refused is not offered here.
      if (setting.in_trigger) continue
      if (typeof setting.value === 'string' && /{{|{%/.test(setting.value)) continue
      return { module, setting }
    }
  }
  return null
}

/** Go to the page that carries a module's card, and wait for it to be drawn. */
const goToCard = async (module) => {
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
}

/** What one row of a card is showing, read off the page. */
const rowNow = (slug, name) =>
  readAll(
    ([want, setting]) => {
      const card = window
        .__deepAll('open-house-hosted-module')
        .find((one) => (one.module?.slug ?? '') === want)
      if (!card) return { error: `no card for ${want}` }
      const form = window.__deepAll(`ha-form[data-setting="${setting}"]`, card)[0]
      if (!form) return { error: `no row called ${setting}` }
      const editor = window.__deepAll('open-house-script', form.parentElement ?? form)[0]
      // The editor draws into the page's own DOM rather than a shadow root of its
      // own -- it reads its frame with `this.querySelector("iframe")` -- so the
      // bar and the modal below are its own children, not children of a shadow
      // root that is not there. It *can* be in the tree one frame before it has
      // rendered at all, which is why every read is guarded.
      const bar = editor?.querySelector('.embed-bar') ?? null
      const frame = editor?.querySelector('iframe') ?? null
      const modal = editor?.querySelector('.embed-modal') ?? null
      return {
        mode: form.data?.cast_mode ?? null,
        // The card's own words about the script: whether one is chosen yet, and
        // the button that opens HA's editor.
        editorDrawn: Boolean(editor),
        chosen: editor?.script ?? null,
        button: bar ? window.__deepText(bar).replace(/\s+/g, ' ').trim() : null,
        opened: Boolean(modal),
        frameUrl: frame ? new URL(frame.src, window.location.origin).pathname : null,
        // Where the frame has *got to*, which for a new script is how its id is
        // learned: HA's editor navigates itself once its Save is pressed.
        frameAt: (() => {
          try {
            return frame?.contentWindow?.location?.pathname ?? null
          } catch {
            return 'unreadable'
          }
        })(),
      }
    },
    [slug, name],
  )

/**
 * Choose an entry in a row's "Set it to" menu.
 *
 * Home Assistant's own select (see the header): what is dispatched is the event
 * the form emits when a person picks, carrying the whole new answer the way the
 * form does.
 */
const chooseMode = (slug, name, mode) =>
  readAll(
    ([want, setting, next]) => {
      const card = window
        .__deepAll('open-house-hosted-module')
        .find((one) => (one.module?.slug ?? '') === want)
      const form = window.__deepAll(`ha-form[data-setting="${setting}"]`, card)[0]
      if (!form) return false
      form.dispatchEvent(
        new CustomEvent('value-changed', {
          detail: { value: { ...form.data, cast_mode: next } },
          bubbles: true,
          composed: true,
        }),
      )
      return true
    },
    [slug, name, mode],
  )

/** Pick the script a row reads, the same way. */
const chooseScript = (slug, name, entityId) =>
  readAll(
    ([want, setting, id]) => {
      const card = window
        .__deepAll('open-house-hosted-module')
        .find((one) => (one.module?.slug ?? '') === want)
      const form = window.__deepAll(`ha-form[data-setting="${setting}"]`, card)[0]
      if (!form) return false
      form.dispatchEvent(
        new CustomEvent('value-changed', {
          detail: { value: { ...form.data, script: id } },
          bubbles: true,
          composed: true,
        }),
      )
      return true
    },
    [slug, name, entityId],
  )

/**
 * Press one button inside an element, by coordinates, hit-tested first.
 *
 * The element is found *through the row*, when a row is given: a page may hold
 * several cards and more than one of them may have an editor on a row, so the
 * first `open-house-script` in the document is whichever card comes first rather
 * than the one this walk is working on -- and pressing its button would open
 * another row's editor and read, on the page, as a button that did nothing.
 */
const pressIn = (selector, label, scope = null) =>
  readAll(
    ([sel, text, row]) => {
      const host = row
        ? (() => {
            const card = window
              .__deepAll('open-house-hosted-module')
              .find((one) => (one.module?.slug ?? '') === row[0])
            const form = window.__deepAll(`ha-form[data-setting="${row[1]}"]`, card)[0]
            return window.__deepAll(sel, form?.parentElement ?? document)[0]
          })()
        : window.__deepAll(sel)[0]
      if (!host) return { error: `no ${sel}` }
      const button = window
        .__deepAll('button', host)
        .find((b) => (b.textContent ?? '').trim() === text)
      if (!button) {
        return {
          error: `no button ${text}, only ${JSON.stringify(
            window.__deepAll('button', host).map((b) => (b.textContent ?? '').trim()),
          )}`,
        }
      }
      button.scrollIntoView({ block: 'center' })
      const r = button.getBoundingClientRect()
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
        x,
        y,
        reaches: !!hit && (holds(button, hit) || holds(hit, button)),
        hit: hit ? hit.tagName.toLowerCase() : 'nothing',
      }
    },
    [selector, label, scope],
  )

const pressOrFail = async (selector, label, wait = 1500, scope = null) => {
  const at = await pressIn(selector, label, scope)
  if (at.error) throw new Error(`${at.error} (in ${selector})`)
  if (!at.reaches) throw new Error(`${label} is behind ${at.hit}, so no click reaches it`)
  await page.mouse.click(at.x, at.y)
  await sleep(wait)
}

try {
  // -- The script the module will read, made first so the walk has one ---------
  // Home Assistant's own config view: the same call its script editor makes when
  // its Save is pressed. Made here rather than through the embedded editor
  // because the editor's *inside* is Home Assistant's page and this walk does not
  // drive it -- what this walk proves is that the panel opens it, in the page.
  await api('POST', `config/script/config/${SCRIPT_ID}`, {
    alias: 'Open House walk script',
    sequence: [{ stop: 'the value the walk asked for', response_variable: 'value' }],
  })
  // The entity appears a moment after the config is written -- Home Assistant
  // creates it on its own reload -- so the walk waits for it rather than looking
  // once and calling a house without it a house where the call did nothing.
  await waitFor((id) => {
    const panel = window.__deepAll('open-house-panel')[0]
    return panel.hass.states[id] !== undefined
  }, SCRIPT_ENTITY)
  console.log(`the house has ${SCRIPT_ENTITY}`)

  const found = await candidate()
  if (!found) {
    console.log('SKIP  no module in this house has a row a cast can be written over')
    for (const event of events) console.log(`  ${event}`)
    await browser.close()
    process.exit(0)
  }
  const { module, setting } = found
  console.log(`the house offers ${module.slug} / ${setting.name}`)

  // Take the cast back off the row. A no-op until the row is known, and then the
  // way the walk leaves the house as it found it -- including from the catch, so
  // a run that fell over half way is not a house that changed.
  restore = async () => {
    await chooseMode(module.slug, setting.name, 'none')
    for (let attempt = 0; attempt < 60; attempt += 1) {
      const now = await hosted(module.slug, module.room_id)
      const back = (now?.settings ?? []).find((entry) => entry.name === setting.name)
      if (!back?.script_id) return true
      await sleep(1000)
    }
    return false
  }

  await goToCard(module)
  const cards = await all(page, 'open-house-hosted-module')
  ok('the module has a card where it lives', cards.length > 0, `${cards.length} cards`)

  // -- The menu offers it -----------------------------------------------------
  const before = await rowNow(module.slug, setting.name)
  if (before.error) throw new Error(before.error)
  ok(
    'the row starts on its own field, with no script',
    before.mode === 'none' && before.editorDrawn === false,
    `mode=${before.mode}, editor=${before.editorDrawn}`,
  )
  const menu = await readAll(
    ([slug, name]) => {
      const card = window
        .__deepAll('open-house-hosted-module')
        .find((one) => (one.module?.slug ?? '') === slug)
      const form = window.__deepAll(`ha-form[data-setting="${name}"]`, card)[0]
      const item = (form?.schema ?? []).find((row) => row.name === 'cast_mode')
      return (item?.selector?.select?.options ?? []).map((option) => option.label)
    },
    [module.slug, setting.name],
  )
  ok(
    'the "Set it to" menu names Home Assistant script logic',
    menu.some((label) => label.includes('HAOS script logic')),
    JSON.stringify(menu),
  )

  // -- Choose it, and the editor is drawn in the row --------------------------
  const sent = await chooseMode(module.slug, setting.name, 'script')
  if (!sent) throw new Error(`no row called ${setting.name} to set the menu on`)
  await sleep(600)
  const afterPick = await rowNow(module.slug, setting.name)
  console.log(
    `  after the pick: mode=${afterPick.mode}, editor=${afterPick.editorDrawn}, ` +
      `button=${JSON.stringify(afterPick.button)}`,
  )
  // Waited for by what the row *shows*, not by the element being in the tree: the
  // element appears a frame before it renders, and a read taken then finds no bar
  // to quote and no button to press.
  await waitFor(
    ([slug, name]) => {
      const card = window
        .__deepAll('open-house-hosted-module')
        .find((one) => (one.module?.slug ?? '') === slug)
      const form = window.__deepAll(`ha-form[data-setting="${name}"]`, card)[0]
      const editor = window.__deepAll('open-house-script', form?.parentElement ?? document)[0]
      return Boolean(editor?.querySelector('.embed-bar'))
    },
    [module.slug, setting.name],
  )
  const chosen = await rowNow(module.slug, setting.name)
  ok(
    'picking it draws the script block on that row',
    chosen.editorDrawn === true,
    `mode=${chosen.mode}, chosen=${chosen.chosen}`,
  )
  ok(
    'and it says there is no script yet',
    (chosen.button ?? '').includes('no script yet'),
    chosen.button ?? '',
  )
  await shot('01-script-row')

  // -- The editor is embedded, in the page, on Home Assistant's own route ------
  await pressOrFail('open-house-script', 'Write a script', 4000, [module.slug, setting.name])
  const open = await rowNow(module.slug, setting.name)
  ok('the button opens an editor over the screen', open.opened === true, `${open.opened}`)
  ok(
    "and it is Home Assistant's own script editor, on its own route",
    open.frameUrl?.startsWith('/config/script/edit/') === true,
    `${open.frameUrl} (the frame has got to ${open.frameAt})`,
  )
  await shot('02-editor-open')

  // The editor is a page *inside this one*: an iframe in the panel's own tree,
  // which is the difference between embedding it and linking to it. Found
  // through the card and the row, because a page may hold more than one card and
  // the first editor on it belongs to whichever row has one.
  const embedded = await readAll(
    ([slug, name]) => {
      const card = window
        .__deepAll('open-house-hosted-module')
        .find((one) => (one.module?.slug ?? '') === slug)
      const form = window.__deepAll(`ha-form[data-setting="${name}"]`, card)[0]
      const editor = window.__deepAll('open-house-script', form?.parentElement ?? document)[0]
      const frame = editor?.querySelector('iframe') ?? null
      if (!frame) return null
      try {
        const doc = frame.contentWindow.document
        return {
          inPanel: true,
          route: frame.contentWindow.location.pathname,
          // Home Assistant's own root element, in the frame's own document: proof
          // that what loaded is Home Assistant and not an error page.
          homeAssistant: Boolean(doc.querySelector('home-assistant')),
          title: doc.title ?? '',
        }
      } catch (error) {
        return { inPanel: true, route: 'unreadable', homeAssistant: false, title: '' }
      }
    },
    [module.slug, setting.name],
  )
  ok(
    "Home Assistant's own editor is loaded inside it, not linked from a tab",
    embedded?.homeAssistant === true,
    `${embedded?.route} "${embedded?.title}"`,
  )

  // -- Close it again: nothing was saved, so the row still has no script -------
  await pressOrFail('open-house-script', 'Done', 1500, [module.slug, setting.name])
  const closed = await rowNow(module.slug, setting.name)
  ok(
    'closing the editor leaves the row as it was, because no script was saved',
    closed.opened === false && closed.chosen === '',
    `opened=${closed.opened}, chosen=${JSON.stringify(closed.chosen)}`,
  )

  // -- Pick the script the automation will call -------------------------------
  // The card saves on a debounce and the write rebuilds the module, so what is
  // waited for is the *server's* own answer rather than a sleep: the row carries
  // the script when the module has been built again with it.
  await chooseScript(module.slug, setting.name, SCRIPT_ENTITY)
  let saved = null
  let row = null
  for (let attempt = 0; attempt < 90; attempt += 1) {
    saved = await hosted(module.slug, module.room_id)
    row = (saved?.settings ?? []).find((entry) => entry.name === setting.name)
    if (row?.script_id === SCRIPT_ID) break
    await sleep(1000)
  }
  ok(
    'the script the person picked lands on the module',
    row?.script_id === SCRIPT_ID,
    `script_id=${JSON.stringify(row?.script_id)} (wanted ${SCRIPT_ID})`,
  )
  await shot('03-script-picked')

  // -- And the automation Home Assistant runs really calls it ------------------
  // A module whose room has not bound its slots yet has no automation at all: its
  // rows are just as answerable, and the cast lands on the record just the same,
  // but there is nothing installed to read the call back out of. That is said
  // rather than reported as a fault in the cast.
  if (!module.automation_id) {
    console.log(
      'SKIP  this house has no *installed* module with a castable row, so there is ' +
        'no automation to read the call back out of',
    )
  } else {
    const automationEntity = module.automation_id
    const document = await readAll(
      (entityId) => {
        const panel = window.__deepAll('open-house-panel')[0]
        const state = panel.hass.states[entityId]
        const configId = state?.attributes?.id
        if (!configId) return { error: `no config id on ${entityId}` }
        return panel.hass
          .callApi('GET', `config/automation/config/${configId}`)
          .then((doc) => ({ configId, doc }))
          .catch((failure) => ({ error: String(failure?.message ?? failure) }))
      },
      automationEntity,
    )
    if (document.error) throw new Error(document.error)
    const actions = document.doc?.actions ?? []
    const call = actions.find(
      (action) =>
        String(action?.service ?? action?.action ?? '') === SCRIPT_ENTITY ||
        String(action?.service ?? action?.action ?? '') === `script.${SCRIPT_ID}`,
    )
    ok(
      'the installed automation calls that script',
      call !== undefined,
      JSON.stringify(actions.slice(0, 2)).slice(0, 220),
    )
    ok(
      'and names the value it hands back, so the input can read it',
      String(call?.response_variable ?? '').startsWith('oh_'),
      `response_variable=${JSON.stringify(call?.response_variable)}`,
    )
    ok(
      'with the call first, before anything that would read it',
      actions[0] === call,
      `the first action is ${JSON.stringify(Object.keys(actions[0] ?? {}))}`,
    )
    console.log(`  the automation is ${automationEntity} (config ${document.configId})`)
    await shot('04-automation')
  }

  // -- Put the house back as it was found -------------------------------------
  // The cast is a *write* to the person's house: the row keeps a script, the
  // input is bound to what it hands back, and the module is rebuilt around it.
  // Left there, a walk is a change to a house rather than a reading of one -- and
  // the next run would find its own row already answered and pick a different
  // module, so the walk would not be runnable twice.
  const putBack = await restore()
  ok('the row is put back on its own field', putBack, 'the script is off it again')
  const cleaned = await hosted(module.slug, module.room_id)
  const left = (cleaned?.settings ?? []).find((entry) => entry.name === setting.name)
  ok(
    'and the module no longer holds a script for it',
    !left?.script_id,
    `script_id=${JSON.stringify(left?.script_id)}`,
  )
  if (putBack) {
    // The script was made by this walk and belongs to no one else; a house left
    // holding it grows a stray `script.open_house_walk_script` on every run.
    try {
      await api('DELETE', `config/script/config/${SCRIPT_ID}`)
      console.log(`  the walk's own ${SCRIPT_ENTITY} is taken out of the house`)
    } catch (trouble) {
      console.log(`  ${SCRIPT_ENTITY} was left in the house: ${String(trouble).slice(0, 120)}`)
    }
  }
} catch (error) {
  ok(`the walk ran to the end (${String(error).slice(0, 300)})`, false)
  // The house is put back even when the walk fell over: a failed run must not be
  // a house that changed.
  if (!(await restore())) console.log('  NOTE the row could not be put back')
}

for (const event of events) console.log(`  ${event}`)
console.log(FAILURES.length ? `\nFAILED: ${FAILURES.length}` : '\nall ok')
await browser.close()
process.exit(FAILURES.length ? 1 : 0)
