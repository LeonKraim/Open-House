// Scratch: walk the Dev tab's "Import as a module" screen in a real browser.
//
// The probe_hosting.py checks prove the *server* turns a blueprint into a
// running module. They cannot see the screen: whether the Dev tab offers the
// job at all, whether picking a blueprint fills the form a person answers,
// whether the blueprint's own variables are offered to publish, whether an
// input can be kept as a setting, and whether the setting that comes of that can
// be changed afterwards. That is what this walks -- by real mouse clicks and
// real keystrokes, so a control that renders behind something is a failure and
// not a pass.
//
// Run: node scripts/_host.mjs
//
// Two sources are used, and the split is deliberate. The corpus blueprint is
// read and answered as far as the person decides (a Keep switch ticked, its
// output candidates looked at) -- but not hosted, because three of its inputs
// have no default and filling them means driving Home Assistant's own entity
// picker, which is a test of that component rather than of this screen. What
// gets hosted instead is pasted YAML with one variable and no inputs. The
// settings editor is then driven on the module `probe_hosting.py` left behind,
// which is the one that really kept a setting and really publishes.
import { openPanel, text, click, type, tickField, typeField, sleep } from './_ui.mjs'

const SCREEN = 'open-house-host-module'
const FORM = `${SCREEN} ha-form`
// The address a **browser** opens Node-RED at, which is the one the panel is
// configured with and the one a link has to use. Different from the address Home
// Assistant pushes flows to -- that one is a compose service name and resolves
// nowhere a browser is -- so a link built from it passes every other check here
// and opens nothing for the person who clicks it. Matches `tools/ha/probe_flows.py`.
const NODE_RED_EDITOR = process.env.NODE_RED_EDITOR_URL ?? 'http://localhost:1880'
const PASTED =
  '{"alias": "Browser Walk Module", "trigger": [{"platform": "time", "at": "23:59:59"}], ' +
  '"action": [{"delay": {"seconds": 1}}, {"variables": {"walk_value": 7}}]}'
// A blueprint with one input and no default for it: the case where the module
// has nothing to build its automation from until somebody answers that input,
// and the case the screen used to make unsaveable. Written as a blueprint rather
// than as an automation because the input is the whole point -- and its one
// input is a text box, so the walk can answer it without driving Home
// Assistant's own entity picker, which is a test of that component.
//
// One line, like the source above, and for a reason: the editor indents what is
// typed into it, so pasted YAML arrives with an extra level on every line but
// the first and is refused as malformed. JSON is YAML's own subset, which makes
// this a blueprint the server reads like any other -- `{"!input": "who"}` is the
// marker `!input who` becomes once the tag is parsed, and is written that way
// because a tag cannot be spelled inside JSON.
const ASKED =
  '{"blueprint": {"name": "Browser Walk Asked", "input": {"who": {"name": "Who To Tell", ' +
  '"selector": {"text": {}}}}}, "trigger": [{"platform": "time", "at": "23:59:58"}], ' +
  '"action": [{"delay": {"seconds": 1}}, {"variables": {"walk_asked": {"!input": "who"}}}]}'

const { browser, page, events } = await openPanel()
const ok = (label, pass, detail = '') =>
  console.log(`${pass ? 'ok  ' : 'FAIL'} ${label}${detail ? ` -- ${detail}` : ''}`)
// A predicate runs in the page, so anything it needs is passed as an argument
// rather than closed over -- a closed-over constant is a ReferenceError there.
const waitFor = (fn, arg = null, ms = 90000) =>
  page.waitForFunction(fn, arg, { timeout: ms, polling: 500 })
// Elements read out of the page do not survive the trip back -- a DOM node
// serialises to nothing -- so every read maps to plain data inside the page and
// only the data crosses.
const readAll = (fn, arg = null) => page.evaluate(fn, arg)
// The replace box is ticked by hand rather than by a helper in `_ui.mjs`,
// because it is not an `ha-form` row: it is a checkbox this screen renders
// itself, and the row-finding helper is about Home Assistant's own form.
const tickReplace = () =>
  readAll(() => {
    const box = window.__deepAll('#host-replace')[0]
    if (box && !box.checked) box.click()
  })
// Open a room's settings page from the room list, by the name in its row, and
// wait for it to have finished reading the room: "the element is in the DOM" and
// "the element has rendered the room" are two moments, and a read taken in
// between sees a page with no bindings in it.
const openRoom = async (name) => {
  await click(page, '#tab-rooms', { wait: 1500 })
  await waitFor((wanted) => {
    const row = window
      .__deepAll('open-house-tab-rooms tbody tr')
      .find((r) => window.__deepText(r).trim().startsWith(wanted))
    const link = row?.querySelector('a')
    if (!link) return false
    link.scrollIntoView({ block: 'center' })
    const box = link.getBoundingClientRect()
    window.__roomAt = { x: box.x + box.width / 2, y: box.y + box.height / 2 }
    return true
  }, name)
  const at = await readAll(() => window.__roomAt)
  await page.mouse.click(at.x, at.y)
  await waitFor(() => {
    const settings = window.__deepAll('open-house-room-settings')[0]
    if (!settings) return false
    const words = window.__deepText(settings)
    if (words.includes('Reading the room')) return false
    return window
      .__deepAll('open-house-room-settings h2')
      .some((h) => (h.textContent ?? '').trim() === 'Devices')
  })
}

const shown = (sel) =>
  readAll((s) => window.__deepText(window.__deepAll(s)[0] ?? document.body).replace(/\s+/g, ' '), sel)

