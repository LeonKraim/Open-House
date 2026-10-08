// Scratch: take a script cast back off every row that holds one, and remove the
// walk's own script. For cleaning up after a run that fell over half way.
import { openPanel, sleep } from './_ui.mjs'

const SCRIPT_ID = 'open_house_walk_script'
const { browser, page, events } = await openPanel()
const ws = (message) =>
  page.evaluate(
    (msg) =>
      window.__deepAll('open-house-panel')[0].hass
        .callWS(msg)
        .then((v) => ({ ok: v }))
        .catch((e) => ({ failed: String(e?.message ?? e) })),
    message,
  )
const api = (method, path) =>
  page.evaluate(
    ([verb, url]) =>
      window.__deepAll('open-house-panel')[0].hass
        .callApi(verb, url)
        .then((v) => ({ ok: v }))
        .catch((e) => ({ failed: String(e?.message ?? e) })),
    [method, path],
  )

const listing = await ws({ type: 'open_house/modules/hosted' })
for (const module of listing.ok?.modules ?? []) {
  for (const setting of module.settings ?? []) {
    if (setting.script_id !== SCRIPT_ID) continue
    console.log(`clearing ${module.slug} / ${setting.name}`)
    const cleared = await ws({
      type: 'open_house/modules/settings',
      module: module.slug,
      bindings: {},
      scripts: { [setting.name]: '' },
      revision: 0,
    })
    console.log(`  -> ${JSON.stringify(cleared).slice(0, 200)}`)
    await sleep(1500)
  }
}

const left = await ws({ type: 'open_house/modules/hosted' })
const held = (left.ok?.modules ?? []).flatMap((module) =>
  (module.settings ?? [])
    .filter((setting) => setting.script_id)
    .map((setting) => `${module.slug}/${setting.name}=${setting.script_id}`),
)
console.log(`still holding a script: ${JSON.stringify(held)}`)

if (held.length === 0) {
  console.log(`removing ${SCRIPT_ID}: ${JSON.stringify(await api('DELETE', `config/script/config/${SCRIPT_ID}`))}`)
}

for (const event of events) console.log(`  ${event}`)
await browser.close()
