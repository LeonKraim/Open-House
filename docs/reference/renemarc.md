# renemarc — licence record

| | |
| --- | --- |
| Repository | `renemarc/home-assistant-config` |
| Author | René-Marc Simard |
| Licence file | `LICENSE.txt` |
| Code licence | `apache_2_0` |
| Prose licence | `cc_by_nc_sa` |
| Derived status (code) | `reusable` |
| Derived status (prose) | `ideas_only` |
| Obligations (code) | `attribution`, `state_changes` |
| Author contact | not required — a licence file exists |

The source of truth is the `renemarc` record in [`catalog/licenses.yaml`](../../catalog/licenses.yaml).

## The split is the point of this record

This is the one repo whose two halves differ, and collapsing them would be wrong
in both directions. Its **code** is Apache-2.0, which permits reuse. Its
**prose** is CC BY-NC-SA 4.0 — non-commercial and share-alike — which does not.
A record carrying only one value would either bar code we are entitled to use or
license documentation we are not.

Behaviour rows reuse code, so they follow `reuse_status_code`. Quoted
documentation would follow `reuse_status_prose`, which is why none is quoted.
The repository states this split itself, in its own README.

## What may be reused

- **Code and structure.** Permitted. Patterns, YAML arrangements and file
  organisation may be taken and adapted. The design notes call renemarc's
  patterns the most instructive of the four repos.
- **Expression.** Permitted, with attribution and a change notice — see below.
  An alias or a name this author typed may be carried into a shipped artifact.

## What may not be reused

- **Prose, in any form.** No shipped artifact, `docs/` included, may contain a
  passage reproduced from this repository. The terms are non-commercial and
  share-alike, and share-alike material must not be incorporated.
- **Ideas from its prose are still available** — an idea is not the expression
  of it. Anything taken from this repo's documentation is restated in our own
  words, which is what this page does rather than quoting it.

## Obligations

- `attribution` — material shipped from this repo names it and its author.
- `state_changes` — this is the one repo in the corpus that carries it, and it
  is why the corpus has a change-notice rule at all. Every adapted row derived
  from this repo carries a `change_notice` naming what was changed and by whom,
  and the file containing such a row carries a file-level notice as well. Both
  are required: the row is the unit of adaptation, the file is the unit of
  distribution.

"By whom" is Open House, in our own words. There is no adapter component in
Phase 0 to name.

## Vendored third-party carve-out

The Apache-2.0 grant covers René-Marc Simard's own work. The repository also
contains material that is neither his original nor third-party vendored — the
altered copies of Home Assistant core files — and that material is excluded by a
rule with its own reason rather than being attributed to him, because he changed
those files rather than writing them. Genuinely vendored integrations and
frontend bundles sit alongside them and are excluded by the vendored-tree rules.
