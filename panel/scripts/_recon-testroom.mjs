// Scratch recon: the testroom page and the v222 module's rows on it.
import { mkdirSync } from 'node:fs'
import { openPanel, BASE, sleep } from './_ui.mjs'

const OUT = 'S:/OS Folders/Desktop/HAOS_Open_House/shots'
mkdirSync(OUT, { recursive: true })
const { browser, page, events } = await openPanel()
await page.setViewportSize({ width: 1440, height: 1100 })
const readAll = (fn, arg = null) => page.evaluate(fn, arg)

const rooms = await readAll(() =>
  window.__deepAll('open-house-panel')[0].hass.callWS({ type: 'open_house/rooms/list' }),
)
console.log('rooms:', JSON.stringify(rooms).slice(0, 900))

await page.goto(`${BASE}/open-house?open_house_tab=rooms&open_house_room=testroom`, {
  waitUntil: 'domcontentloaded',
})
await sleep(5000)

const shape = await readAll(() => {
  const cards = window.__deepAll('open-house-hosted-module').map((card) => {
    const title = card.module?.title ?? card.module?.slug ?? null
    const parts = window.__deepAll('[data-slot-part]', card).map((s) => ({
      slot: s.getAttribute('data-slot-part'),
      value: s.value,
      options: [...s.options].map((o) => `${o.value}|${o.textContent.trim()}`),
    }))
    const rules = window.__deepAll('[data-slot-rule]', card).map((s) => ({
      slot: s.getAttribute('data-slot-rule'),
      kind: s.value,
    }))
    const settings = window.__deepAll('[data-setting]', card).map((f) => ({
      setting: f.getAttribute('data-setting'),
      mode: f.data?.cast_mode ?? null,
      text: window.__deepText(f).replace(/\s+/g, ' ').trim().slice(0, 80),
    }))
    return {
      slug: card.module?.slug,
      title,
      parts,
      rules,
      settings,
      text: window.__deepText(card).replace(/\s+/g, ' ').trim().slice(0, 700),
    }
  })
  return { cards, slotRows: window.__deepAll('[data-slot-rule]').length }
})

console.log(`\ncards on the testroom page: ${shape.cards.length}`)
for (const card of shape.cards) {
  console.log(`\n-- ${card.slug} | ${card.title}`)
  console.log(`   parts: ${JSON.stringify(card.parts)}`)
  console.log(`   rules: ${JSON.stringify(card.rules)}`)
  console.log(`   settings: ${JSON.stringify(card.settings)}`)
}

// The v222 card's own picture.
const box = await readAll(() => {
  const card = window.__deepAll('open-house-hosted-module').find((one) =>
    String(one.module?.slug ?? '').includes('v222'),
  )
  if (!card) return null
  card.scrollIntoView({ block: 'start' })
  const r = card.getBoundingClientRect()
  const top = Math.max(0, r.y - 10)
  return {
    x: Math.max(0, r.x - 10),
    y: top,
    width: Math.min(1400, r.width + 20),
    height: Math.min(1080 - top, r.height + 20),
  }
})
if (box) {
  await page.screenshot({ path: `${OUT}/testroom-v222.png`, clip: box })
  console.log(`\nwrote ${OUT}/testroom-v222.png ${JSON.stringify(box)}`)
} else {
  console.log('\nno v222 card on the testroom page')
  await page.screenshot({ path: `${OUT}/testroom-page.png` })
}

for (const event of events) console.log(`  ${event}`)
await browser.close()
