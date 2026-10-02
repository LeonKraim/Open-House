// Scratch: what does the Light group picker offer?
import { openPanel, click, sleep } from './_ui.mjs'
const slot = process.argv[2] ?? 'Light group'
const { browser, page } = await openPanel()
try {
  await click(page, '#tab-rooms')
  await click(page, 'open-house-tab-rooms tbody tr a')
  await sleep(2500)
  const at = await page.evaluate((label) => {
    const row = window.__deepAll('open-house-room-settings table tbody tr')
      .find((r) => (r.querySelector('td')?.textContent ?? '').trim().startsWith(label))
    if (!row) return null
    const b = [...row.querySelectorAll('button')].find((b) => b.textContent.trim() === 'Bind')
    b.scrollIntoView({ block: 'center' })
    const r = b.getBoundingClientRect()
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 }
  }, slot)
  if (!at) throw new Error(`no ${slot} row`)
  await page.mouse.click(at.x, at.y)
  await sleep(3000)
  const picker = await page.evaluate(() => {
    const g = window.__deepAll('open-house-room-settings [role="group"]')[0]
    return {
      text: g ? window.__deepText(g).replace(/\s+/g, ' ').trim().slice(0, 400) : null,
      uses: g ? [...g.querySelectorAll('button')].filter((b) => b.textContent.trim() === 'Use this').length : 0,
    }
  })
  console.log(slot, '->', JSON.stringify(picker, null, 1))
} finally { await browser.close() }
