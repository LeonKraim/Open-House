# ccostan — licence record

| | |
| --- | --- |
| Repository | `CCOSTAN/Home-AssistantConfig` |
| Author | Carlo Costanzo |
| Licence file | `LICENSE` |
| Code licence | `mit` |
| Prose licence | `mit` |
| Derived status (code) | `reusable` |
| Derived status (prose) | `reusable` |
| Obligations (code) | `attribution` |
| Author contact | not required — a licence file exists |

The source of truth is the `ccostan` record in [`catalog/licenses.yaml`](../../catalog/licenses.yaml).
This page states what that record permits; where the two disagree, the record
wins and the check that reads it will have failed.

## What may be reused

Both halves, under the MIT terms. MIT is the least restrictive licence in the
corpus that still attaches a condition, so this is the most material any single
repo can donate.

- **Code and structure.** Files, snippets and arrangements of YAML may be taken
  and adapted.
- **Expression.** Aliases, display names, comments and other authored text may
  be carried over. Under `reusable` there is no need to rewrite a name in our
  own words before shipping it.
- **Prose.** Documentation may be quoted, because the prose licence is MIT too.

## Obligations

`attribution`. Anything shipped that carries material from this repo must name
it and its author. `docs/attribution.md` is generated from the licence records
for exactly this purpose (task 6.4) and is where the obligation is discharged.

There is no `state_changes` obligation: MIT does not require that modifications
be marked.

## Vendored third-party carve-out

The MIT licence covers Carlo Costanzo's own work. It does **not** cover the
third-party components vendored into the repository — HACS-managed frontend
bundles under `www/community/`, custom cards, and anything else distributed by
its own authors under its own terms.

Vendored trees are excluded from the corpus by a named rule and never enter it,
and they are never attributed to this repo's author, who redistributed them but
did not write them. A file that is neither this author's original work nor
third-party vendored is excluded by a rule with its own reason rather than being
folded into either category.
