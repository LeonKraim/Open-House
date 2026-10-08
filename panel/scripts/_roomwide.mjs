// Scratch: a room's picker, widened to the whole house.
//
// The complaint: filling a room's slot offered nothing unless Home Assistant had
// filed a device under that room's *area*, so a room whose area holds no devices
// (the walk house's `testroom`) drew no selector at all for any slot -- while the
// Whole house table's picker for the same slot listed every light in the house.
// The two are the same control drawing the same list, so the room looked broken
// rather than room-shaped.
//
// The server now proposes the whole house for a room's slot too, and only the
// *order* is the room's: its own devices first, then the rest of the house. What
// this walks is that the page shows it -- the selector is drawn where none was,
// it lists the house's lights, a room with a light of its own leads with it, and
// a device from another room can actually be bound and taken back out again.
//
// Run: node scripts/_roomwide.mjs
//
// One thing is driven without a real click and says so: choosing an entity in
// Home Assistant's own selector. Its search box and list are HA's component, and
// driving those is a test of that component rather than of this panel -- the same
// reason `_host.mjs` answers a text input instead. The panel's half is the
// `value-changed` event below, which is exactly what the selector emits when a
// person picks something; everything after it (the write, the row, the status,
// and the clearing) is read back off the server's own answer.
import { openPanel, all, click, sleep } from './_ui.mjs'

const SCREEN = 'open-house-room-settings'
// A light from another room, and the kitchen's own: the mock fleet has eight
// lights and no kitchen in the garage, so "somewhere else" is unambiguous.
const ELSEWHERE_LIGHT = 'light.garage_demo_garage'
const KITCHEN_LIGHT = 'light.open_house_mock_fleet_demo_kitchen'

const { browser, page, events } = await openPanel()
const ok = (label, pass, detail = '') =>
  console.log(`${pass ? 'ok  ' : 'FAIL'} ${label}${detail ? ` -- ${detail}` : ''}`)
const waitFor = (fn, arg = null, ms = 90000) =>
  page.waitForFunction(fn, arg, { timeout: ms, polling: 500 })
const readAll = (fn, arg = null) => page.evaluate(fn, arg)

// Read a room's page: from the room list, by the name in its row, and wait for it
// to have finished reading the room -- "in the DOM" and "rendered the room" are
// two moments and a read in between sees a page with no bindings in it.
const openRoom = async (name) => {
  await click(page, '#tab-rooms', { wait: 1500 })
  // A room stays open *inside* the Rooms tab, so selecting the tab again changes
  // nothing and the list never comes back: its own Back button is the way to it.
  // Without this the lookup below hunts for room rows among the open room's own
  // tables, times out, and calls the second room missing.
  if ((await all(page, `${SCREEN}`)).length > 0) {
    await click(page, `${SCREEN} button`, { nth: 'Back', wait: 2000 })
  }
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
    if (window.__deepText(settings).includes('Reading the room')) return false
    return window
      .__deepAll('open-house-room-settings h2')
      .some((h) => (h.textContent ?? '').trim() === 'Devices')
  })
}

// The rows of the *room's* table, in one slot's own words.
//
// Two tables on this page list the same slots -- the Whole house section above
// the room's own bindings, which is the first one in the DOM -- and both name
// "Light group", so a lookup that takes the first match reads the house's row and
// reports it as the room's. The house rows are the ones whose control says "Bind
// for the house"; that button is what tells the two tables apart.
const buttonIn = (slot, label) =>
  readAll(
    ([wanted, text]) => {
      const rows = window
        .__deepAll('open-house-room-settings tbody tr')
        .filter((row) => !window.__deepText(row).includes('Bind for the house'))
      for (const row of rows) {
        const words = window.__deepText(row).replace(/\s+/g, ' ').trim()
        if (!words.includes(wanted)) continue
        const button = window
          .__deepAll('button', row)
          .find((b) => (b.textContent ?? '').trim() === text)
        if (!button) continue
        button.scrollIntoView({ block: 'center' })
        const box = button.getBoundingClientRect()
        return { x: box.x + box.width / 2, y: box.y + box.height / 2, row: words.slice(0, 140) }
      }
      return null
    },
    [slot, label],
  )

// What the picker is holding, and what it is drawing: the state the page keeps,
// whether Home Assistant's own selector came up, and the sentence under it.
//
// The *shortest* card naming Cancel, because the picker is drawn inside the
// section's own card: `__deepText` on the outer one is the whole page, and an
// assertion against it would pass on words that happen to appear anywhere.
const picker = () =>
  readAll(() => {
    const settings = window.__deepAll('open-house-room-settings')[0]
    const state = settings?.picker ?? null
    const cards = window
      .__deepAll('open-house-room-settings .card')
      .filter((c) => window.__deepText(c).includes('Cancel'))
      .map((c) => ({ card: c, words: window.__deepText(c).replace(/\s+/g, ' ').trim() }))
      .sort((a, b) => a.words.length - b.words.length)
    const card = cards[0]?.card
    return {
      state,
      selector: card ? window.__deepAll('ha-selector', card).length : 0,
      words: cards[0]?.words.slice(0, 240) ?? null,
    }
  })

