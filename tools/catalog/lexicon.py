"""The role lexicon: which slot a hardcoded reference is a candidate for.

Stage A records every entity reference the extraction found as a fact -- a
`domain.object_id`, a scope and a naming-convention label -- in
`catalog/hardcoded_refs.yaml`, and stops there on purpose. The step from a
reference to a *slot* is not a fact about the reference: `binary_sensor.x` is a
motion sensor in one place and a door contact in another, so the role lives in
the name and the naming convention, not in the domain. That step is a judgement,
and this module is where it is written down once -- so task 5.3 can populate
`catalog/slots.yaml` from it, so one vocabulary is shared rather than re-invented
per consumer, and so the judgement can be read and disagreed with instead of
buried in an extraction script.

The spec names no artifact for the lexicon. The home chosen is a module under
`tools/catalog/` and not a file under `catalog/`, and the two differ in what they
cost. Every file under `catalog/` needs a schema under `schemas/catalog/` and is
pinned by `tests/test_catalog_data.py`, so a new data file would add a schema and
break a pinned set -- for something that is a derivation rather than corpus data.
The corpus is written *to* the schemas (design D7), and a derivation belongs with
the other derivations, `licenses.derive` and `normalise`, which live here. Task
5.3 consumes `candidates()` and `accepts_domains()` directly, so the choice costs
it no rewrite.

The key is `(domain, name pattern, room context)`. The pattern is what separates
a real `binary_sensor.motion` from a real `binary_sensor.door`; the room context
separates a room's light group from the house's. Where the domain already fixes
the role -- a `climate` entity is a climate zone and a `lock` is a lock -- the
pattern admits any object id, and that is not a weakness in the key: D5 rejected
deriving *every* slot from the domain because `binary_sensor` is ambiguous, and
these entries state which domains are ambiguous and which are not instead of
leaving it unstated. The room context is a refinement rather than a
precondition, because the scope stage A infers is a property of the domain and
is revisited here; `candidates` therefore narrows on it when a caller has it and
matches domain and pattern alone when the caller does not.

Every entry cites the source repos whose naming convention produced it. Facts
about the repos are committable and their expression is not, so an entry holds a
pattern and repo names, never an alias, a display name or a copied identifier:
the pattern is a regex over the tokens a role is spelled with, written here and
not lifted from a file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import licenses
from .errors import Report

if TYPE_CHECKING:
    from re import Pattern

LEXICON_CHECK = "lexicon"

#: The artifact this check reports against, as a repository-relative path, so a
#: diagnostic points a reader at the one file the entries live in.
_LEXICON_PATH = "tools/catalog/lexicon.py"

#: The scopes a role can live in, matching the closed set the `reference-catalog`
#: spec uses for a behaviour's `scope`. `room` is a reference attached to one
#: room; `house` is one that only means something for the whole home -- a mode
#: selector, a vacuum, the house's own light group.
SCOPES: tuple[str, ...] = ("room", "house")

#: The controlled vocabulary of slot candidates. A role outside this tuple is a
#: spelling invented at a point of use, which is exactly what a controlled
#: vocabulary exists to refuse, so the check names it rather than admitting it.
ROLE_VOCABULARY: tuple[str, ...] = (
    "ambient_light_sensor",
    "climate_zone",
    "cover",
    "door_contact",
    "fan",
    "humidity_sensor",
    "leak_sensor",
    "light_group",
    "lock",
    "media_player",
    "motion_sensor",
    "scene_selector",
    "temperature_sensor",
    "vacuum",
)

#: A Home Assistant domain: the leading half of a `domain.object_id`, matched to
#: the same shape `normalise.ENTITY_REF_RE` admits so that a domain the lexicon
#: accepts can actually appear in `hardcoded_refs.yaml`.
_DOMAIN_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class LexiconEntry:
    """One rule: a reference of this domain, matching this pattern, at this
    scope, is a candidate for this role.

    `why` is the reason the pattern means the role -- a reviewer disagreeing with
    an entry needs the claim it is making, and the claim is not recoverable from
    a regex. `source_repos` is the provenance the task requires every entry to
    carry: the repo whose naming convention produced the rule, so a rule with no
    source is visible as an invention.
    """

    role: str
    domain: str
    pattern: Pattern[str]
    scopes: tuple[str, ...]
    source_repos: tuple[str, ...]
    why: str


def _entry(
    role: str,
    domain: str,
    pattern: str,
    scopes: tuple[str, ...],
    source_repos: tuple[str, ...],
    why: str,
) -> LexiconEntry:
    """Compile one rule's pattern into an entry.

    A builder rather than compiled literals at the call sites so the pattern
    string stays readable beside the role it decides, and so `re.compile` is
    applied once, at import, rather than per reference.
    """
    return LexiconEntry(
        role=role,
        domain=domain,
        pattern=re.compile(pattern),
        scopes=scopes,
        source_repos=source_repos,
        why=why,
    )


#: Every rule, in the order a caller receives its candidates. Seeded from the
#: naming conventions of all four repos: `johnkoht` spells domain, room and
#: function into every identifier, `fwartner` compounds German role nouns into
#: the object id, `renemarc` writes the room or the function as the whole id, and
#: `ccostan` is opaque in places but keeps room-suffixed light and cover groups.
#: No entry embeds an identifier from a repo that grants nothing -- only the
#: tokens the roles are spelled with.
LEXICON: tuple[LexiconEntry, ...] = (
    _entry(
        "light_group",
        "light",
        r"(?:_lights?$|_group$|^group$|^all$|^alle_lampen$)",
        ("room", "house"),
        ("ccostan", "fwartner", "johnkoht"),
        "a light whose id ends in the plural `_lights`, or names the group it "
        "belongs to, is the switchable unit a room or the house turns on "
        "together; a lone `light.torchiere` is a fixture, not the group",
    ),
    _entry(
        "motion_sensor",
        "binary_sensor",
        r"(motion|occupancy|presence|belegung|anwesenheit|bewegung|mmwave|multisensor|espresense)",
        ("room",),
        ("fwartner", "johnkoht", "renemarc"),
        "the role is the one D5 uses to reject domain-only derivation: the "
        "English tokens, the German `belegung`/`anwesenheit` and the device "
        "words a multisensor or mmWave node carries all name presence in a room",
    ),
    _entry(
        "door_contact",
        "binary_sensor",
        r"(door|window|contact|fenster|t[üu]r|gate)",
        ("room",),
        ("fwartner", "johnkoht", "renemarc"),
        "a door, window, gate or their German names is an opening contact; the "
        "token, not the domain, is what tells it from a motion sensor on the "
        "same domain",
    ),
    _entry(
        "leak_sensor",
        "binary_sensor",
        r"(leak|water|wasser)",
        ("room",),
        ("johnkoht", "renemarc"),
        "a water or leak contact is its own role because its binding differs -- "
        "it raises an alert rather than driving a light -- and it shares the "
        "binary_sensor domain with motion and opening contacts",
    ),
    _entry(
        "ambient_light_sensor",
        "sensor",
        r"(illuminance|lux)",
        ("room",),
        ("fwartner", "johnkoht"),
        "an ambient-light reading is the lux sensor design D5 names: it is "
        "distinct from a lamp's own brightness, which is why the pattern is "
        "`illuminance`/`lux` and not a bare `brightness`",
    ),
    _entry(
        "temperature_sensor",
        "sensor",
        r"(temperature|temperatur|_temp$|_temp_)",
        ("room",),
        ("ccostan", "fwartner", "johnkoht", "renemarc"),
        "a temperature reading, in either language, is the room's climate input",
    ),
    _entry(
        "humidity_sensor",
        "sensor",
        r"(humidity|feuchtigkeit)",
        ("room",),
        ("ccostan", "fwartner", "johnkoht", "renemarc"),
        "a humidity reading is its own role because a bathroom or a humidor "
        "binds it where other rooms do not",
    ),
    _entry(
        "climate_zone",
        "climate",
        r"^[a-z0-9_]+$",
        ("room",),
        ("ccostan", "fwartner", "johnkoht"),
        "the domain is unambiguous -- every `climate` entity is a heating or "
        "cooling zone -- so the pattern adds nothing and the domain alone "
        "carries the role",
    ),
    _entry(
        "media_player",
        "media_player",
        r"^[a-z0-9_]+$",
        ("room",),
        ("johnkoht", "renemarc"),
        "as with climate, the domain is the role: a `media_player` entity is a "
        "speaker or a display wherever it stands",
    ),
    _entry(
        "scene_selector",
        "input_select",
        r"scene",
        ("room", "house"),
        ("johnkoht", "renemarc"),
        "a selector naming a scene chooses a lighting mood; it is told from "
        "every other input_select by the `scene` token rather than by scope, "
        "because the home's own state is the engine's and is no slot at all",
    ),
    _entry(
        "cover",
        "cover",
        r"(garage|rollo|shade|blind|curtain|_cover$|door)",
        ("room",),
        ("ccostan", "fwartner", "johnkoht"),
        "a garage door or a roller blind -- `rollo` in the German repo -- is the "
        "cover a room or an entrance opens",
    ),
    _entry(
        "lock",
        "lock",
        r"^[a-z0-9_]+$",
        ("room", "house"),
        ("ccostan", "fwartner", "johnkoht"),
        "every `lock` entity is a lock, so the domain carries the role; it is "
        "listed at both scopes because a front door lock is read as the house's "
        "and an interior one as a room's",
    ),
    _entry(
        "vacuum",
        "vacuum",
        r"^[a-z0-9_]+$",
        ("house",),
        ("ccostan", "fwartner", "johnkoht"),
        "a vacuum cleans the floor plan rather than a room, so its role is "
        "house-scoped and the domain is unambiguous",
    ),
    _entry(
        "fan",
        "fan",
        r"^[a-z0-9_]+$",
        ("room",),
        ("fwartner", "johnkoht"),
        "as with climate, the domain is the role: every `fan` entity is an air "
        "mover for the space it stands in -- a ventilator, an extractor, an air "
        "purifier -- and the corpus spells one of them with nothing but the name "
        "of the room it serves, so no device token can be required of the "
        "pattern",
    ),
)


def candidates(
    domain: str, object_id: str, scope: str | None = None
) -> tuple[str, ...]:
    """The slot candidates a reference is drawn from, in lexicon order.

    A candidate rather than a verdict: `binary_sensor.basement_exterior_door_leak_sensor`
    matches both `door_contact` and `leak_sensor`, and that is the honest
    answer -- the reference is evidence for both, and 5.3 weighs it against the
    rest of the usage. Returning one role would require a precedence nothing in
    the corpus supplies. Roles are de-duplicated and ordered by `LEXICON`, so the
    result is a function of the inputs and not of iteration order.

    `scope` is the room context, and it is optional because stage A's scope is
    deliberately coarse: it reads the domain alone, and 5.3 is where that
    inference is revisited. Omitted, the match is
    on domain and pattern and every scope's roles are returned; supplied, it
    narrows to the roles that admit that context. A caller that has no scope
    observation still gets the candidates, and one that has a corrected scope
    gets the narrowing -- neither path is blocked by the other.
    """
    found: list[str] = []
    for entry in LEXICON:
        if entry.domain != domain or entry.pattern.search(object_id) is None:
            continue
        if scope is not None and scope not in entry.scopes:
            continue
        if entry.role not in found:
            found.append(entry.role)
    return tuple(found)


def accepts_domains(role: str) -> tuple[str, ...]:
    """The entity domains a slot of this role admits, sorted.

    Sorted so a caller building a `slots.yaml` record gets the same list on
    every run, which is what makes the generated artifact reviewable.
    """
    return tuple(sorted({entry.domain for entry in LEXICON if entry.role == role}))


def scope_for(role: str) -> tuple[str, ...]:
    """The scopes a role lives in, sorted.

    Exposed because stage A's scope inference is deliberately coarse -- it reads
    the domain alone -- while a role's own scopes are what 5.3 narrows it with.
    """
    return tuple(
        sorted({s for entry in LEXICON if entry.role == role for s in entry.scopes})
    )


def _known_repos() -> tuple[str, ...]:
    """The repositories the project draws from, from the licence records.

    Read from `licenses.yaml` rather than kept as a second list here, because two
    lists drift and the licence file already answers "who are the sources" for
    the rest of the package.
    """
    return tuple(
        sorted({record.repo for record in licenses.load_licences() if record.repo})
    )


def _where(entry: LexiconEntry, index: int) -> str:
    """A location for one rule that is stable and unique within the module.

    There is no id field on an entry -- a rule is identified by the role it
    decides and the domain it reads, and two rules for one role would collide --
    so the position disambiguates, and the reader can still find the rule by its
    role and domain.
    """
    return f"{_LEXICON_PATH}#{index + 1}:{entry.role}/{entry.domain}"


def check_lexicon(report: Report) -> None:
    """The lexicon's own rules, and the coverage the task requires.

    Two halves. Each entry must be well formed: a role in the vocabulary, at
    least one source repo drawn from the licence records, a domain the
    `entity_ref` shape admits, a non-empty subset of `SCOPES`, and a pattern that
    does not match the empty string -- a pattern that does dispatches every
    reference of its domain and names no role at all. And the lexicon as a whole
    must be seeded from at least three of the four repos, which is the clause
    task 4.2 states and the reason the entries carry `source_repos` in the first
    place.
    """
    if not LEXICON:
        report.add(
            LEXICON_CHECK,
            _LEXICON_PATH,
            "defines no entries; a lexicon with no rules maps no reference to "
            "any slot, and every later reader of the vocabulary would find it "
            "empty rather than wrong",
        )
        return

    known = _known_repos()
    repos_seen: set[str] = set()
    for index, entry in enumerate(LEXICON):
        where = _where(entry, index)
        if entry.role not in ROLE_VOCABULARY:
            report.add(
                LEXICON_CHECK,
                where,
                f"role `{entry.role}` is outside the controlled vocabulary "
                f"{list(ROLE_VOCABULARY)}; a slot name invented at a point of "
                "use is what the vocabulary exists to refuse",
            )
        if not entry.source_repos:
            report.add(
                LEXICON_CHECK,
                where,
                "cites no source repo; a rule with no provenance cannot be "
                "weighed against the naming convention it claims to come from, "
                "and reads as an invention",
            )
        for repo in entry.source_repos:
            if repo not in known:
                report.add(
                    LEXICON_CHECK,
                    where,
                    f"cites `{repo}`, which is not one of the repositories the "
                    f"project draws from {list(known)}; a rule attributed to a "
                    "source with no licence record is one adopted under a grant "
                    "nobody read",
                )
            else:
                repos_seen.add(repo)
        if not _DOMAIN_RE.match(entry.domain):
            report.add(
                LEXICON_CHECK,
                where,
                f"domain `{entry.domain}` is not a Home Assistant domain; no "
                "entry in hardcoded_refs.yaml could carry it, so the rule would "
                "match nothing",
            )
        if not entry.scopes or any(scope not in SCOPES for scope in entry.scopes):
            report.add(
                LEXICON_CHECK,
                where,
                f"scopes {list(entry.scopes)} are not a non-empty subset of "
                f"{list(SCOPES)}; a rule that matches at no scope never fires, "
                "and one at an unknown scope never matches a reference",
            )
        if entry.pattern.search("") is not None:
            report.add(
                LEXICON_CHECK,
                where,
                "pattern matches the empty string, so it matches every object "
                "id of its domain and names no role; a rule that decides every "
                "reference decides none",
            )

    if len(repos_seen) < 3:
        report.add(
            LEXICON_CHECK,
            _LEXICON_PATH,
            f"is seeded from {sorted(repos_seen)}, fewer than three of the four "
            "repositories; a vocabulary drawn from one or two repos is a single "
            "author's spelling, and the corpus exists to avoid exactly that",
        )
