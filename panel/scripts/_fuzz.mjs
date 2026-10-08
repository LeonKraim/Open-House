// Scratch: every command, asked with nothing but its name (and then with
// hostile values), and the answer read back.
//
// The rule being tested is the one the panel's own error handling depends on:
// a command that is *asked badly* answers a clean refusal -- `invalid_format`
// for a payload that does not fit, `not_found` for a thing that is not there --
// and only a defect answers `unknown_error`, which is the raw 500 and the one
// answer the panel cannot say anything useful about. Swept over every command
// the protocol declares, because the defect is never in the command a person
// wrote the sweep for; it is in the one nobody has asked badly yet.
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { openPanel, BASE, sleep } from './_ui.mjs'

const PROTOCOL = fileURLToPath(new URL('../src/api/protocol.ts', import.meta.url))
const COMMANDS = [
  ...new Set(
    [...readFileSync(PROTOCOL, 'utf8').matchAll(/"(open_house\/[a-z0-9_/]+)"/g)].map((m) => m[1]),
  ),
].sort()

const { browser, page, events } = await openPanel()
await page.goto(`${BASE}/open-house`, { waitUntil: 'domcontentloaded' })
await sleep(4000)
const readAll = (fn, arg = null) => page.evaluate(fn, arg)

/** One command's answer, as the panel's own client sees it. */
const call = (message) =>
  readAll(
    (msg) =>
      window.__deepAll('open-house-panel')[0].hass.callWS(msg).then(
        (result) => ({ ok: true, result }),
        (failure) => ({
          ok: false,
          code: failure?.code ?? null,
          message: String(failure?.message ?? failure),
        }),
      ),
    message,
  )

const CLEAN = new Set(['invalid_format', 'not_found', 'home_assistant_error', 'unauthorized', 'unknown_command'])
// Fields a payload can be missing or wrong about. Named once here rather than
// per command: the point of a sweep is to ask commands questions their author
// did not write an answer for.
const FIELDS = [
  'room_id', 'pack', 'slot', 'slot_id', 'name', 'module', 'profile', 'behaviour',
  'entity_id', 'to', 'source', 'kind', 'value', 'when', 'device', 'part', 'parts',
  'path', 'url', 'id', 'revision', 'scope', 'enabled', 'priority', 'flow', 'outputs',
]
const HOSTILE = [
  ['null', null],
  ['a number where text is expected', 42],
  ['an empty string', ''],
  ['a path', '../../etc/passwd'],
  ['unicode', '💥‮\u0000'],
  ['a very long string', 'x'.repeat(4000)],
  ['a list', ['a']],
  ['a mapping', { a: 1 }],
]

const findings = []
const note = (label, verdict) => findings.push(`${label} -- ${verdict}`)

console.log(`sweeping ${COMMANDS.length} commands\n`)

// -- Sweep 1: the name and nothing else ------------------------------------
const bare = {}
for (const type of COMMANDS) {
  const answer = await call({ type })
  bare[type] = answer
  if (!answer.ok && !CLEAN.has(answer.code)) {
    note(`${type} with no payload`, `${answer.code}: ${answer.message.slice(0, 200)}`)
  }
}
const acceptedBare = Object.entries(bare).filter(([, a]) => a.ok).map(([t]) => t)
console.log(`answered without any payload: ${acceptedBare.length}`)
for (const type of acceptedBare) console.log(`  ${type}`)

// **Answering with no payload is not a finding, and saying it was made this
// sweep cry wolf on every run.** Half the protocol's commands are questions
// about the house -- "list the modules", "what does the overview say" -- and
// those take no arguments at all, so succeeding is the correct answer and the
// only thing that would be wrong is a refusal. Whether a *particular* one of
// them should have demanded something is a question about that command's own
// schema, which `tests/test_ws_contract.py` asks; it is not something a sweep
// can conclude from the answer alone. So they are listed and not reported.

// What the rest answered, so the sweep can be seen to have actually asked them
// something: a run where every command came back `unknown_command` would find
// nothing and would look exactly like a clean house.
const codes = {}
for (const [type, answer] of Object.entries(bare)) {
  const key = answer.ok ? 'ok' : (answer.code ?? 'no code')
  codes[key] = (codes[key] ?? 0) + 1
}
console.log(`\ncodes for the bare sweep: ${JSON.stringify(codes, null, 1)}`)
const unfuzzed = COMMANDS.filter((t) => bare[t].ok || ['unknown_command', 'unauthorized'].includes(bare[t].code))
console.log(`commands the hostile sweep skips: ${unfuzzed.length}`)

// -- Sweep 2: each declared field, each hostile value ----------------------
// Only on the commands that *have* a schema to be wrong about -- a command
// that accepted the bare name is answering about the house, not about its
// arguments, and injecting fields into it would be fuzzing the wrong thing.
console.log('\ninjecting hostile values...')
for (const type of COMMANDS) {
  if (acceptedBare.includes(type)) continue
  if (bare[type]?.code === 'unknown_command' || bare[type]?.code === 'unauthorized') continue
  for (const field of FIELDS) {
    for (const [label, value] of HOSTILE) {
      const answer = await call({ type, [field]: value })
      if (!answer.ok && !CLEAN.has(answer.code)) {
        note(`${type} with ${field} = ${label}`, `${answer.code}: ${answer.message.slice(0, 200)}`)
      }
    }
  }
}

console.log(`\n${findings.length} findings`)
for (const finding of findings) console.log(`  ${finding}`)
for (const event of events) console.log(`  ${event}`)
await browser.close()
