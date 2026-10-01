# The `fan` role and slot

`catalog/slots.yaml` is a Phase 0 artifact: fourteen slots derived in that phase
from the corpus. This page records the one amendment Phase 2 makes to it, a
fifteenth slot named `fan`, and why that role is *derivable* from the corpus
rather than invented here. Every repository path spelled in backticks below is
asserted to exist by `tests/test_reference_docs.py`, so a path that moves fails a
test here rather than misleading a reader quietly.

## What the corpus holds

A role enters the vocabulary when real usage produced it. Thirty-six records in
`catalog/raw-behaviors.json` carry a `fan.*` reference, and they come from two
repositories. Between them they spell **twelve** distinct identifiers, naming the
appliance in five shapes:

| Spelling | Repository | What it names |
| --- | --- | --- |
| `fan.ventilator_wohnzimmer` | fwartner | a ventilator, whose identifier compounds the German noun for the device with the room it serves |
| `fan.office_air_purifier` | johnkoht | an air purifier, one per room |
| `fan.mudroom_bathroom_fan` | johnkoht | an extractor, named for its room and the appliance |
| `fan.winix_basement` | johnkoht | a purifier named by its brand and the room it stands in |
| `fan.studio` | johnkoht | a fan named by nothing but the room it stands in |

The table gives one spelling per shape rather than all twelve: the air-purifier
row stands for six rooms' purifiers, the extractor row for two, and the twelfth
identifier — `fan.air_purifiers` — is a group of the room units rather than a
device of its own, so it is taken up under scope below. The last row
is the one that decides the rule. `climate_zone`, `media_player` and `vacuum` are
derived from the domain alone on the grounds that every entity of that domain
already is the role, and the table above is the evidence that `fan` belongs to
that group: no token is common to the five shapes, and one of them carries no
device token at all. A pattern keyed on `ventilator` or `air_purifier` would miss
the extractor, the branded purifier and the bare room, so the rule matches any
well-formed identifier on the domain and the domain carries the role.

Every one of the shapes is a *room's* appliance — the air purifier in the office,
the extractor in the bathroom, the purifier in the basement, the fan in the
studio — so the role is
room-scoped and not house-scoped. The corpus does hold one name for purifiers
together rather than one of them, and it is a group of the room units that a
house-wide automation turns on at once, not a second appliance that serves the
floor plan on its own; that is the shape `vacuum` was drawn from, and nothing in
the corpus spells it for a fan.

## Where the rule lives

The step from a reference to a role is recorded once, in the `LEXICON` table in
`tools/catalog/lexicon.py`; the slot is written *from* that table rather than
beside it. Adding the role therefore means naming it in two files, of which the
pair that must agree is checked rather than trusted:

- `fan` joins `ROLE_VOCABULARY` in `tools/catalog/lexicon.py`, and a rule is
  added to the table naming the `fan` domain, the pattern and the two repos.
- A `fan` slot is added to `catalog/slots.yaml` with
  `accepts_domains: [fan]`.

`tools/catalog/slots.py` recomputes the accepting domains from the rule and fails
a slot that disagrees with the rule that decided it, so the name and the domains
in the vocabulary are a reading of the lexicon and not a second opinion.

## What the amendment does not claim

Both repositories the fan references come from grant nothing, so neither is
`reusable`. Each of the slot's two examples is cited to the raw records it was
drawn from and recorded as a description of the pattern in our own words; neither
transcribes an identifier of the records it cites. The reuse statuses an example
carries are copied from each repository's record in `catalog/licenses.yaml`, and
the check reads them from there rather than from a second table here.

The slot is not `required`. Lighting and occupancy are the two a room of almost
any type provides; a fan is one a particular room has and the rest do not, so a
room type that provides it names it in `catalog/room_types.yaml` and no room type
is obliged to.

## What the amendment moves

One of the extraction's three outputs changes, and by exactly the evidence above.
The hardcoding audit `catalog/hardcoded_refs.yaml` is one of them, and its rule is
that an entry resolves to a slot the vocabulary declares or to one of the fixed
constants — a reference resolving to neither is not committed, because it records
a reference nobody has decided what to do with. The twelve `fan.*` identifiers
resolved to neither while the vocabulary had no `fan` slot, so the file did not
carry them; with the slot declared they resolve, and the file now carries twelve
more entries. Nothing is removed and no other reference's resolution changes: the
store is *re-run* from the committed corpus rather than edited, which is what
`tests/test_normalise.py` holds it to. That is the amendment reaching the audit by
the vocabulary rather than by a hand edit to the audit.

Two further generated files change with it, and they are re-rendered rather than
edited too. `schemas/catalog/raw-behaviors.json` and
`schemas/catalog/hardcoded_refs.json` each carry the vocabulary's slot names as an
`enum`, so each gains `fan` — one line apiece, in the enum and nowhere else.
Both are rendered by `tools/catalog/facts.py`, which is the only thing that
writes them, so the re-render is `facts.write_raw_record_schema()` and
`facts.write_hardcoded_refs_schema()`, and `oh-catalog validate` refuses a hand
edit by comparing each file against a fresh generation. The other two extraction
stores, `catalog/raw-behaviors.json` and `catalog/inventory.json`, do not move at
all.

## What stays as Phase 0 left it

`docs/reference/phase-0-verification.md` records that the vocabulary carried
fourteen slots, and that number is what Phase 0 verified against the corpus at
the time. The number is kept there as a record of a verification that happened,
and the place in that document that states it says that Phase 2 has overtaken it
and points here; it was not silently rewritten into a fifteenth. The
other fourteen slots, their accepting domains, their examples, and the room types
are unchanged: the rule added for `fan` is the last entry in the table and names
its own domain, so no reference that resolved to a role before resolves to a
different one now.
