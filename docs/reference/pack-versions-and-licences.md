# Pack versions and licences

Two facts a pack author writes against, and neither is a fact about their pack:
the version of the format they are writing (published, chained and frozen) and
the licence code a manifest may declare (a published code with an SPDX identifier
behind it). This page states both. Every repository path it spells in backticks,
and every link on it, is asserted to exist by `tests/test_reference_docs.py`, so a
path that moves fails a test here rather than misleading a reader quietly.

## The chain

A published version is a file that is never written to again. Each version names
the one it replaces, and the arrow points **backwards**:

| Version file | `supersedes` |
| --- | --- |
| `schemas/pack-manifest/1.0.0.json` | `null` |
| `schemas/pack-manifest/1.1.0.json` | `1.0.0` |
| `schemas/pack-manifest/1.2.0.json` | `1.1.0` |
| `schemas/pack-manifest/1.3.0.json` | `1.2.0` |
| `schemas/pack-manifest/1.4.0.json` | `1.3.0` |

Pointing the arrow backwards is worth stating, because the forward-pointing form
is the one everyone writes first. With a `superseded_by` field, publishing a
successor means writing into the version being retired, and that write is an edit
to a frozen file: the act of publishing would be the violation. Retiring a
version is not a content change to it, it is the *absence* of any successor
naming it, so nothing is written and nothing needs to be.

Three clauses hold the chain together. All three are implemented in
[tools/catalog/schemas.py](../../tools/catalog/schemas.py) and all three run in
`oh-catalog validate`:

- **A step, not a chain and not a fork.** Every version but the oldest names its
  immediate predecessor, and no two versions name the same one. `1.2.0` naming
  `1.0.0` is refused, because the `1.1.0` between them would then be named by
  nothing.
- **Exactly one head.** A version is *current* when no other version present
  names it, and exactly one version must be. Two versions neither of which names
  the other leaves no current version at all, and the diagnostic names both.
- **Immutability.** A published version file must equal its content at the commit
  that introduced it. There is no recorded digest to compare against: the commit
  *is* the record, which is why the check reads git rather than a digest file.
  Deleting a version is a change too — a content comparison cannot see a file
  that is gone — so absence is checked against the paths git remembers adding.

## The current versions

| Artifact | Version | `supersedes` |
| --- | --- | --- |
| [schemas/pack-manifest/1.4.0.json](../../schemas/pack-manifest/1.4.0.json) | 1.4.0 | `1.3.0` |
| [schemas/engine-api/1.0.0.json](../../schemas/engine-api/1.0.0.json) | 1.0.0 | `null` |

`schemas/pack-manifest/1.2.0.json` closed `kind` to five kinds — `module`,
`room-template`, `behavior`, `profile-set`, `house-template` — added
`dependencies`, `conflicts`, `engine_api`, `license` and `i18n`, added
`derives_from` for a pack derived from a corpus row, extended
`$defs.provided.class` with `mode` while the other ten values were unchanged,
retired `min_engine_version` in favour of `engine_api`, and widened
`$defs.behaviour` with `priority`, `services` and `slots`.

`schemas/pack-manifest/1.3.0.json` supersedes it and adds three clauses: `match`
and `mode` on a behaviour, and a pack-level `slots`. `match` is the value beside
a `state` condition — the readings the behaviour acts on — and `mode` is the
value beside a `service` action whose act is entering a house mode rather than
writing an entity; both are optional, so every `1.2.0` pack is a `1.3.0` pack,
and both are literals the vocabulary can check rather than expressions the engine
must evaluate. `slots` lets a pack declare a device the catalog does not name,
each declaration carrying the `intent` that says what it is for and the
`accepts_domains` a binding may use. The versions it supersedes,
`schemas/pack-manifest/1.2.0.json`, `schemas/pack-manifest/1.1.0.json` and
`schemas/pack-manifest/1.0.0.json`, are retained and unedited, which is the whole
of what the chain above is for.

`schemas/pack-manifest/1.4.0.json` supersedes `1.3.0` and adds one clause:
`suppresses`, an optional list of pack names on a behaviour. A behaviour naming a
pack holds that pack's module off for as long as the behaviour is on, and it is
the first clause in the format that reaches outside the declaring pack. What it
reaches is exactly the target's module switch and nothing about the target's
configuration, which is what makes the suppression temporary by construction —
the engine derives it from which modules are currently on rather than writing
anything down, so it is undone the moment either switch moves. The clause names
whole packs rather than behaviours, settings or slots for the same reason: a finer
grain would be one pack editing another pack's configuration, and no uninstall
could put that back. A pack naming itself is refused (`self_suppression`), because
a pack that holds itself off can never run; a pack naming one that is not
installed holds nothing off rather than failing, because a manifest does not know
which house it will land in. It is optional, so every `1.3.0` pack is a `1.4.0`
pack.

## The API version policy

`schemas/engine-api/1.0.0.json` publishes one number: the engine API version a
manifest's `engine_api` range is checked against. The filename *is* the version —
this concept's versions are API versions — so the only field the document carries
that names one is `schema_version`, the field every published schema here carries
and which must equal the filename, and the current version is read through the
same succession rule as every other concept. The number moves only when what a manifest may **declare**
changes incompatibly, and not when the engine's implementation changes. The
closure of `kind` to the five kinds is the first such change, which is why 1.0.0
is published in the same change as `schemas/pack-manifest/1.2.0.json` rather than
earlier.

It is deliberately not `pyproject.toml`'s `version = "0.0.0"`. That is a
packaging value: it moves for packaging reasons and says nothing about what a
manifest may declare, so a range checked against it would be a check in name
only. The engine reads the published version through
[engine/vocabulary.py](../../engine/vocabulary.py) rather than declaring one,
because a constant in the engine would be a second publication of a promise the
artifact already makes.

## Licence codes

A manifest declares `license` as one of five published codes, never as an SPDX
string: the corpus already spells a licence as `mit` and `apache_2_0`, and a
manifest saying `MIT` would be a third spelling of one fact. The codes and their
SPDX identifiers are published side by side in
[schemas/catalog/licenses.json](../../schemas/catalog/licenses.json), so a
consumer reporting a licence has one place to read:

| Code | SPDX identifier |
| --- | --- |
| `public_domain` | `CC0-1.0` |
| `mit` | `MIT` |
| `apache_2_0` | `Apache-2.0` |
| `cc_by_nc_sa` | `CC-BY-NC-SA-4.0` |
| `no_licence` | `NONE` |

The order is least to most restrictive and is part of the publication: three of
the five codes share the status `reusable`, so an order over statuses could not
rank them, and the derivation rows are read against this order rather than
against a status. `NONE` is SPDX's own sentinel for a work whose licence grants
nothing, which is what `no_licence` records; the two ends of the table are
deliberately opposite, one granting everything and the other nothing.
