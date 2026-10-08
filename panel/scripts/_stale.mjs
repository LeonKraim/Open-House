// Scratch: a page the house profile moved out from under, and the write that
// must not land on it.
//
// The reported bug this exists to prove fixed: a hosted-module card saves the
// values it holds on a timer once the typing stops, and a page switched out
// from under it -- a house profile taken in another tab -- left that timer
// running. The pre-switch answers then landed over the profile just restored,
// and the person who switched watched their new profile come up already
// carrying the old one's settings.
//
// Two things have to be true, and they are two different things: the *server*
// must refuse the write (the revision the page was rendered from is no longer
// the house's), and the *page* must stop pretending to be live. The refusal
// alone would leave a form that silently does nothing, which reads as the
// panel being broken; the notice alone would be a client-side promise the
// server does not keep. So this walk checks both, on two pages: the one a card
// saves itself on, and a room page whose devices are written the same way.
//
// Everything is done through the panel's own websocket connection where it is
// not the thing under test -- hosting the module, taking the profile -- because
// the click paths to those are a dozen clicks the other walks already make, and
// what is in question here is the notice and the refusal.
//
// Run: node scripts/_stale.mjs
import { mkdirSync } from 'node:fs'
import { openPanel, click, typeField, sleep } from './_ui.mjs'

const SHOTS = `${process.env.TEMP ?? '/tmp'}/open-house-shots`
const BLUEPRINT = 'MarqBarq/dynamic-lighting.yaml'
const LUX = 'sensor.open_house_mock_fleet_minimal_living_room_lux'
const LIGHT = 'light.open_house_mock_fleet_minimal_living_room'
const TITLE = 'Stale walk'
const PROFILE = 'Stale walk profile'
//: The room page's half of the walk, and the slot on it the walk writes.
//: Kitchen binds its own light group, so the row is there to be cleared -- and
//: the write is refused, so clearing it is not a thing that happens.
const ROOM = 'Kitchen'
const ROOM_LIGHT = 'light.open_house_mock_fleet_demo_kitchen'
//: The sentence the user asked for, verbatim. It is the heading and not a
//: paraphrase, so a page that says something *like* it fails this walk.
const NOTICE =
  'This page has been disabled because you switched your house profile. Please reload.'

