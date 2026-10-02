// Scratch: what appeared on screen after clicking "Add to..."?
import { openPanel, sleep, click } from './_ui.mjs'
const area = process.argv[2] ?? 'kitchen'
const { browser, page } = await openPanel()
try {
  await page.goto(`http://localhost:8123/config/areas/area/${area}`, { waitUntil: 'domcontentloaded' })
  await sleep(10000)
  const before = await page.evaluate(() => window.__deepAll('*').length)
  await click(page, 'button, ha-button', { nth: 'Add to…' })
  await sleep(4000)
  const dump = await page.evaluate(() => {
    const tags = [...new Set(window.__deepAll('*').map((e) => e.tagName.toLowerCase()))]
    const dialog = window.__deepAll('ha-dialog')[0]
    return {
      elements: window.__deepAll('*').length,
      textLen: window.__deepText(document.body).length,
      dialogHostText: dialog ? dialog.textContent.trim().slice(0, 400) : null,
      dialogShadow: dialog?.shadowRoot ? dialog.shadowRoot.textContent.trim().slice(0, 400) : null,
      newTags: tags.filter((t) => t.startsWith('ha-') || t.startsWith('wa-')).slice(0, 60),
    }
  })
  console.log('before elements:', before)
  console.log(JSON.stringify(dump, null, 1))
} finally { await browser.close() }