// The room's own row for one slot, as the table reads it.
const rowFor = (slot) =>
  readAll((wanted) => {
    const rows = window
      .__deepAll('open-house-room-settings tbody tr')
      .filter((row) => !window.__deepText(row).includes('Bind for the house'))
    for (const row of rows) {
      const words = window.__deepText(row).replace(/\s+/g, ' ').trim()
      if (words.includes(wanted)) return words.slice(0, 200)
    }
    return null
  }, slot)

try {
  // -- A room whose area holds no devices -----------------------------------
  await openRoom('testroom')
  const empty = await picker()
  ok('no picker is open before one is asked for', empty.state === null && empty.selector === 0)

  const bind = await buttonIn('Light group', 'Bind')
  ok('the room offers to bind its light group slot', bind !== null, bind?.row ?? 'no Bind button')
  await page.mouse.click(bind.x, bind.y)
  await waitFor(() => {
    const settings = window.__deepAll('open-house-room-settings')[0]
    return settings?.picker && settings.picker.loading === false
  })
  const test = await picker()
  ok(
    'the selector is drawn for a room whose area holds no devices',
    test.selector === 1,
    `selectors: ${test.selector}, includes: ${test.state?.includes?.length ?? 'none'}`,
  )
  ok(
    'it is offered the whole house, not the room',
    test.state?.includes?.length >= 8 && test.state.includes.includes(KITCHEN_LIGHT),
    `${test.state?.includes?.length} entities, first ${test.state?.includes?.[0]}`,
  )
  ok(
    'the sentence says the room comes first and the house is behind it',
    (test.words ?? '').includes('first, then the rest of the house'),
    test.words ?? 'no picker card',
  )

  // Choose a light from another room: the write the complaint was about. HA's own
  // selector emits this when a person picks one (see the header).
  await readAll(
    (entity) => {
      const card = window
        .__deepAll('open-house-room-settings .card')
        .find((c) => window.__deepText(c).includes('Cancel'))
      const selector = window.__deepAll('ha-selector', card)[0]
      selector.dispatchEvent(
        new CustomEvent('value-changed', { detail: { value: entity }, bubbles: true, composed: true }),
      )
    },
    KITCHEN_LIGHT,
  )
  await sleep(2500)
  const bound = await rowFor('Light group')
  ok(
    "the room's slot can be pointed at a device from another room",
    bound !== null && bound.includes(KITCHEN_LIGHT),
    bound ?? 'no row',
  )

  // And back out, so the walk leaves the house as it found it.
  const unbind = await buttonIn('Light group', 'Unbind')
  ok('the binding can be taken back out', unbind !== null, unbind?.row ?? 'no Unbind button')
  await page.mouse.click(unbind.x, unbind.y)
  await sleep(2500)
  const cleared = await rowFor('Light group')
  ok(
    'and the row says nothing is bound',
    cleared !== null && cleared.includes('Nothing bound'),
    cleared ?? 'no row',
  )

  // -- A room with a light of its own ---------------------------------------
  await openRoom('Kitchen')
  // "Rebind" first: the kitchen arrives with its own light on its light group
  // slot, and the control that opens the picker on a bound slot is the replace
  // one whatever it is called.
  const kitchenBind =
    (await buttonIn('Light group', 'Rebind')) ?? (await buttonIn('Light group', 'Bind'))
  ok('the kitchen has a control for its light group slot too', kitchenBind !== null, kitchenBind?.row ?? 'none')
  await page.mouse.click(kitchenBind.x, kitchenBind.y)
  await waitFor(() => {
    const settings = window.__deepAll('open-house-room-settings')[0]
    return settings?.picker && settings.picker.loading === false
  })
  const kitchen = await picker()
  ok(
    "the kitchen's own light leads the list, with the rest of the house behind it",
    kitchen.state?.includes?.[0] === KITCHEN_LIGHT && kitchen.state.includes.length >= 8,
    `first ${kitchen.state?.includes?.[0]}, ${kitchen.state?.includes?.length} entities`,
  )
  ok(
    'a light from another room is in the same list',
    kitchen.state?.includes?.includes(ELSEWHERE_LIGHT) === true,
    ELSEWHERE_LIGHT,
  )
  // Leave with the picker closed rather than open on a page the next walk reads.
  await click(page, `${SCREEN} button`, { nth: 'Cancel', wait: 400 })
} catch (error) {
  ok('the walk ran to the end', false, String(error).slice(0, 300))
}
if (events.length > 0) console.log('EVENTS:', events.slice(0, 6).join(' | '))
await browser.close()
