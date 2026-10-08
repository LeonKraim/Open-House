import { openPanel, sleep } from './_ui.mjs'
const { browser, page } = await openPanel()
const api = (method, path, body) =>
  page.evaluate(
    ([verb, url, data]) =>
      window.__deepAll('open-house-panel')[0].hass
        .callApi(verb, url, data)
        .then((v) => ({ ok: v }))
        .catch((e) => ({ failed: String(e?.message ?? e) })),
    [method, path, body],
  )
const service = (domain, name, data) =>
  page.evaluate(
    ([d, s, payload]) =>
      window.__deepAll('open-house-panel')[0].hass
        .callService(d, s, payload)
        .then((v) => ({ ok: v }))
        .catch((e) => ({ failed: String(e?.message ?? e) })),
    [domain, name, data],
  )
const state = (id) =>
  page.evaluate((x) => {
    const s = window.__deepAll('open-house-panel')[0].hass.states[x]
    return s === undefined ? null : s.state
  }, id)

console.log('create:', JSON.stringify(await api('POST', 'config/input_boolean/config/open_house_walk', { name: 'Open House walk' })).slice(0, 300))
await sleep(2500)
console.log('state now:', await state('input_boolean.open_house_walk'))
console.log('turn on:', JSON.stringify(await service('input_boolean', 'turn_on', { entity_id: 'input_boolean.open_house_walk' })).slice(0, 200))
await sleep(1000)
console.log('state after turn_on:', await state('input_boolean.open_house_walk'))
console.log('delete:', JSON.stringify(await api('DELETE', 'config/input_boolean/config/open_house_walk')).slice(0, 200))
await browser.close()
