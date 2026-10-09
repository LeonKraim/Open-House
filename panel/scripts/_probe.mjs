// Scratch: why a published module's row still offers Publish.
import { openPanel, sleep, click } from './_ui.mjs'

const { browser, page } = await openPanel()
try {
  await click(page, '#tab-store')
  await sleep(5000)
  const dump = await page.evaluate(() => {
    const tab = window.__deepAll('open-house-tab-store')[0]
    const split = tab?.split ?? null
    const offers = tab?.offers ?? []
    const row = (r) => ({ slug: r.slug, mine: r.mine, title: r.title, publisher: r.publisher })
    return {
      hasSplit: !!split,
      installed: (split?.installed ?? []).map(row),
      notInstalled: (split?.not_installed ?? []).map(row),
      offers: offers.map((o) => ({
        slug: o.slug,
        name: o.name ?? o.title,
        publishedHere: tab.publishedHere(o.slug),
      })),
      ids: {
        publish: window.__deepAll('[id^="store-publish-"]').map((n) => n.id),
        published: window.__deepAll('[id^="store-published-"]').map((n) => n.id),
      },
      status: tab?.status ?? null,
    }
  })
  console.log(JSON.stringify(dump, null, 2))
} finally {
  await browser.close()
}