const FAILURES = []
const check = (label, ok, detail = '') => {
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${label}${detail ? ` -- ${detail}` : ''}`)
  if (!ok) FAILURES.push(label)
}

mkdirSync(SHOTS, { recursive: true })

/** The driver for one browser tab: reads, waits and the panel's own commands. */
const drive = (page) => {
  const read = (fn, arg = null) => page.evaluate(fn, arg)
  const waitFor = (fn, arg = null, ms = 90000) =>
    page.waitForFunction(fn, arg, { timeout: ms, polling: 500 })
  /** One command over the panel's own connection -- the same wire it commands on. */
  const command = (message) =>
    read(async (msg) => {
      const panel = window.__deepAll('open-house-panel')[0]
      try {
        return { ok: true, reply: await panel.hass.callWS(msg) }
      } catch (error) {
        return { ok: false, reply: String(error?.message ?? error) }
      }
    }, message)
  return { page, read, waitFor, command }
}

/** The module record the server holds, by slug. */
const hosted = async (tab, slug) => {
  const reply = await tab.command({ type: 'open_house/modules/hosted' })
  const mine = (reply.reply?.modules ?? []).find((module) => module.slug === slug)
  const setting = (mine?.settings ?? []).find((row) => row.name === 'max_brightness_percent')
  return { room: mine?.room_id ?? null, value: setting?.value ?? null }
}

/** Open a room's page from the room list, the way a person reaches it. */
const openRoom = async (tab, name) => {
  await click(tab.page, '#tab-rooms', { wait: 1500 })
  await tab.waitFor((wanted) => {
    const row = window
      .__deepAll('open-house-tab-rooms tbody tr')
      .find((r) => window.__deepText(r).trim().startsWith(wanted))
    return !!row?.querySelector('a')
  }, name)
  await tab.read((wanted) => {
    const row = window
      .__deepAll('open-house-tab-rooms tbody tr')
      .find((r) => window.__deepText(r).trim().startsWith(wanted))
    row.querySelector('a').click()
  }, name)
  await tab.waitFor(() => {
    const settings = window.__deepAll('open-house-room-settings')[0]
    return !!settings && !window.__deepText(settings).includes('Reading the room')
  })
  await sleep(800)
}

/** What the disabled-page notice is showing, off the page it is drawn over. */
const notice = (tab) =>
  tab.read(() => {
    const sheet = window.__deepAll('.stale-sheet')[0]
    const button = window.__deepAll('.stale-sheet button')[0]
    return {
      heading: (window.__deepAll('.stale-sheet h2')[0]?.textContent ?? '').replace(/\s+/g, ' ').trim(),
      reason: (window.__deepAll('.stale-sheet p')[0]?.textContent ?? '').replace(/\s+/g, ' ').trim(),
      button: (button?.textContent ?? '').replace(/\s+/g, ' ').trim(),
      // The page body is inert behind the notice, so nothing under it can be
      // clicked or tabbed to -- a control that would silently refuse is worse
      // than no control at all.
      inert: window.__deepAll('div[inert]').length,
      sheet: !!sheet,
    }
  })

// -- two tabs: the page under test, and the one that switches the house -----
const one = await openPanel()
const two = await openPanel()
const TAB = drive(one.page)
const SWITCHER = drive(two.page)

try {
  // -- the card, and the page it is on ------------------------------------
  const listed = await TAB.command({ type: 'open_house/rooms/list' })
  const rooms = listed.reply?.rooms ?? []
  const idFor = (name) => rooms.find((room) => room.name === name)?.id
  check(`the walk house has a ${ROOM} to open`, !!idFor(ROOM), JSON.stringify(rooms.map((r) => r.name)))
  await TAB.command({ type: 'open_house/modules/unhost', module: 'stale_walk' })
  const made = await TAB.command({
    type: 'open_house/modules/host',
    kind: 'blueprint',
    key: BLUEPRINT,
    title: TITLE,
    bindings: {
      lux_sensor: { kind: 'entity', value: LUX },
      weather_entity: { kind: 'literal', value: 'weather.nowhere' },
      lights: { kind: 'entity', value: LIGHT },
      max_brightness_percent: { kind: 'literal', value: 100 },
    },
    outputs: [],
    settings: ['max_brightness_percent'],
    flows: [],
  })
  check('a module is hosted to hold a card', made.ok, JSON.stringify(made.reply).slice(0, 160))
  const slug = made.reply?.module ?? 'stale_walk'
  await sleep(2500)

  // A module goes where its devices are, and the page that holds its card is
  // the page this walk has to disable -- so the walk asks the server where the
  // module landed rather than assuming the House tab.
  const placed = await hosted(TAB, slug)
  check('and it landed somewhere with a page', placed.room !== null, JSON.stringify(placed))
  if (placed.room === '') {
    await click(one.page, '#tab-house', { wait: 2500 })
  } else {
    const name = rooms.find((room) => room.id === placed.room)?.name
    await openRoom(TAB, name)
  }
  await TAB.waitFor(
    (title) =>
      window
        .__deepAll('open-house-hosted-module')
        .some((node) => window.__deepText(node).includes(title)),
    TITLE,
  )
  const cardAt = placed.room === '' ? 'The house page' : `The ${placed.room} page`
  console.log(`${cardAt} is holding the card`)

  const card = (tab) =>
    tab.read((title) => {
      const node = window
        .__deepAll('open-house-hosted-module')
        .find((card) => window.__deepText(card).includes(title))
      return node
        ? { revision: node.revision, stale: node.stale, error: node.error ? String(node.error.message ?? node.error) : null }
        : null
    }, TITLE)

  const form = `ha-form[data-module="${slug}"][data-setting="max_brightness_percent"]`

  // -- a save that lands, so the refusal below is about the switch ----------
  await typeField(one.page, { form, label: null }, '60')
  await sleep(3000)
  const saved = await hosted(TAB, slug)
  check(
    'before the switch, a value typed on the card is saved',
    Number(saved.value) === 60,
    JSON.stringify(saved),
  )
  const before = await card(TAB)
  check('and the card is live, holding a revision', before?.stale === false, JSON.stringify(before))

  // -- the second tab takes a house profile: the house moves ----------------
  await openRoom(SWITCHER, ROOM)
  const kitchenRevision = await SWITCHER.read(
    () => window.__deepAll('open-house-room-settings')[0]?.revision,
  )
  const capture = await SWITCHER.command({
    type: 'open_house/profiles/capture',
    name: PROFILE,
  })
  const taken = (capture.reply?.profiles ?? []).find((profile) => profile.kind === 'house' && profile.active)
  check(
    'the second tab takes a house profile from the house, and the house goes on it',
    capture.ok && !!taken,
    JSON.stringify(capture.reply).slice(0, 200),
  )
  const after = await SWITCHER.command({ type: 'open_house/profiles/list' })
  const revision = after.reply?.revision
  check(
    'which moves the house under both pages',
    typeof revision === 'number' && revision > kitchenRevision,
    `revision ${kitchenRevision} -> ${revision}`,
  )

  // -- the card saves itself again, and the write must be refused -----------
  await typeField(one.page, { form, label: null }, '70')
  await sleep(3500)
  const shown = await notice(TAB)
  check('the page a card was editing disables itself', shown.sheet, JSON.stringify(shown))
  check(
    'with the sentence that was asked for, word for word',
    shown.heading === NOTICE,
    JSON.stringify(shown.heading),
  )
  check('a way back, and it is a reload', shown.button === 'Reload', shown.button)
  check('and nothing behind it can be reached', shown.inert > 0, `${shown.inert} inert element(s)`)
  const staled = await card(TAB)
  check(
    'the card itself knows, rather than showing the refusal as a fault',
    staled?.stale === true && staled?.error === null,
    JSON.stringify(staled),
  )
  // The point of the whole mechanism: the answers typed before the switch did
  // not land over the profile that was just restored.
  const kept = await hosted(TAB, slug)
  check(
    'and the answer typed on the stale page landed nowhere',
    Number(kept.value) === Number(saved.value),
    `${JSON.stringify(saved.value)} -> ${JSON.stringify(kept.value)}`,
  )
  await one.page.screenshot({ path: `${SHOTS}/stale-page-module.png` })

  // -- the same on a room page, whose devices write the same way ------------
  // The switcher tab is still on the kitchen page it read *before* the move,
  // which is the state the bug is about: a page holding a profile that is gone.
  const roomRevision = await SWITCHER.read(
    () => window.__deepAll('open-house-room-settings')[0]?.revision,
  )
  check(
    'a room page read before the switch is holding the revision it read too',
    roomRevision === kitchenRevision && roomRevision < revision,
    `${roomRevision} vs ${revision}`,
  )
  const at = await SWITCHER.read((entity) => {
    const page = window.__deepAll('open-house-room-settings')[0]
    // Every row the device appears in, and only then the button. The room's own
    // row is not the first to name it: the page draws a Whole house table above
    // the room's devices, and the house's row for the same slot says "Its own:
    // light.kitchen" -- so a search that stopped at the first row naming the
    // device found a row whose only control is "Bind for the house", and a walk
    // that read that as the slot not being bound would have reported the room
    // page as offering nothing to clear.
    const button = window
      .__deepAll('tr', page)
      .filter((row) => window.__deepText(row).includes(entity))
      .flatMap((row) => window.__deepAll('button', row))
      .find((b) => window.__deepText(b).trim() === 'Unbind')
    if (!button) return null
    button.scrollIntoView({ block: 'center' })
    const box = button.getBoundingClientRect()
    return { x: box.x + box.width / 2, y: box.y + box.height / 2 }
  }, ROOM_LIGHT)
  check('the room page offers to clear the device it bound', !!at, JSON.stringify(at))
  if (at) {
    await two.page.mouse.click(at.x, at.y)
    await sleep(3000)
  }
  const room = await notice(SWITCHER)
  check('so the room page disables itself too', room.sheet, JSON.stringify(room))
  check(
    'with the same sentence, and it says which page it was',
    room.heading === NOTICE && room.reason.includes(ROOM),
    JSON.stringify(room),
  )
  const cleared = await SWITCHER.command({
    type: 'open_house/rooms/get',
    room_id: idFor(ROOM),
  })
  const held = (cleared.reply?.bindings ?? []).find((row) => row.slot === 'light_group')
  check(
    'and the slot the write would have cleared is still bound',
    held?.entity_id === ROOM_LIGHT,
    JSON.stringify(held?.entity_id),
  )
  await two.page.screenshot({ path: `${SHOTS}/stale-page-room.png` })

  // -- put the house back ---------------------------------------------------
  // Both tabs are disabled and stay that way; commands do not go through the
  // page's own state, so the house can still be handed back as it was found.
  const off = await SWITCHER.command({ type: 'open_house/profiles/deactivate_house' })
  const removed = await SWITCHER.command({ type: 'open_house/profiles/remove', profile: taken?.name })
  const unhosted = await SWITCHER.command({ type: 'open_house/modules/unhost', module: slug })
  console.log(
    `put back: deactivated ${off.ok}, removed the profile ${removed.ok}, unhosted ${unhosted.ok}`,
  )
  console.log(`the notices: ${SHOTS}/stale-page-module.png, ${SHOTS}/stale-page-room.png`)
} finally {
  console.log(one.events.filter((e) => !e.startsWith('NAV')).join('\n'))
  console.log(two.events.filter((e) => !e.startsWith('NAV')).join('\n'))
  console.log()
  if (FAILURES.length) {
    console.log(`${FAILURES.length} check(s) failed:`)
    for (const line of FAILURES) console.log(`  - ${line}`)
  } else {
    console.log('every check passed')
  }
  await one.browser.close()
  await two.browser.close()
}
process.exit(FAILURES.length ? 1 : 0)
