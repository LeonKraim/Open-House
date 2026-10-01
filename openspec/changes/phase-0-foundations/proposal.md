# Proposal — Phase 0: Foundations

## Why

The product promises that a newcomer plugs devices into placeholder slots and
gets a working smart home. That promise is only credible if the defaults are the
*distilled* behaviour of real, battle-tested houses rather than guesses. We have
four such houses on disk (`ressources/`). Today they are four unrelated YAML
estates with differing licences, unknown overlap, and four naming conventions.

Phase 0 is the gate the whole project stands on. It does two jobs that
`spec.txt` defines as one phase: **0a** turns the four estates into one
provenance-tagged, licence-cleared behaviour corpus, and **0b** freezes the
versioned schemas and the repository layout that every later phase writes
against. Both halves land here because `spec.txt`'s exit criterion — all four
repos classified, slot vocabulary derived from real usage, and a hand-written
example house *and* pack validating against the schemas — cannot be met by
either half alone.

## What Changes

### 0a — Reference corpus

- **Repo identification.** Per repo: name, author, purpose, HA style, scale,
  HA version, and what it is best at.
- **Licence and attribution settlement.** Each licence read and judged; reuse
  status and obligations recorded before anything is adapted. Unlicensed repos
  donate **concepts and recorded facts but no authored text** — no alias, no
  comment, no YAML — and an author-contact attempt is logged. (An earlier draft
  said "quarantined to concept-only", which overstated it: identifiers from
  those repos are recorded, because the hardcoding audit cannot run without
  them.)
- **Full inventory.** Every tracked configuration artifact in all four repos
  classified exactly once, against a defined include/exclude rule set.
- **Behaviour extraction.** Per behaviour: what it does, its triggers,
  conditions, actions, and the slots it needs. Classified `generic`,
  `module_candidate` or `discard`.
- **Cross-repo comparison.** Behaviours appearing in ≥2 repos merge into one
  record; overlapping approaches compared and the winner recorded.
- **Hardcoding audit.** Every hardcoded entity reference per repo, with room or
  house scope, becoming slot candidates.
- **Slot vocabulary.** Derived from real usage across repos, with provenance
  per slot and controlled names.
- **Room-type map.** Default room types and per-type slot sets, plus a `house`
  scope, from the union of real rooms.
- **Edge cases and pain points.** Recorded separately: solved edge cases become
  simulator seeds; pain points become the product's "must do better" list.
- **Dependency list.** HACS integrations and add-ons per repo, marked
  `require` / `replace` / `avoid`.

### 0b — Schemas and repository

- **Frozen versioned schema set** for: room types, slots, houses, pack
  manifests, the trigger/condition/action vocabulary, modes, profiles, and the
  export document. Each schema versioned and immutable once published, with
  superseded versions retained. `spec.txt` describes the room-type and slot
  schemas as derived from the catalog; this proposal authors them *before* it,
  because the catalog's own files must validate against them. That deviation is
  recorded in the design rather than left as a discrepancy.
- **Repository layout** `engine/`, `ha_adapter/`, `custom_components/`,
  `panel/`, `packs/official/`, `sim/`, with the engine-purity invariant
  enforced from day one. The separate `registry/` repository cannot be created
  outside this project root and is deferred to Phase 7, which is where
  `spec.txt` puts the registry client; Phase 0 keeps only the half it can test,
  that no registry pointer files accrete here.
- **A hand-written example house, example pack and static example export** that
  validate against the schemas in CI — the example house and pack are
  `spec.txt`'s exit criterion; the export is shape-frozen here, its round-trip
  is Phase 3.
- **CI, linting, typing and pre-commit.**

### Deliverable

`catalog/` — one merged, provenance-tagged corpus whose behaviour rows carry
`id, name, description, category, scope, source_repos, required_slots,
optional_slots, concept, expression, raw_ids, reuse_status, obligations,
license, classification, retention, change_notice` — plus `schemas/` holding
the frozen 0b schema set. The `reference-catalog` spec's enumeration is
normative; this list tracks it.

## Capabilities

### New Capabilities

- `reference-catalog`: the merged behaviour corpus and everything derived from
  it — behaviour records, the hardcoding audit, the slot vocabulary, the
  room-type map, the edge-case and pain-point registers, and the integration
  dependency ledger.
- `attribution`: licence records, the obligation model, the strictness order,
  the reuse gate, and user-facing attribution.
- `configuration-schemas`: the frozen, versioned schema set that is the single
  source of truth for rooms, slots, packs, the behaviour vocabulary, modes,
  profiles and export, and the requirement that a hand-written example house
  and pack validate against it.
- `architecture-invariants`: the repository module layout and the purity rules
  that keep the engine free of Home Assistant imports and the registry
  separate.

### Modified Capabilities

_None — this is the first change; `openspec/specs/` is empty._

## Impact

- **New artifacts:** `catalog/` (data + schemas), `schemas/`, `docs/reference/`,
  `docs/attribution.md`, and the `engine/ ha_adapter/ custom_components/
  panel/ packs/official/ sim/` skeletons.
- **Consumed by:** Phase 1 (fixtures, scenarios, slot binding read the corpus;
  the engine is written against the vocabulary schema), Phase 2 (pack manifests
  and defaults), 3 (profile and export schemas), 4 (room-type detection), 8
  (attribution docs).
- **Constraints created:** the frozen schemas bind every later phase; the reuse
  classification binds what Phase 2 may ship.
- **No runtime behaviour.** No engine logic, no integration, no panel — the
  module skeletons are empty and their purity is asserted, not exercised.
- **External dependencies:** read access to the four local clones, used only by
  the one-time extraction, which runs locally and commits its outputs. CI reads
  this repository alone and requires no network.
- **Legal constraint, stated plainly:** two of four repos grant nothing.
  johnkoht ships no licence (all rights reserved) and fwartner *claims* MIT in
  its README but ships no licence file — a claim, not a grant. Only CCOSTAN
  (MIT) and renemarc (Apache-2.0 for code; CC BY-NC-SA for prose, which is
  non-commercial and share-alike) can donate material. The corpus therefore
  separates **concept** from **expression** and permits the latter only where a
  grant exists.
- **Unresolved by this proposal:** the author-contact asks in `spec.txt` are
  outbound actions and are recorded as attempts pending the user's authorisation
  to actually make contact.
