import { openPanel, click, sleep } from './_ui.mjs'
const { browser, page } = await openPanel()
await click(page, '#tab-dev', { wait: 1500 })
await page.evaluate(() => {
  const b = window.__deepAll('button').find((x) => (x.textContent ?? '').includes('Import as a module'))
  if (b) b.click()
})
await sleep(1500)
const out = await page.evaluate(() => {
  const body = window.__deepText(document.body)
  return {
    hasHosts: body.includes('Modules this house hosts'),
    heads: window.__deepAll('open-house-host-module h2').map((h) => h.textContent.trim()),
    anyHosts: [...document.querySelectorAll('*')].filter((n) => (n.textContent ?? '').includes('Modules this house hosts')).length,
    src: [...document.querySelectorAll('script[src]')].map((s) => s.src),
  }
})
console.log(JSON.stringify(out, null, 1))
await browser.close()