try {
  // -- the Dev tab, and the job that imports a module ----------------------
  await waitFor(() => window.__deepAll('#tab-dev').length > 0)
  await click(page, '#tab-dev', { wait: 1200 })
  await click(page, '.tabs .tab', { nth: 'Import as a module', wait: 1200 })
  ok('the Dev tab offers a job called "Import as a module"', (await text(page, '.tabs')) !== null)

  // -- a blueprint is chosen, and read -------------------------------------
  await click(page, '.tab', { nth: 'Blueprint', wait: 800, within: SCREEN })
  await waitFor(() => window.__deepAll('#host-blueprint').length > 0)
  const options = await readAll(() =>
    [...window.__deepAll('#host-blueprint option')].map((o) => o.value).filter(Boolean),
  )
  ok(
    'the blueprint picker lists the corpus',
    options.includes('MarqBarq/dynamic-lighting.yaml'),
    `${options.length} blueprints`,
  )
  await readAll(() => {
    const select = window.__deepAll('#host-blueprint')[0]
    select.value = 'MarqBarq/dynamic-lighting.yaml'
    select.dispatchEvent(new Event('change', { bubbles: true }))
  })
  await sleep(400)
  await click(page, 'button', { nth: 'Read this source', wait: 2500 })
  await waitFor((sel) => window.__deepAll(sel).length > 0, FORM)
  await sleep(900)

  const sections = await readAll(
    (sel) =>
      window.__deepAll(`${sel} h2`).map((h) => window.__deepText(h).replace(/\s+/g, ' ').trim()),
    SCREEN,
  )
  ok(
    'the screen shows the source, the inputs, the outputs and the save step -- and nothing else',
    sections.length === 4,
    sections.join(' | '),
  )

  const unfilledBanner = await readAll(
    (sel) =>
      window.__deepText(
        window.__deepAll(`${sel} .banner.info`)[0] ?? document.body,
      )
        .replace(/\s+/g, ' ')
        .trim(),
    SCREEN,
  )
  ok(
    'the inputs nothing fills yet are named, as options the module will wait for',
    unfilledBanner.includes('no default in the blueprint') &&
      unfilledBanner.includes('does not run until'),
    unfilledBanner.slice(0, 150),
  )

  const candidates = await readAll(
    (sel) => window.__deepAll(`${sel} .candidate`).map((r) => window.__deepText(r).replace(/\s+/g, ' ').trim().slice(0, 44)),
    SCREEN,
  )
  ok(
    "the blueprint's own variables are offered to publish",
    candidates.length >= 2,
    `${candidates.length}: ${candidates.slice(0, 4).join(' / ')}`,
  )

  // The other half of what may be published: not what the blueprint *names* but
  // what it *drives*. The fixture turns lights on and off with a brightness, a
  // colour temperature and a transition, and each of those is a service call
  // away from being readable -- offered as `service:light.turn_on` for the
  // devices it acted on and `service:light.turn_on:brightness` for the value it
  // set, so one name has to carry the colon.
  const offered = await readAll(
    (sel) => window.__deepAll(`${sel} [data-candidates] .candidate`).map((r) =>
      window.__deepText(r).replace(/\s+/g, ' ').trim(),
    ),
    SCREEN,
  )
  ok(
    'what the blueprint drives is offered too',
    offered.some((row) => row.startsWith('service:light.turn_on:brightness')),
    offered.filter((row) => row.startsWith('service:')).slice(0, 4).join(' / ') || 'none',
  )

  // The forms' own schemas are asked rather than counted in the DOM: one row per
  // input, plus a value row where the input has a value, plus exactly one Keep
  // switch -- and the switch has to exist for every single input, because the
  // inputs nobody kept are the ones answered once and frozen. Every form, not the
  // first: a row is a form of its own now (its title is written above it, which
  // is what a dropdown's own caption cannot be), so the total is a sum.
  const shape = await readAll((sel) => {
    const total = { asks: 0, keeps: 0, switches: 0, kept: 0 }
    for (const form of window.__deepAll(sel)) {
      const names = form.schema.map((item) => item.name)
      const keeps = names.filter((n) => n.startsWith('expose_'))
      total.asks += names.filter((n) => n.startsWith('how_')).length
      total.keeps += keeps.length
      total.kept += keeps.filter((n) => form.data[n] === true).length
      total.switches += window.__deepAll('ha-selector-boolean', form.shadowRoot).length
    }
    return total
  }, FORM)
  ok(
    'every input the blueprint asks for gets one Keep switch of its own',
    shape.asks > 0 && shape.keeps === shape.asks && shape.switches === shape.asks,
    `${shape.asks} inputs, ${shape.keeps} keeps, ${shape.switches} switches rendered`,
  )
  // Every input, not just the ones somebody thought to tick: what a person can
  // change about a module they just imported is the question this screen answers,
  // and answering it with "nothing" is what this replaced.
  ok(
    'and every one of them is kept by default',
    shape.kept === shape.asks,
    `${shape.kept} of ${shape.asks} kept`,
  )

  const labels = await readAll((sel) => {
    const boxes = window
      .__deepAll(sel)
      .flatMap((form) => window.__deepAll('ha-selector-boolean', form.shadowRoot))
    return boxes
      .slice(0, 3)
      .map((box) => window.__deepText(box.getRootNode().host).replace(/\s+/g, ' ').trim().slice(0, 60))
  }, FORM)
  ok(
    'each Keep switch says what keeping does -- the title above it names the input',
    labels.length === 3 && labels.every((l) => l === 'Keep as a setting'),
    labels[0],
  )

  // The screen's own state is read rather than its labels, because the question
  // is which *input* a control belongs to and the label is the blueprint's
  // prose. The state is what the screen decided; each row is addressed by the
  // input it was rendered for (`data-input`), which is what makes a control's
  // own form findable without counting rows.
  const before = await readAll(() => {
    const screen = window.__deepAll('open-house-host-module')[0]
    return {
      inputs: screen.decisions.map((d, i) => ({
        i,
        name: d.input.name,
        title: d.input.title,
        how: d.how,
      })),
      slots: screen.slots.map((s) => s.name),
      licences: screen.reading?.licences ?? [],
    }
  })

  // Which field that switch is, is asked of the row rather than guessed from the
  // order: a Keep switch belonging to the wrong input looks identical on the
  // screen and keeps the wrong thing on the module.
  const rowOf = (at) =>
    readAll((sel) => {
      const form = window.__deepAll(sel)[0]
      const name = form?.schema.find((item) => item.name.startsWith('expose_'))?.name ?? null
      return {
        field: name,
        value: name ? form.data[name] : null,
        title: window
          .__deepText(window.__deepAll(sel.replace(/ ha-form$/, ' .label'))[0] ?? document.body)
          .trim(),
      }
    }, `ha-form[data-input="${at}"]`)

  const weather = before.inputs.find((row) => row.name === 'weather_entity')
  const unkept = await tickField(page, {
    form: `ha-form[data-input="${weather.i}"]`,
    label: 'Keep as a setting',
  })
  ok('a kept input can be unticked', unkept.reaches, `hit ${unkept.hit}`)
  const off = await rowOf(weather.i)
  ok(
    'and it is that input the screen now holds as one to answer once',
    off.field === `expose_${weather.i}` && off.value === false,
    `${off.field} = ${off.value} | ${off.title}`,
  )
  const others = await readAll((mine) => {
    let off = 0
    for (const form of window.__deepAll('open-house-host-module ha-form')) {
      const name = form.schema.find((item) => item.name.startsWith('expose_'))?.name
      if (name && name !== mine && form.data[name] === false) off += 1
    }
    return off
  }, off.field)
  ok('and it left every other input kept', others === 0, `${others} other input(s) also unticked`)

  // -- answering an input with a *slot* instead of a device ---------------
  // The screen's own state is read rather than its labels, because the question
  // is which *input* a control belongs to and the label is the blueprint's
  // prose. The state is what the screen decided; the form is where a person
  // changes it, and the change is dispatched the way the form's own control
  // would dispatch it -- so what is exercised is the screen's handler, and what
  // is stepped over is Home Assistant's dropdown widget.
  const howMenu = await readAll((sel) => {
    const form = window.__deepAll(sel)[0]
    const item = form.schema.find((row) => row.name === 'how_0')
    return (item?.selector?.select?.options ?? []).map((o) => o.value)
  }, FORM)
  ok(
    'the menu for filling an input offers answering it with a slot',
    howMenu.includes('slot'),
    howMenu.join(', '),
  )
  ok(
    "and the screen holds the house's own slot names to offer",
    before.slots.includes('ambient_light_sensor'),
    `${before.slots.length}: ${before.slots.slice(0, 6).join(', ')}`,
  )
  ok(
    'and the licences a module may be saved under',
    before.licences.includes('mit') && before.licences.includes('no_licence'),
    before.licences.join(', '),
  )

  const lux = before.inputs.find((row) => row.name === 'lux_sensor')
  const lights = before.inputs.find((row) => row.name === 'lights')
  // The blueprint's third input with no default. It takes a weather entity and
  // the walk answers it with a name this instance does not have, which is what
  // the live probe does: the blueprint only lowercases its state, and an absent
  // entity reads `unknown`, which is a real answer rather than a missing one.
  // The other two are answered with a device -- one takes a target and one an
  // entity -- for the same reason as the slot: the picker they would be typed
  // into is Home Assistant's, and this walk is about what the screen does with
  // the answer rather than about that component.
  //
  // One row at a time, because a row is a form of its own: an answer is
  // dispatched to the row it belongs to, the way that row's own control would
  // dispatch it, and the next waits for the screen to have read the last.
  for (const [at, how, value] of [
    [lux.i, 'slot', 'ambient_light_sensor'],
    [lights.i, 'entity', 'light.kitchen'],
    [weather.i, 'literal', 'weather.nowhere'],
  ]) {
    await readAll(([sel, at, how, value]) => {
      const form = window.__deepAll(sel)[0]
      if (!form) return
      form.dispatchEvent(
        new CustomEvent('value-changed', {
          detail: { value: { ...form.data, [`how_${at}`]: how, [`value_${at}`]: value } },
          bubbles: true,
          composed: true,
        }),
      )
    }, [`ha-form[data-input="${at}"]`, at, how, value])
    await sleep(1200)
  }
  await sleep(1200)

  // -- casting any answer ------------------------------------------------
  // The answer "this device, but what the module should be given is something
  // worked out from it": Home Assistant's own condition editor, or a template it
  // renders into the field the automation reads. Offered *beside* the choice and
  // not instead of it, which is what makes it on demand -- empty gives the
  // choice back.
  //
  // **Every row has it.** Which rows offer a cast is the question this block
  // used to ask, and the answer it was written with -- devices yes, free text no
  // -- was wrong in two ways: the row holding a literal wanted the menu as much
  // as the row holding a device, and the row a trigger *names* is the one a
  // person most needs a condition on, since a template there installs and never
  // fires. So the menu is on every row and the editor under it is chosen per
  // row, which is what the checks below walk: the menu is there everywhere, it
  // opens on "nothing", and picking an editor swaps in that editor's control.
  //
  // Read once the screen has taken all three answers, not after a fixed pause:
  // each answer is a server round trip, and the first read of this block caught
  // the slot row still showing its blueprint default while the device row was
  // already answered -- a sleep long enough for one row and not for three is the
  // kind of check that passes or fails on machine speed.
  await waitFor(
    (want) => {
      const screen = window.__deepAll('open-house-host-module')[0]
      return want.every((row) => screen.decisions[row.i]?.how === row.how)
    },
    [
      { i: lux.i, how: 'slot' },
      { i: lights.i, how: 'entity' },
      { i: weather.i, how: 'literal' },
    ],
  )
  const readCastRows = () =>
    readAll(() => {
      const screen = window.__deepAll('open-house-host-module')[0]
      return screen.decisions.map((decision, i) => {
        const form = window.__deepAll(`ha-form[data-input="${i}"]`)[0]
        const menu = form?.schema.find((row) => row.name === `cast_mode_${i}`)
        const box = form?.schema.find((row) => row.name === `cast_${i}`)
        return {
          i,
          name: decision.input.name,
          how: decision.how,
          watched: decision.input.in_trigger,
          menu: menu !== undefined,
          options: (menu?.selector?.select?.options ?? []).map((row) => row.value),
          editor:
            box === undefined
              ? 'none'
              : box.selector?.condition !== undefined
                ? 'condition'
                : box.selector?.template !== undefined
                  ? 'template'
                  : 'other',
          label: box ? form.computeLabel(box) : '',
        }
      })
    })
  const castRows = await readCastRows()
  const castRow = (name) => castRows.find((row) => row.name === name)
  ok(
    'every row of the import is offered a cast, not only the ones holding a device',
    castRows.every((row) => row.menu === true),
    JSON.stringify(castRows.map((row) => [row.name, row.menu])),
  )
  ok(
    'and the row that is already free text gets it too, which was the complaint',
    castRow('weather_entity')?.menu === true,
    JSON.stringify(castRow('weather_entity')),
  )
  ok(
    'the menu names both editors, so neither is the hidden one',
    castRows.every(
      (row) =>
        row.options.includes('none') &&
        row.options.includes('condition') &&
        row.options.includes('template'),
    ),
    JSON.stringify(castRow('lights')?.options),
  )
  ok(
    'and every row opens on "nothing", because no row is cast until someone says so',
    castRows.every((row) => row.editor === 'none'),
    JSON.stringify(castRows.map((row) => [row.name, row.editor])),
  )

  // Choosing an editor, the way a person does: the menu's own value-changed,
  // and then the editor's control is read back off the schema it produced.
  const setCastMode = (at, mode) =>
    readAll(
      ([sel, index, value]) => {
        const form = window.__deepAll(sel)[0]
        if (!form) return
        form.dispatchEvent(
          new CustomEvent('value-changed', {
            detail: { value: { ...form.data, [`cast_mode_${index}`]: value } },
            bubbles: true,
            composed: true,
          }),
        )
      },
      [`ha-form[data-input="${at}"]`, at, mode],
    )
  // **The fourth editor, on every row.** It used to be offered only where the
  // answer was a device -- Open House pushes a state trigger on the entity a row
  // resolves to, and a row holding a number has none -- which took the cast away
  // from exactly the inputs most worth programming. The output node, which is the
  // half that hands a value back to this input, is made whichever way the row was
  // answered, so the option is on all of them and only the *trigger* is narrowed.
  // The word in the menu is Node-RED's, because the editor behind it is Node-RED's
  // own.
  const withoutFlow = castRows.filter((row) => !row.options.includes('nodered'))
  ok(
    'the menu names the flow editor on every row, not only the ones holding devices',
    withoutFlow.length === 0,
    JSON.stringify(withoutFlow.map((row) => row.name)),
  )
  await setCastMode(lights.i, 'nodered')
  await sleep(1000)
  const flowRow = await readAll((at) => {
    const form = window.__deepAll(`ha-form[data-input="${at}"]`)[0]
    return {
      names: form ? form.schema.map((row) => row.name) : null,
      labels: form
        ? form.schema.map((row) => [row.name, form.computeLabel(row)])
        : null,
    }
  }, lights.i)
  // **The field goes, exactly as it does for the other two casts.** A flow is
  // the answer, not a remark about the answer, so the row draws it in the
  // field's place: what the flow writes, whether a device arrives wired into it,
  // and where to build the rest. A row still offering its own selector beside the
  // flow reads as two answers to one question, and the dead one is on top.
  ok(
    'picking it takes the row\'s own field away, like every other cast',
    !(flowRow.names ?? []).includes(`value_${lights.i}`),
    JSON.stringify(flowRow.names),
  )
  // What is drawn instead, read off the DOM rather than off the form, because it
  // is a block of its own beside it -- and read *through* it, because the editor
  // is a Node-RED element of this panel's own and the iframe is inside it.
  const heldFlow = await readAll((at) => {
    const scope = window.__deepAll(`[data-input="${at}"]`)[0]
    if (!scope) return null
    const block = window.__deepAll(`[data-flow]`, scope)[0]
    if (!block) return null
    const embed = window.__deepAll('open-house-node-red', scope)[0]
    const frame = embed ? window.__deepAll('iframe', embed)[0] : null
    const link = embed ? window.__deepAll('a', embed)[0] : null
    return {
      text: window.__deepText(block).replace(/\s+/g, ' '),
      src: frame ? frame.getAttribute('src') : null,
      href: link ? link.getAttribute('href') : null,
    }
  }, lights.i)
  // `_ lights` and not `_lights`: lit-html puts the input's name in a hole of
  // its own, so the text that comes back has a space where the two halves of the
  // entity id meet. Read as one string it would be a check that fails on the
  // renderer's whitespace rather than on the name.
  ok(
    'and draws the flow in its place, named after the entity it will write',
    heldFlow !== null &&
      /sensor\.open_house_flow_<module>_\s*lights/.test(heldFlow.text),
    JSON.stringify(heldFlow),
  )
  // **The editor itself, in the page.** It is Node-RED's own, so what this can
  // check is that the frame is pointed at it -- at the address a *browser* opens,
  // which is the one the panel is configured with and not the compose service
  // name Home Assistant pushes to. An address nothing resolves would load an
  // error page into a frame that looks exactly like the right one from here.
  ok(
    "and embeds Node-RED's own editor under it, at the address the panel was given",
    heldFlow !== null &&
      heldFlow.src !== null &&
      heldFlow.src.startsWith(`${NODE_RED_EDITOR}/`),
    JSON.stringify(heldFlow?.src),
  )
  // The way out is the same address as the way in, so the link cannot drift from
  // the frame: both are the one `src` the element built.
  ok(
    'with the same address as the link that opens it in its own tab',
    heldFlow !== null && heldFlow.href === heldFlow.src,
    JSON.stringify([heldFlow?.href, heldFlow?.src]),
  )
  ok(
    'and it says which half arrives built, for a row holding a device',
    heldFlow !== null && /wired into its input node/i.test(heldFlow.text),
    JSON.stringify(heldFlow?.text),
  )
  await setCastMode(lights.i, 'template')
  await sleep(1000)
  const templateRow = (await readCastRows()).find((row) => row.name === 'lights')
  ok(
    'picking the template editor swaps in the template box',
    templateRow?.editor === 'template',
    JSON.stringify(templateRow),
  )
  ok(
    'and the box that came in its place is named for what it holds',
    templateRow?.label === 'The template',
    templateRow?.label,
  )
  // **One control per row, and the row's own field is not one of them.** A cast
  // is the answer rather than a remark about it, so the choice it replaced stops
  // being drawn: two controls for one answer reads as two answers, and the one
  // above is the dead one.
  const swapped = await readAll((at) => {
    const form = window.__deepAll(`ha-form[data-input="${at}"]`)[0]
    return {
      names: form ? form.schema.map((row) => row.name) : null,
      fields: form ? form.schema.filter((row) => row.name === `value_${at}`).length : -1,
    }
  }, lights.i)
  ok(
    'and the field it replaced is taken off the row rather than left above it',
    swapped.fields === 0,
    JSON.stringify(swapped),
  )

  // Typed into the row and read back off the screen, the way every other answer
  // here is, and then cleared again -- the choice has to come back, and the
  // module the walk goes on to save has to be the one it was answering for.
  const cast = `{{ true if is_state('input_text.home_state', 'sleep') else false }}`
  const typeCast = (value) =>
    readAll(
      ([sel, at, text]) => {
        const form = window.__deepAll(sel)[0]
        if (!form) return
        form.dispatchEvent(
          new CustomEvent('value-changed', {
            detail: { value: { ...form.data, [`cast_${at}`]: text } },
            bubbles: true,
            composed: true,
          }),
        )
      },
      [`ha-form[data-input="${lights.i}"]`, lights.i, value],
    )
  await typeCast(cast)
  await sleep(1000)
  const heldCast = await readAll((at) => {
    const screen = window.__deepAll('open-house-host-module')[0]
    return screen.decisions[at]?.cast ?? null
  }, lights.i)
  ok(
    'what is typed into it is what the screen holds as the answer',
    heldCast === cast,
    heldCast ?? '(nothing held)',
  )
  await typeCast('')
  await sleep(1000)
  const cleared = await readAll((at) => {
    const screen = window.__deepAll('open-house-host-module')[0]
    const form = window.__deepAll(`ha-form[data-input="${at}"]`)[0]
    return {
      cast: screen.decisions[at]?.cast ?? null,
      value: screen.decisions[at]?.value ?? null,
      editor:
        form?.schema.find((row) => row.name === `cast_${at}`)?.selector?.template !== undefined
          ? 'template'
          : 'none',
    }
  }, lights.i)
  ok(
    'and clearing it gives the choice back rather than emptying the answer',
    !cleared.cast && cleared.value === 'light.kitchen' && cleared.editor === 'template',
    JSON.stringify(cleared),
  )

  // The other editor, on the same row: Home Assistant's own condition builder,
  // which is the one that works on a row a trigger names -- and the one the
  // person asked for instead of hand-written expressions.
  await setCastMode(lights.i, 'condition')
  await sleep(1000)
  const conditionRow = (await readCastRows()).find((row) => row.name === 'lights')
  ok(
    'picking the condition editor swaps in Home Assistant\'s own condition builder',
    conditionRow?.editor === 'condition',
    JSON.stringify(conditionRow),
  )
  const conditionHeld = await readAll((at) => {
    const screen = window.__deepAll('open-house-host-module')[0]
    const form = window.__deepAll(`ha-form[data-input="${at}"]`)[0]
    // The condition selector's own control, drawn by Home Assistant: the screen
    // hands the row a `{condition: {}}` selector rather than a box of its own,
    // which is what "Home Assistant's editor" means and is the whole of the ask.
    return {
      selector: form?.schema.find((row) => row.name === `cast_${at}`)?.selector ?? null,
      answered: screen.decisions[at]?.condition ?? null,
    }
  }, lights.i)
  ok(
    'and it is Home Assistant\'s selector, with nothing invented around it',
    typeof conditionHeld.selector?.condition === 'object',
    JSON.stringify(conditionHeld),
  )
  // **And it is a builder, not a blank space.** Home Assistant's condition
  // editor reads every one of its labels through `hass.localize`, and the
  // strings for it are not in the translation fragment a `panel_custom` panel is
  // served -- it gets `custom`, the automation editor's are in `config`. So the
  // panel asks for that fragment at its root. Without it the editor still
  // renders, and renders as a bare `+` and a menu of empty rows, which is what a
  // person reads as "there is no UI for this" rather than as a missing word.
  const condUi = await readAll((at) => {
    const form = window.__deepAll(`ha-form[data-input="${at}"]`)[0]
    const box = form
      ? window.__deepAll('ha-automation-condition', form)[0]
      : null
    const button = box ? window.__deepAll('ha-button', box)[0] : null
    return {
      editor: !!box,
      button: (button?.textContent ?? '').trim(),
      height: box ? Math.round(box.getBoundingClientRect().height) : 0,
    }
  }, lights.i)
  ok(
    'and its one control is labelled, which is what a loaded translation buys',
    condUi.editor && condUi.button === 'Add condition',
    JSON.stringify(condUi),
  )
  // Clicking it opens the type menu -- the thing a person actually builds a
  // condition in, and the thing whose entries are blank without those strings.
  await readAll((at) => {
    const form = window.__deepAll(`ha-form[data-input="${at}"]`)[0]
    const button = window.__deepAll('ha-automation-condition', form)[0]
    window.__deepAll('ha-button', button)[0]?.click()
  }, lights.i)
  await sleep(2500)
  // The dialog opens on "by target" -- pick a device, then pick what about it.
  // The type list, the one whose entries are the condition names, is behind the
  // other half of the header's toggle. A person switches it; so does the walk.
  const condTypes = await readAll(() => {
    const dialog = window.__deepAll('add-automation-element-dialog')[0]
    if (!dialog) return { dialog: false, text: '' }
    const toggle = window.__deepAll('ha-button-toggle-group', dialog)[0]
    const buttons = toggle
      ? window.__deepAll('ha-button', toggle.shadowRoot ?? toggle)
      : []
    const byType = buttons.find((one) => /^by type$/i.test((one.textContent ?? '').trim()))
    byType?.click()
    return {
      dialog: true,
      options: buttons.map((one) => (one.textContent ?? '').trim()),
      switched: !!byType,
    }
  })
  await sleep(1500)
  const condMenu = await readAll(() => {
    const dialog = window.__deepAll('add-automation-element-dialog')[0]
    if (!dialog) return { dialog: false, rows: [] }
    // The picker's entries are `ha-list-item-button`s inside the dialog. Every
    // one of them is a word this panel did not have until it asked for the
    // fragment: unlabelled, they were the blank rows a person read as "there is
    // no UI for this".
    const rows = window
      .__deepAll('ha-list-item-button', dialog)
      .map((one) => (one.textContent ?? '').trim())
    return { dialog: true, rows }
  })
  ok(
    'and the condition types it offers are named, so a condition can be built',
    condMenu.dialog &&
      condMenu.rows.length >= 8 &&
      condMenu.rows.every((one) => one.length > 0),
    JSON.stringify({ ...condMenu, rows: condMenu.rows.slice(0, 8), ...condTypes }),
  )
  // The dialog is Home Assistant's own and it covers the screen, so it has to be
  // dismissed before the walk goes on -- and this is the walk's own click that
  // opened it, not anything the screen did.
  await page.keyboard.press('Escape')
  await sleep(800)
  await setCastMode(lights.i, 'none')
  await sleep(1000)

  const control = await readAll(([sel, name]) => {
    const form = window.__deepAll(sel)[0]
    const item = form.schema.find((row) => row.name === name)
    const select = item?.selector?.select
    return select
      ? {
          mode: select.mode,
          typed: select.custom_value === true,
          options: (select.options ?? []).map((o) => o.value),
        }
      : null
  }, [`ha-form[data-input="${lux.i}"]`, `value_${lux.i}`])
  ok(
    'answering with a slot swaps the value control for a menu of the house’s slots',
    control !== null && control.typed && control.options.includes('ambient_light_sensor'),
    JSON.stringify(control)?.slice(0, 120),
  )
  ok(
    'and the menu is one a name can be typed into as well as picked from',
    control?.typed === true,
    `custom_value=${control?.typed}`,
  )
  // Read only the banner, not the screen: the whole screen contains the row the
  // answer was typed into, so a `shown` that fell back to the body would "find"
  // the input's own name and pass on the strength of the thing it is asking
  // about.
  const unfilledBanners = await readAll((sel) =>
    window.__deepAll(`${sel} .banner.info`).map((b) =>
      window.__deepText(b).replace(/\s+/g, ' ').trim(),
    ),
  SCREEN)
  ok(
    'an input answered with a slot is no longer one nothing fills',
    !unfilledBanners.some((words) => words.includes(lux.title)),
    unfilledBanners.join(' | ') || '(nothing left unfilled)',
  )
  // One of the three answers above is a device -- "Lights to Control" is answered
  // with `light.kitchen` -- and the screen has to say so, because a device is one
  // house's. The banner is the whole of that: it is the one fact about a module
  // that the person sending the file can see and the person receiving it cannot,
  // and it names the way out rather than only the problem.
  const pinnedBanner = await readAll((sel) => {
    const banner = window.__deepAll(`${sel} .banner.warn`)[0]
    return banner ? window.__deepText(banner).replace(/\s+/g, ' ').trim() : ''
  }, SCREEN)
  ok(
    'an answer that is a device of this house is called out, with what to do about it',
    pinnedBanner.includes('names devices from your house') &&
      pinnedBanner.includes("the room's lux sensor"),
    pinnedBanner.slice(0, 120) || '(no warning at all)',
  )
  const blocked = await readAll((sel) => {
    const button = window
      .__deepAll(`${sel} button`)
      .find((b) => (b.textContent ?? '').includes('Save this module'))
    return button?.disabled ?? null
  }, SCREEN)
  ok('and a module waiting on a slot is not blocked from being saved', blocked === false, `disabled=${blocked}`)

  // Saving is the import screen's last step now, and it installs nothing: what a
  // person made is a module the house *offers*, and the room it goes into is
  // chosen afterwards, in the Store tab, where the same module can be given to
  // five rooms and downloaded for somebody else.
  await type(page, '#host-title', 'Browser Walk By Slot')
  await type(page, '#host-author', 'the walk')
  await type(page, '#host-version', '0.3.0')
  await readAll(() => {
    const select = window.__deepAll('#host-licence')[0]
    select.value = 'mit'
    select.dispatchEvent(new Event('change', { bubbles: true }))
  })
  // Ticked, so a second run of this walk replaces what the first one saved
  // rather than being refused. The refusal itself is proved by the live probe,
  // which needs the unticked case and can assert on the sentence; what this walk
  // is about is the screen, so it takes the path a person takes to update.
  await tickReplace()
  await sleep(300)
  const hostedBefore = await readAll((sel) => window.__deepAll(sel)[0].hosted.map((m) => m.slug), SCREEN)
  await click(page, 'button', { nth: 'Save this module', wait: 8000 })
  await sleep(3000)
  const savedDefinition = await readAll((sel) => {
    const screen = window.__deepAll(sel)[0]
    return {
      notice: window.__deepText(window.__deepAll(`${sel} .banner.info`)[0] ?? document.body).trim(),
      error: window.__deepText(window.__deepAll(`${sel} .banner.error`)[0] ?? document.body).trim(),
      hosted: screen.hosted.map((m) => m.slug),
    }
  }, SCREEN)
  ok(
    'saving it answers with the name it was saved under, and says where to find it',
    savedDefinition.notice.includes('Saved as browser_walk_by_slot') &&
      savedDefinition.notice.includes('Store tab'),
    `${savedDefinition.notice} | error: ${savedDefinition.error}`,
  )
  ok(
    'and saving it installed it nowhere',
    // Compared before and after rather than searched for, because a run of this
    // walk leaves the module it installed behind and an installation cannot be
    // removed over the API -- so "it is not in the list" is false on a rerun for
    // a reason that has nothing to do with saving.
    savedDefinition.hosted.join(', ') === hostedBefore.join(', '),
    `before: ${hostedBefore.join(', ')} | after: ${savedDefinition.hosted.join(', ')}`,
  )

  // -- a source with nothing to answer is hosted --------------------------
  await click(page, '.tab', { nth: 'Paste YAML', wait: 800, within: SCREEN })
  await click(page, 'ha-code-editor', { wait: 600, within: SCREEN })
  await page.keyboard.type(PASTED, { delay: 8 })
  await sleep(700)
  await click(page, 'button', { nth: 'Read this source', wait: 2500 })
  // Waited for by the section this source's own answer is in, and not by the
  // form: a source with nothing to answer renders the sentence in place of the
  // form, and the form was only ever found here because the screen used to draw
  // the house's own cards below it too -- one of those cards' settings form
  // answered a wait about *this* source. The read is over when the screen says
  // what this source asks for, which is what the assertion below then reads.
  await waitFor(
    (sel) =>
      window
        .__deepText(window.__deepAll(`${sel} p.muted`)[0] ?? document.body)
        .includes('asks for nothing'),
    SCREEN,
  )
  await sleep(900)
  const pastedSections = await readAll(
    (sel) => window.__deepAll(`${sel} h2`).map((h) => window.__deepText(h).replace(/\s+/g, ' ').trim()),
    SCREEN,
  )
  ok(
    'a source with nothing to answer says so instead of asking',
    pastedSections.length >= 4 && (await shown(`${SCREEN} p.muted`)).includes('asks for nothing'),
    pastedSections.join(' | '),
  )
  await readAll(() => {
    const box = window.__deepAll('open-house-host-module .candidate input[type=checkbox]')[0]
    if (box && !box.checked) box.click()
  })
  await sleep(300)
  await type(page, '#host-title', 'Browser Walk Module')
  await tickReplace()
  await sleep(300)
  await click(page, 'button', { nth: 'Save this module', wait: 5000 })
  await sleep(2500)
  const state = await readAll((sel) => {
    const at = (text) =>
      window.__deepText(window.__deepAll(`${sel} .banner.${text}`)[0] ?? document.body).trim().slice(0, 200)
    const save = window
      .__deepAll(`${sel} button`)
      .find((b) => (b.textContent ?? '').includes('Save this module'))
    return { notice: at('info'), error: at('error'), disabled: save?.disabled ?? null }
  }, SCREEN)
  ok(
    'saving a source with nothing to answer answers with what happened',
    state.notice.includes('Saved as browser_walk_module'),
    `${state.notice} | error: ${state.error} | blocked: ${state.disabled}`,
  )

  // -- the store: what was saved is offered, and placing is a second act ----
  // Saving a source keeps it; it does not put it anywhere, and neither does the
  // store, which is the house's shelf. Putting it somewhere is the *room's* act,
  // through "Add module to room". The two halves are checked apart here, because
  // keeping them apart is the whole of what this walk is for.
  //
  // The room is asked for rather than guessed at: this is about the *waiting*
  // state, so it has to be one whose lux slot is spare, and which rooms those
  // are is a fact about the house rather than about this script.
  await click(page, '#tab-store', { wait: 2500 })
  const spare = await readAll(async () => {
    const screen = window.__deepAll('open-house-tab-store')[0]
    const fallback = []
    for (const candidate of await screen.client.rooms()) {
      let detail = null
      try {
        detail = await screen.client.room(candidate.id)
      } catch (error) {
        continue
      }
      const row = (detail.bindings ?? []).find((b) => b.slot === 'ambient_light_sensor')
      const where = { id: candidate.id, name: candidate.name, offers: row !== undefined }
      if (row && !row.entity_id) return where
      if (!row) fallback.push(where)
    }
    return fallback[0] ?? null
  })
  ok(
    'a room whose lux slot is spare, so the module has something to wait for',
    spare !== null,
    spare ? `${spare.name} (${spare.id})` : 'every room already binds ambient_light_sensor',
  )
  // Whatever an earlier run left standing is taken back out first, through the
  // room's own Remove button. This walk runs against a house that keeps what it
  // is given: a second run finds the last one's copy still in its room, and the
  // assertions below are about a module that is in no room. Driving the removal
  // rather than assuming a clean house is also what keeps the two assertions
  // after it -- the store row, and adding it again -- meaning the same thing on
  // every run.
  const leftOver = await readAll(async () => {
    const screen = window.__deepAll('open-house-tab-store')[0]
    const rows = await screen.client.modulesStore()
    // Both of the modules this walk places, because both are placed into the
    // spare room and a run that ended early -- or in the middle of the section
    // that answers a module's own option -- leaves the last one's copy behind.
    // A house that keeps what it is given is the house this runs against.
    return ['browser_walk_by_slot', 'browser_walk_asked'].flatMap(
      (slug) => rows.store.find((row) => row.slug === slug)?.deployed ?? [],
    )
  })
  for (const where of leftOver) {
    await openRoom(where.room_name)
    await click(page, `#unhost-${where.slug}`, { wait: 8000 })
    await sleep(2500)
  }
  if (leftOver.length > 0) {
    ok(
      'a module an earlier run left in a room is taken back out of it',
      true,
      leftOver.map((where) => where.slug).join(', '),
    )
    await click(page, '#tab-store', { wait: 2500 })
  }

  // Nothing below this point means anything without a room to add into: a
  // house-wide placement would answer a different question, and would sit in
  // every room of the dev house afterwards.
  if (!spare) throw new Error('no room to add a waiting module to')
  // Waited for rather than slept past: the tab is built again when it is
  // switched to, and a read taken in that window reports a store with no rows in
  // it rather than a store that has not answered.
  await waitFor((slug) => {
    const screen = window.__deepAll('open-house-tab-store')[0]
    return (
      !!screen &&
      window
        .__deepAll('.nested', screen)
        .some((row) => window.__deepText(row).includes(slug))
    )
  }, 'browser_walk_by_slot')
  const storeRows = await readAll(() => {
    const screen = window.__deepAll('open-house-tab-store')[0]
    const rows = window.__deepAll('.nested', screen)
    const mine = (slug) =>
      rows.find((row) => window.__deepText(row).includes(slug)) ?? null
    const slot = mine('browser_walk_by_slot')
    const pasted = mine('browser_walk_module')
    // Every chip on a row, not the first: a saved module's row carries more than
    // one -- that it is installed, and whether it is pinned to this house's own
    // devices -- and reading the first would answer about the wrong one the
    // moment a chip was added ahead of it.
    const chips = (row) =>
      row ? window.__deepAll('.chip', row).map((c) => window.__deepText(c).trim()) : []
    return {
      slotChips: chips(slot),
      pastedChips: chips(pasted),
      slotText: slot ? window.__deepText(slot).replace(/\s+/g, ' ').trim() : null,
      // What a row may be pressed to do. A store row is a filing cabinet: the
      // one act it offers is downloading the file, and anything that placed the
      // module from here would be the two acts collapsed back into one.
      slotButtons: slot
        ? [...slot.querySelectorAll('button')].map((b) => (b.textContent ?? '').trim())
        : [],
    }
  })
  ok(
    'the store lists the module that was just saved, with what it reaches through and the version it was saved at',
    (storeRows.slotText ?? '').includes('ambient_light_sensor') &&
      (storeRows.slotText ?? '').includes('0.3.0'),
    (storeRows.slotText ?? '(no row)').slice(0, 150),
  )
  ok(
    'and it says which of the two another house could run, from the answers each carries',
    storeRows.slotChips.includes('your devices') &&
      storeRows.pastedChips.includes('any house'),
    `${storeRows.slotChips.join('+')} | ${storeRows.pastedChips.join('+')}`,
  )
  ok(
    'and a saved module reads as installed, in no room, with the room named as where to add it',
    (storeRows.slotText ?? '').includes('installed') &&
      (storeRows.slotText ?? '').includes('In no room yet') &&
      (storeRows.slotText ?? '').includes('Add module to room'),
    (storeRows.slotText ?? '(no row)').slice(0, 200),
  )
  ok(
    "and the store offers no way to place it: that is the room's act, not the shelf's",
    storeRows.slotButtons.every((label) => !/^(Install|Add to)/.test(label)),
    storeRows.slotButtons.join(' | ') || '(no buttons)',
  )

  // -- into a room, from the room ------------------------------------------
  // Which is the only way a module is placed, and the reason it is driven here
  // rather than in the store: the button lives in the room's own dialog, beside
  // the packs that room can take.
  await openRoom(spare.name)
  await click(page, 'open-house-room-settings button', { nth: 'Add module to room', wait: 2500 })
  const kept = await readAll(() => {
    const card = window.__deepAll(
      'open-house-dialog .card[data-module="browser_walk_by_slot"]',
    )[0]
    return {
      text: card ? window.__deepText(card).replace(/\s+/g, ' ').trim() : null,
      button: window.__deepAll('#add-stored-browser_walk_by_slot').length,
    }
  })
  ok(
    `the room's "Add module to room" offers the modules this house kept, and can add one here`,
    kept.text !== null && kept.button === 1,
    (kept.text ?? '(not offered)').slice(0, 200),
  )
  await click(page, '#add-stored-browser_walk_by_slot', { wait: 8000 })
  // Waited for by the card, not slept past: the room's page is reloaded when the
  // module lands, and a read taken during that reload sees the page as it was --
  // a room with no such module in it -- which is exactly the failure this walk
  // reported before it was written this way.
  const installedSlugEarly = `browser_walk_by_slot_${spare.id}`
  await waitFor(
    (slug) =>
      window
        .__deepAll('open-house-hosted-module')
        .some((node) => node.module?.slug === slug),
    installedSlugEarly,
  )
  const here = await readAll((slug) => {
    const card = window
      .__deepAll('open-house-hosted-module')
      .find((node) => node.module?.slug === slug)
    return card
      ? {
          text: window.__deepText(card).replace(/\s+/g, ' ').trim(),
          remove: window.__deepAll(`#unhost-${slug}`).length,
        }
      : null
  }, `browser_walk_by_slot_${spare.id}`)
  ok(
    'and the room it was added to draws it, waiting for the device, with a way to take it back out',
    (here?.text ?? '').includes('Waiting for') && here?.remove === 1,
    here ? `${here.remove} remove button(s): ${here.text.slice(0, 160)}` : 'no card in the room',
  )

  // -- the module, in the room, waiting for the device ---------------------
  // Read off the card the *room* draws, which is where a module's card is drawn:
  // the import screen makes modules and a room holds them, and a listing of the
  // whole house on the import screen was one more place for the same card to
  // drift. The copy this walk placed is found by its slug -- `<definition>_<room>`
  // -- which is the name the room is in.
  const installedSlug = `browser_walk_by_slot_${spare?.id ?? ''}`
  const card = await readAll((slug) => {
    const node = window
      .__deepAll('open-house-hosted-module')
      .find((n) => n.module?.slug === slug)
    const module = node?.module
    return module
      ? {
          room: module.room_id,
          title: module.title,
          slots: module.slots,
          running: module.automation_id !== '',
          text: window.__deepText(node).replace(/\s+/g, ' ').trim(),
        }
      : null
  }, installedSlug)
  ok(
    'the installation names the room it is in, in Home Assistant’s own list and here',
    card?.room === (spare?.id ?? '') && (card?.title ?? '').includes(spare?.name ?? 'x'),
    `${card?.room} | ${card?.title}`,
  )
  ok(
    'and it is a module with no automation until that device exists',
    card?.running === false &&
      (card?.slots ?? []).some(
        (row) => row.name === 'ambient_light_sensor' && row.bound === '',
      ),
    JSON.stringify(card?.slots ?? null),
  )
  ok(
    'and the card for it says what it is waiting for, rather than reading as idle',
    (card?.text ?? '').includes('Waiting for'),
    (card?.text ?? '(no card)').slice(0, 200),
  )

  // -- the setting the blueprint kept, changed from the screen -------------
  // The settings are a `details` among the module's own; the section says which
  // inputs it holds by labelling each, and the label is what a person reads.
  // Which module is driven is read out of the screen's own list rather than
  // assumed: two of the modules here keep the same input, and a rebuild moves
  // one to the end of the list, so the settings section that belongs to the
  // module the walk imported has to be named and not counted.
  const mine = await readAll((slug) => {
    const module = window
      .__deepAll('open-house-hosted-module')
      .find((node) => node.module?.slug === slug)?.module
    return module
      ? { slug: module.slug, settings: module.settings.map((s) => s.title || s.name) }
      : null
  }, installedSlug)
  ok(
    'the module the walk imported kept the input it ticked, and its card says so',
    (mine?.settings ?? []).includes('Maximum Brightness (%)'),
    `${mine?.slug}: ${(mine?.settings ?? []).join(' | ')}`,
  )
  // A bare selector, with no ancestor part: a card's markup lives in that card's
  // own shadow root now, and the walk's deep reader evaluates a whole selector
  // against one root at a time -- so `open-house-host-module details.nested`
  // matches nothing, because no single root holds both halves of it. The two
  // attributes are the card's own and unique in the document -- and `data-kind`
  // is what tells the settings section from the configuration bar above it,
  // which is a `details.nested` carrying the same slug.
  const section = `details.nested[data-kind="settings"][data-module="${mine?.slug}"]`
  const held = await readAll((sel) => {
    const details = window.__deepAll(sel)[0]
    if (!details) return null
    return window.__deepAll('label.label', details).map((l) =>
      window.__deepText(l).replace(/\s+/g, ' ').trim(),
    )
  }, section)
  ok(
    'and its settings section names the input it kept',
    Array.isArray(held) && held.includes('Maximum Brightness (%)'),
    held === null ? 'no settings section at all' : held.join(' | '),
  )
  // The input answered with a slot is one of those settings now -- every input
  // is kept by default -- and it is the case that must not be editable: its
  // value is whatever the room binds, so typing one there would replace the
  // promise with a hard-coded device and say nothing about it.
  // Scoped to this module's own section: another module in the house has a
  // `lux_sensor` setting that *is* editable, so a document-wide count would
  // find it and pass on a form that belongs to something else.
  const slotSetting = await readAll((sel) => {
    const details = window.__deepAll(sel)[0]
    return {
      note: window.__deepText(details ?? document.body).replace(/\s+/g, ' ').trim(),
      editable: details
        ? window.__deepAll('ha-form[data-setting="lux_sensor"]', details).length
        : -1,
    }
  }, section)
  ok(
    "the setting the room fills is shown as the room's, with nothing to type into",
    slotSetting.editable === 0 &&
      slotSetting.note.includes("moves when the room's binding moves"),
    `editable forms: ${slotSetting.editable} | ${slotSetting.note.slice(0, 140)}`,
  )
  const settingsForm = `ha-form[data-module="${mine?.slug}"][data-setting="max_brightness_percent"]`
  const typed = await typeField(page, { form: settingsForm, label: null }, '60')
  ok('a new value can be typed into it', typed.reaches, `into ${typed.hit}`)
  // **Nothing is pressed to save it.** The card saves what it holds once the
  // typing stops, so this waits for the sentence a save leaves rather than
  // clicking a button that is deliberately not there any more -- and a Save
  // button coming back would fail this by timing out, which is the check.
  await waitFor((slug) => {
    const card = window
      .__deepAll('open-house-hosted-module')
      .find((node) => node.module?.slug === slug)
    return !!window.__deepAll('.banner.info', card ?? document.body)[0]
  }, mine?.slug)
  await sleep(1500)
  // Read off the card itself, and by card: the banner is found inside the card
  // rather than across the document, because a document-wide search for an info
  // banner would find the import screen's own "Saved as ..." notice from the
  // earlier step instead. Scoped by element and not by `card.shadowRoot`: every
  // screen here renders into the light DOM -- `createRenderRoot` returns the
  // element itself, so Home Assistant's own theme reaches the markup -- and a
  // card with no shadow root answers `null` to that, however it is worded.
  const saved = await readAll((slug) => {
    const card = window
      .__deepAll('open-house-hosted-module')
      .find((node) => node.module?.slug === slug)
    const banner = window.__deepAll('.banner.info', card ?? document.body)[0]
    return banner ? window.__deepText(banner).replace(/\s+/g, ' ').trim() : ''
  }, mine?.slug)
  ok('saving answers with what happened', saved.includes('is set up that way now'), saved.trim().slice(0, 130))
  // A module whose device is not there yet is saved and still not running, and
  // the answer has to say which of the two it is -- "Home Assistant is running
  // it" about a module with no automation is exactly the sort of thing a person
  // would go looking for in Home Assistant's automations and not find.
  ok(
    'and what it says is about this module, which is still waiting',
    saved.includes('waiting for') && saved.includes('not created until'),
    saved.trim().slice(0, 200),
  )
  const now = await readAll((sel) => {
    const form = window.__deepAll(sel)[0]
    const name = form?.schema?.[0]?.name
    return { name, value: name ? form.data[name] : null }
  }, settingsForm)
  ok(
    'and the module now holds the value that was typed',
    String(now.value) === '60',
    `${now.name} = ${JSON.stringify(now.value)}`,
  )
  // -- the cast, on the card, which is the other place one is offered --------
  // The import screen has its own cast walk above; a module is *lived with* on
  // this one, and the row a person changes their mind on is here. Both places
  // offer the same menu and both have to draw the same editor: a row that says
  // "The condition" with nothing under it is what "there is no UI for this"
  // looks like from the outside, and it is what was reported from this card.
  await readAll((sel) => {
    const form = window.__deepAll(sel)[0]
    if (!form) return
    form.dispatchEvent(
      new CustomEvent('value-changed', {
        detail: { value: { ...form.data, cast_mode: 'condition' } },
        bubbles: true,
        composed: true,
      }),
    )
  }, settingsForm)
  await sleep(1200)
  const cardEditor = await readAll((sel) => {
    const form = window.__deepAll(sel)[0]
    const box = form ? window.__deepAll('ha-automation-condition', form)[0] : null
    const button = box ? window.__deepAll('ha-button', box)[0] : null
    return {
      schema: form ? form.schema.map((row) => row.name) : null,
      data: form ? Object.keys(form.data) : null,
      editor: !!box,
      button: (button?.textContent ?? '').trim(),
      height: box ? Math.round(box.getBoundingClientRect().height) : 0,
    }
  }, settingsForm)
  ok(
    "a card's rows are castable too, and the condition editor is the one Home Assistant draws",
    cardEditor.editor && cardEditor.button === 'Add condition',
    JSON.stringify(cardEditor),
  )
  // **And the flow editor, on the card, is a flow editor here too.** The card
  // drew the row's own selector beside a flow until this was reported: what a
  // flow *watches* is the entity the row resolves to, which made the field read
  // as the wire rather than as a second answer -- but a person looking at a row
  // that says it is answered by a flow and still asks them to pick an entity has
  // been given two answers to one question and no way to tell which one counts.
  // The field goes, as it does under every other cast, and the block beside it
  // says what the flow writes and where to build it. Choosing it saves it like
  // everything else on this card, so the flow **is** pushed -- the row knows the
  // id Node-RED gave it, and the editor opens on that flow rather than on the
  // landing page. The row is put back below, which forgets the flow again.
  await readAll((sel) => {
    const form = window.__deepAll(sel)[0]
    form?.dispatchEvent(
      new CustomEvent('value-changed', {
        detail: { value: { ...form.data, cast_mode: 'nodered' } },
        bubbles: true,
        composed: true,
      }),
    )
  }, settingsForm)
  await sleep(1200)
  const cardFlow = await readAll((sel) => {
    const form = window.__deepAll(sel)[0]
    const scope = form ? form.parentElement : null
    const block = scope ? window.__deepAll('[data-flow]', scope)[0] : null
    const embed = scope ? window.__deepAll('open-house-node-red', scope)[0] : null
    const frame = embed ? window.__deepAll('iframe', embed)[0] : null
    return {
      schema: form ? form.schema.map((row) => row.name) : null,
      text: block ? window.__deepText(block).replace(/\s+/g, ' ') : null,
      src: frame ? frame.getAttribute('src') : null,
    }
  }, settingsForm)
  ok(
    "a card's nodered row drops its own selector, as every other cast does",
    cardFlow.schema !== null &&
      !cardFlow.schema.includes('max_brightness_percent') &&
      cardFlow.schema.includes('cast_mode'),
    JSON.stringify(cardFlow.schema),
  )
  // The card spells the entity out with the module's own slug, where the import
  // screen leaves the name as a hole -- the card *has* the module, and the import
  // screen would have to guess the slug the server derives from the title.
  ok(
    'and says what the flow will write, that the device is wired in, and that it runs',
    cardFlow.text !== null &&
      new RegExp(
        `sensor\\.open_house_flow_${installedSlug}_\\s*max_brightness_percent`,
      ).test(cardFlow.text) &&
      /wired into its input node/.test(cardFlow.text) &&
      /runs on its own/.test(cardFlow.text) &&
      !/created when you save/.test(cardFlow.text),
    JSON.stringify(cardFlow),
  )
  // **And the editor, here too.** The choice saved, so the flow exists and
  // Node-RED has given it an id: the frame opens on that flow rather than on the
  // landing page, which is the whole of what the choice bought. Still the editor
  // rather than a link to it.
  ok(
    "and embeds the editor on the flow the choice just made",
    cardFlow.src !== null &&
      cardFlow.src.startsWith(`${NODE_RED_EDITOR}/`) &&
      cardFlow.src.includes('#flow/'),
    JSON.stringify(cardFlow?.src),
  )
  await readAll((sel) => {
    const form = window.__deepAll(sel)[0]
    form?.dispatchEvent(
      new CustomEvent('value-changed', {
        detail: { value: { ...form.data, cast_mode: 'none' } },
        bubbles: true,
        composed: true,
      }),
    )
  }, settingsForm)
  // Putting the row back saved like everything else on this card, and the flow
  // goes with it rather than being left in Node-RED for a row that no longer
  // answers with it. Waited for off the module itself, and not slept past: the
  // room's page is drawn again when that save lands, and the click below is on a
  // button that a reload halfway through drawing has already taken away -- which
  // is a click on nothing, and a card that never goes.
  await waitFor(
    (slug) => {
      const card = window
        .__deepAll('open-house-hosted-module')
        .find((node) => node.module?.slug === slug)
      const row = card?.module?.settings?.find(
        (setting) => setting.name === 'max_brightness_percent',
      )
      return !!row && !row.flow_id
    },
    installedSlug,
  )
  // -- out of the room again, and still in the store ------------------------
  // The other half of the two acts, and the half that says they are two: taking
  // the copy out of the room leaves the module itself where it was, still
  // offered, still downloadable. The button is the room's, because the room is
  // what holds this copy.
  // The room's page is where this is done, and the walk is already looking at it
  // -- the module was placed and its settings changed here -- so the page is not
  // opened again, only waited for: the card that carries the button is the proof
  // that the page in front of the walk is the room's, after the save's reload.
  await waitFor(
    (slug) =>
      window
        .__deepAll('open-house-hosted-module')
        .some((node) => node.module?.slug === slug),
    installedSlug,
  )
  await click(page, `#unhost-${installedSlug}`, { wait: 8000 })
  // Waited out for the same reason the add was waited for: the room's page is
  // read again when the card says the module changed, and a read taken inside
  // that reload still has the card on it -- a room that looks like it still has
  // the module it was just told to let go of.
  await waitFor(
    (slug) =>
      !window
        .__deepAll('open-house-hosted-module')
        .some((node) => node.module?.slug === slug),
    installedSlug,
  )
  const gone = await readAll((slug) => ({
    cards: window
      .__deepAll('open-house-hosted-module')
      .filter((node) => node.module?.slug === slug).length,
    error: window
      .__deepText(
        window.__deepAll('open-house-room-settings .banner.error')[0] ?? document.body,
      )
      .trim(),
  }), installedSlug)
  ok(
    'removing it from the room takes its card out of that room',
    gone.cards === 0 && gone.error === '',
    `${gone.cards} card(s) left | error: ${gone.error}`,
  )
  await click(page, '#tab-store', { wait: 2500 })
  const after = await readAll(() => {
    const screen = window.__deepAll('open-house-tab-store')[0]
    const row = window
      .__deepAll('.nested', screen)
      .find((r) => window.__deepText(r).includes('browser_walk_by_slot'))
    return row ? window.__deepText(row).replace(/\s+/g, ' ').trim() : null
  })
  ok(
    'and the module is still saved, in no room, ready to be added again',
    (after ?? '').includes('In no room yet') && (after ?? '').includes('installed'),
    (after ?? '(no row)').slice(0, 200),
  )
  // -- an input nobody answers, kept as the module's own option ------------
  // The other half of keeping an input: one the person does not answer at import
  // at all. "Blueprint default" leaves the row with no value, and where the
  // blueprint gives that input no default there is nothing to build the
  // automation from -- so the module is saved, installs wherever it is put, and
  // waits. What must *not* happen is Save going grey: what the person is making
  // is a module with a job still to do, and a screen that refuses to make it is
  // the screen this walk was written after.
  await click(page, '#tab-dev', { wait: 1200 })
  await click(page, '.tabs .tab', { nth: 'Import as a module', wait: 1500 })
  await click(page, '.tab', { nth: 'Paste YAML', wait: 800, within: SCREEN })
  await click(page, 'ha-code-editor', { wait: 600, within: SCREEN })
  await page.keyboard.type(ASKED, { delay: 8 })
  await sleep(700)
  await click(page, 'button', { nth: 'Read this source', wait: 2500 })
  await waitFor((sel) => window.__deepAll(sel).length > 0, FORM)
  await sleep(900)
  const asked = await readAll((sel) => {
    const screen = window.__deepAll(sel)[0]
    const button = window
      .__deepAll(`${sel} button`)
      .find((b) => (b.textContent ?? '').includes('Save this module'))
    return {
      how: screen.decisions.map((d) => d.how),
      unfilled: screen.unfilled,
      disabled: button?.disabled ?? null,
      said: window
        .__deepAll(`${sel} .banner.info`)
        .map((b) => window.__deepText(b).replace(/\s+/g, ' ').trim())
        .join(' | ')
        .replace(/\s+/g, ' '),
    }
  }, SCREEN)
  ok(
    'an input a person does not answer is left to the module by default',
    asked.how.length === 1 && asked.how[0] === 'default',
    asked.how.join(', '),
  )
  ok(
    'and an input with no default is not a reason to block saving: it is an option still to set',
    asked.disabled === false &&
      asked.unfilled.includes('Who To Tell') &&
      asked.said.includes('does not run until'),
    `disabled=${asked.disabled} | unfilled: ${asked.unfilled.join(', ')} | ${asked.said.slice(0, 140)}`,
  )
  await type(page, '#host-title', 'Browser Walk Asked')
  await tickReplace()
  await sleep(300)
  await click(page, 'button', { nth: 'Save this module', wait: 8000 })
  await sleep(2500)
  const askedSaved = await readAll(
    (sel) =>
      window.__deepText(
        window.__deepAll(`${sel} .banner.info`)[0] ?? document.body,
      ).trim(),
    SCREEN,
  )
  ok(
    'so it saves, with the input still unanswered',
    askedSaved.includes('Saved as browser_walk_asked'),
    askedSaved.replace(/\s+/g, ' ').slice(0, 160),
  )

  // -- and the room it is put in is asked for that input -------------------
  await openRoom(spare.name)
  await click(page, 'open-house-room-settings button', { nth: 'Add module to room', wait: 2500 })
  await click(page, '#add-stored-browser_walk_asked', { wait: 8000 })
  const askedSlug = `browser_walk_asked_${spare.id}`
  await waitFor(
    (slug) =>
      window
        .__deepAll('open-house-hosted-module')
        .some((node) => node.module?.slug === slug),
    askedSlug,
  )
  const askedHere = await readAll((slug) => {
    const card = window
      .__deepAll('open-house-hosted-module')
      .find((node) => node.module?.slug === slug)
    return card
      ? {
          text: window.__deepText(card).replace(/\s+/g, ' ').trim(),
          automation: card.module?.automation_id ?? '',
        }
      : null
  }, askedSlug)
  ok(
    'the room draws it waiting for its own option, with nothing running yet',
    (askedHere?.automation ?? 'x') === '' &&
      (askedHere?.text ?? '').includes('no value yet') &&
      (askedHere?.text ?? '').includes('Settings'),
    (askedHere?.text ?? '(no card in the room)').slice(0, 220),
  )
  // Answered where the module is, which is the whole point of keeping it: the
  // room's copy is the room's, and the control is the input's own -- a text box
  // for a text input, an entity picker for one that names devices.
  const askedSection = `details.nested[data-kind="settings"][data-module="${askedSlug}"]`
  const typedAsked = await readAll((sel) => {
    const details = window.__deepAll(sel)[0]
    const form = window.__deepAll('ha-form[data-setting="who"]', details ?? document.body)[0]
    if (!form) return false
    form.dispatchEvent(
      new CustomEvent('value-changed', {
        detail: { value: { who: 'the walk' } },
        bubbles: true,
        composed: true,
      }),
    )
    return true
  }, askedSection)
  ok(
    "its one option is a field in the module's own settings",
    typedAsked,
    `form found: ${typedAsked}`,
  )
  // And nothing is pressed here either: the field's own change is what saves,
  // and the wait below is for the module Home Assistant is running -- which
  // arrives only if the auto-save really went out.
  // Waited out, not slept past: the room's page is read again when the card says
  // the module changed, and the module is built inside that read.
  // What is waited for is the **automation**, not a "Waiting for" sentence: this
  // module has no slot to wait on, so its card never carried that sentence and
  // waiting for it to go would return before the save had left. A card reading
  // `not running` with nothing under it is a read taken too early -- the option
  // answered is a module Home Assistant has built.
  await waitFor(
    (slug) => {
      const card = window
        .__deepAll('open-house-hosted-module')
        .find((node) => node.module?.slug === slug)
      return !!card && (card.module?.automation_id ?? '') !== ''
    },
    askedSlug,
  )
  const askedRunning = await readAll((slug) => {
    const card = window
      .__deepAll('open-house-hosted-module')
      .find((node) => node.module?.slug === slug)
    return {
      text: window.__deepText(card ?? document.body).replace(/\s+/g, ' ').trim(),
      automation: card?.module?.automation_id ?? '',
      saved: window.__deepText(
        window.__deepAll('.banner.info', card ?? document.body)[0] ?? document.body,
      )
        .replace(/\s+/g, ' ')
        .trim(),
    }
  }, askedSlug)
  ok(
    'and setting it builds the module and runs it',
    (askedRunning.automation ?? '') !== '' &&
      !askedRunning.text.includes('Waiting for') &&
      askedRunning.saved.includes('Home Assistant is running it'),
    `${askedRunning.automation} | ${askedRunning.saved.slice(0, 140)}`,
  )

  // -- and the card in the room opens the module itself --------------------
  // The control is on the room's card, because a room is where somebody
  // notices the module wants changing. What it opens is the **module**, not
  // this room's copy of it: the screen a fresh import uses, with the module's
  // own answers already in it, and a sentence saying which rooms a save is
  // about to rebuild. So the walk checks both halves of that -- that it is the
  // module's screen and not this room's settings, and that a save really does
  // reach the room it was opened from.
  await click(page, `#edit-${askedSlug}`, { wait: 2500 })
  await waitFor(() => {
    const screen = window.__deepAll('open-house-host-module').find((n) => n.editing)
    return (
      !!screen &&
      window
        .__deepAll('h2', screen)
        .some((h) => (h.textContent ?? '').trim() === '1. What this module is')
    )
  })
  const editView = await readAll((slug) => {
    const screen = window.__deepAll('open-house-host-module').find((n) => n.editing === slug)
    if (!screen) return null
    const said = (sel) =>
      window.__deepAll(sel, screen).map((n) => window.__deepText(n).replace(/\s+/g, ' ').trim())
    return {
      inDialog: !!screen.closest('open-house-dialog'),
      headings: said('h2'),
      // The import screen's own furniture, which an edit must not offer: the
      // three source tabs, the source fields, and the replace box. Each one of
      // them would let an answer be moved onto a document that does not
      // declare it.
      tabs: window.__deepAll('button.tab', screen).length,
      source: window.__deepAll('#host-blueprint', screen).length,
      replace: window.__deepAll('#host-replace', screen).length,
      title: window.__deepAll('#host-title', screen)[0]?.value ?? null,
      rows: window.__deepAll('ha-form', screen).length,
      banners: said('.banner.info'),
    }
  }, askedSlug)
  ok(
    'the room card opens the module on the screen an import uses',
    editView?.inDialog === true &&
      editView.headings.includes('1. What this module is') &&
      editView.headings.includes('4. Save the changes'),
    `in a dialog: ${editView?.inDialog} | ${(editView?.headings ?? []).join(' / ') || '(no screen)'}`,
  )
  ok(
    'and it is the module being edited, not this room: no source, no replace box',
    (editView?.tabs ?? 1) === 0 &&
      (editView?.source ?? 1) === 0 &&
      (editView?.replace ?? 1) === 0,
    `tabs=${editView?.tabs} source=${editView?.source} replace=${editView?.replace}`,
  )
  ok(
    'and it arrives filled with what the module already is',
    (editView?.title ?? '') === 'Browser Walk Asked' && (editView?.rows ?? 0) >= 1,
    `title="${editView?.title}" | ${editView?.rows} input rows on it`,
  )
  ok(
    'and it says which rooms saving will rebuild',
    (editView?.banners ?? []).some(
      (b) => b.includes('Saving rebuilds 1 installation') && b.includes(spare.name),
    ),
    (editView?.banners ?? []).join(' ; ').slice(0, 200) || '(no banner)',
  )
  // Saving is the whole of the request: the room it was opened from has to be
  // running the module *afterwards*, from the same automation -- rebuilt, not
  // replaced -- and still holding the answer this room gave it, because that
  // answer is the room's and an edit of the module is not entitled to it.
  await type(page, '#host-title', 'Browser Walk Asked Renamed')
  await click(page, 'button', { nth: 'Save the changes', wait: 8000 })
  await waitFor((slug) => {
    const card = window
      .__deepAll('open-house-hosted-module')
      .find((node) => node.module?.slug === slug)
    return !!card && window.__deepText(card).includes('Browser Walk Asked Renamed')
  }, askedSlug)
  const editAfter = await readAll((slug) => {
    const card = window
      .__deepAll('open-house-hosted-module')
      .find((node) => node.module?.slug === slug)
    return {
      text: window.__deepText(card ?? document.body).replace(/\s+/g, ' ').trim(),
      automation: card?.module?.automation_id ?? '',
      dialogs: window.__deepAll('open-house-dialog').length,
      // The edit screen is what has to be gone, not every dialog: this page
      // keeps its own "Add module to room" sheet in the document all along,
      // closed, so a count of dialogs is a count of the page.
      editing: window.__deepAll('open-house-host-module').filter((n) => n.editing).length,
    }
  }, askedSlug)
  ok(
    'and saving renames the module the room is showing',
    (editAfter.text ?? '').includes('Browser Walk Asked Renamed') &&
      editAfter.editing === 0,
    `${(editAfter.text ?? '(no card)').slice(0, 140)} | edit screens left: ${editAfter.editing}` +
      ` | dialogs: ${editAfter.dialogs}`,
  )
  ok(
    'and the room is left running it from the same automation, on its own answer',
    (editAfter.automation ?? '') !== '' &&
      editAfter.automation === askedRunning.automation &&
      !editAfter.text.includes('Waiting for'),
    `${editAfter.automation} vs ${askedRunning.automation}` +
      ` | ${editAfter.text.includes('Waiting for') ? 'went back to waiting' : 'still running'}`,
  )

  // The house is left as it was found, so a second run walks the same house the
  // first one did. The module stays in the store -- what is taken back is only
  // this room's copy of it, and by the same door the by-slot section uses.
  await click(page, `#unhost-${askedSlug}`, { wait: 8000 })
  await waitFor(
    (slug) =>
      !window
        .__deepAll('open-house-hosted-module')
        .some((node) => node.module?.slug === slug),
    askedSlug,
  )
  await click(page, '#tab-store', { wait: 2500 })
  const askedAfter = await readAll(() => {
    const screen = window.__deepAll('open-house-tab-store')[0]
    const row = window
      .__deepAll('.nested', screen)
      .find((r) => window.__deepText(r).includes('browser_walk_asked'))
    return row ? window.__deepText(row).replace(/\s+/g, ' ').trim() : null
  })
  ok(
    'and the walk leaves it saved and in no room, as it found the house',
    (askedAfter ?? '').includes('In no room yet') &&
      (askedAfter ?? '').includes('installed'),
    (askedAfter ?? '(no row)').slice(0, 200),
  )

} catch (error) {
  ok('the walk completed', false, String(error).slice(0, 300))
  // Where it stopped, for a wait that timed out: the page it was on, the cards
  // that page is drawing, and whatever the screens are saying in red. A 90-second
  // waitFor says nothing about which of the hundred waits it was.
  try {
    const state = await page.evaluate(() => ({
      url: location.href,
      cards: window
        .__deepAll('open-house-hosted-module')
        .map((node) => node.module?.slug),
      banners: window
        .__deepAll('.banner')
        .map((node) => window.__deepText(node).replace(/\s+/g, ' ').trim()),
    }))
    console.log('   state:', JSON.stringify(state).slice(0, 600))
  } catch {
    // The page may be gone; the error above is the one worth reading.
  }
} finally {
  for (const event of events.slice(-10)) console.log('   event:', event)
  await browser.close()
}
