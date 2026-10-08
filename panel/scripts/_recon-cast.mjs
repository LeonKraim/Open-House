// Scratch recon: does flipping a row's "Set it to" menu redraw the card?
//
// The question is narrow. The card draws a cast's editor from `castModes`, which
// is a plain field, so the only thing that can put the editor on the screen is a
// render -- and the only `requestUpdate()` on the save path is in the `finally`
// of `save()`, which returns early when a cast is open and still empty. This
// checks, on a live card, what actually happens to the row when the menu moves.
//
// Run: node scripts/_recon-cast.mjs
import { openPanel, all, click, sleep } from './_ui.mjs'

const { browser, page, events } = await openPanel()
const readAll = (fn, arg = null) => page.evaluate(fn, arg)

const SLUG = 'dynamic_lighting'
const SETTING = 'max_brightness_percent'

await click(page, '#tab-house', { wait: 2500 })
await page.waitForFunction(
  (slug) =>
    window.__deepAll('open-house-hosted-module').some((one) => one.module?.slug === slug),
  SLUG,
)

const look = async (when) => {
  const now = await readAll(
    ([slug, name]) => {
      const card = window.__deepAll('open-house-hosted-module').find((one) => one.module?.slug === slug)
      const form = window.__deepAll(`ha-form[data-setting="${name}"]`, card)[0]
      const editor = window.__deepAll('open-house-script', form?.parentElement ?? document)[0]
      return {
        castModes: JSON.stringify(card?.castModes ?? null),
        formMode: form?.data?.cast_mode ?? null,
        editorDrawn: Boolean(editor),
        renderCount: card?.__renderCount ?? null,
      }
    },
    [SLUG, SETTING],
  )
  console.log(`${when}: ${JSON.stringify(now)}`)
  return now
}

await look('before')

const dispatched = await readAll(
  ([slug, name]) => {
    const card = window.__deepAll('open-house-hosted-module').find((one) => one.module?.slug === slug)
    const form = window.__deepAll(`ha-form[data-setting="${name}"]`, card)[0]
    if (!form) return 'no form'
    // Count renders so the redraw question is answered rather than inferred.
    if (card.__renderCount === undefined) {
      card.__renderCount = 0
      const render = card.render.bind(card)
      card.render = (...args) => {
        card.__renderCount += 1
        return render(...args)
      }
    }
    form.dispatchEvent(
      new CustomEvent('value-changed', {
        detail: { value: { ...form.data, cast_mode: 'script' } },
        bubbles: true,
        composed: true,
      }),
    )
    return 'sent'
  },
  [SLUG, SETTING],
)
console.log(`dispatch: ${dispatched}`)

for (const ms of [300, 1500, 3000]) {
  await sleep(ms)
  await look(`after ${ms}ms more`)
}

// The card's own redraw, asked for by hand: if this draws the editor, the state
// was right all along and only the render was missing.
await readAll((slug) => {
  window.__deepAll('open-house-hosted-module').find((one) => one.module?.slug === slug)?.requestUpdate()
}, SLUG)
await sleep(500)
await look('after a hand-called requestUpdate')

for (const event of events) console.log(`  ${event}`)
await browser.close()
