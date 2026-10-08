// Scratch: which engine-pack modules have a light slot, and how each is bound.
import { openPanel, BASE, sleep } from './_ui.mjs'
const { browser, page } = await openPanel()
await page.goto(`${BASE}/open-house`, { waitUntil: 'domcontentloaded' })
await sleep(4000)
const rows = await page.evaluate(() =>
  window.__deepAll('open-house-panel')[0].hass
    .callWS({ type: 'open_house/modules/list' })
    .then((r) =>
      (r.modules ?? []).flatMap((m) =>
        (m.slots ?? []).map((s) => ({
          pack: m.pack, room: m.room_id, slot: s.slot,
          entity: s.entity_id ?? '', rule: s.rule_kind ?? null,
          accepts: s.accepts_domains ?? [],
        })),
      ),
    ),
)
for (const r of rows) {
  console.log(`${r.pack} / ${r.room} / ${r.slot} -> ${JSON.stringify(r.entity)} rule=${JSON.stringify(r.rule)} accepts=${JSON.stringify(r.accepts)}`)
}
await browser.close()
