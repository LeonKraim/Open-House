// Scratch recon: what the server says about the rows a cast can go on, and what
// Home Assistant answers when the walk makes a script.
import { openPanel, sleep } from './_ui.mjs'

const { browser, page, events } = await openPanel()
const call = (msg) =>
  page.evaluate(
    (m) =>
      window.__deepAll('open-house-panel')[0].hass
        .callWS(m)
        .then((v) => ({ ok: v }))
        .catch((e) => ({ failed: String(e?.message ?? e) })),
    msg,
  )
const api = (method, path, body) =>
  page.evaluate(
    ([verb, url, data]) =>
      window.__deepAll('open-house-panel')[0].hass
        .callApi(verb, url, data)
        .then((v) => ({ ok: v }))
        .catch((e) => ({ failed: String(e?.message ?? e) })),
    [method, path, body],
  )

const listing = await call({ type: 'open_house/modules/hosted' })
for (const module of listing.ok?.modules ?? []) {
  console.log(`\n== ${module.slug}  room=${JSON.stringify(module.room_id)}`)
  console.log(`   automation_id=${JSON.stringify(module.automation_id)}`)
  for (const setting of module.settings ?? []) {
    console.log(
      `   row ${setting.name}: bound_kind=${JSON.stringify(setting.bound_kind)} ` +
        `bound_to=${JSON.stringify(setting.bound_to)} cast=${JSON.stringify(setting.cast)} ` +
        `flow=${JSON.stringify(setting.flow_id)} script=${JSON.stringify(setting.script_id)} ` +
        `value=${JSON.stringify(setting.value)}`,
    )
  }
}

console.log('\n-- making a script --')
console.log(
  'POST:',
  JSON.stringify(
    await api('POST', 'config/script/config/open_house_walk_script', {
      alias: 'Open House walk script',
      sequence: [{ stop: 'the value the walk asked for', response_variable: 'value' }],
    }),
  ).slice(0, 400),
)
for (const ms of [1000, 2000, 4000]) {
  await sleep(ms)
  const seen = await page.evaluate(() => {
    const hass = window.__deepAll('open-house-panel')[0].hass
    return Object.keys(hass.states).filter((id) => id.startsWith('script.'))
  })
  console.log(`after ${ms}ms more: ${JSON.stringify(seen)}`)
}

for (const event of events) console.log(`  ${event}`)
await browser.close()
