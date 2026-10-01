# fwartner — licence record

| | |
| --- | --- |
| Repository | `fwartner/homeassistant-config` |
| Author | Florian Wartner |
| Licence file | none — `null` |
| Code licence | `no_licence` |
| Prose licence | `no_licence` |
| Derived status (code) | `ideas_only` |
| Derived status (prose) | `ideas_only` |
| Obligations | none |
| README claim | MIT |
| Author contact | `not_attempted` — see [`author-contact.md`](author-contact.md) |

The source of truth is the `fwartner` record in [`catalog/licenses.yaml`](../../catalog/licenses.yaml).

## The claim

This repository's README declares MIT. No licence file exists in it.

A README sentence is a statement about the work; a licence file is a grant of
rights in it. The two are not the same thing, and only the second one binds
anyone. Absent a file, no licence attaches to this repository's code or its
prose, and the default applies: all rights reserved by the author.

The claim is recorded rather than discarded because it is evidence of the
author's intent, and because it is the starting point for the contact ask in
[`author-contact.md`](author-contact.md). If the author confirms it, the record
changes and every derived value follows the table; until then the record is
`no_licence` for both halves and a discrepancy is recorded beside the claim.

## What may be reused

- **Ideas, and only ideas.** The repository is read for concepts — what it
  automates, which integrations it leans on, what problems its structure solves.
  Everything taken from it is restated in our own words.
- **Identifiers.** An entity identifier such as `light.kitchen_ceiling` is a
  recorded fact about a naming scheme rather than authored text, so the
  extraction commits identifiers from this repo along with the other three. The
  hardcoding audit cannot be performed at all without them. This is not an
  exception carved into the licence rule — the licence rule does not reach
  identifiers in the first place.

## What may not be reused

- **Expression of any kind.** No alias, display name, comment, block of YAML or
  other authored text may reach a shipped artifact. This is a stricter bar than
  a permissive licence would set and it is not a choice: with no grant, there is
  nothing to comply with.
- **Prose.** No passage from this repository may be reproduced anywhere,
  `docs/` included. Ideas from it are described in our own words, as above.
- A behaviour may still be *taken* from this repo — concepts are not
  licensable — but its `concept` field is written in our own words and its
  `expression` is empty.

## Vendored third-party carve-out

The question does not arise here, because nothing in this repository is in the
corpus for reuse. It is stated anyway for completeness: third-party components
vendored into the repository are under their own authors' licences, are excluded
by the vendored-tree rules, and would never be attributed to this author even if
the repository itself were licensed.
