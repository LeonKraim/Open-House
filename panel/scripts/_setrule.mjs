import { openPanel, BASE, sleep } from './_ui.mjs'
const { browser, page } = await openPanel()
await page.goto(`${BASE}/open-house`, { waitUntil: 'domcontentloaded' })
await sleep(4000)
await page.evaluate(() => window.__deepAll('open-house-panel')[0].hass.callWS({
  type: 'open_house/modules/set_slot_rule', room_id: 'living_room', pack: 'critic_round3',
  slot: 'light_group', kind: 'template',
  value: "{{ 'light.garage_demo_garage' if is_state('light.open_house_mock_fleet_minimal_living_room','on') else 'light.open_house_mock_fleet_demo_bathroom' }}",
  when: [], device: null,
}))
await sleep(5000)
await browser.close()
