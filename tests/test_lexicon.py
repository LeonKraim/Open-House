"""The role lexicon -- task 4.2.

The lexicon is the one place the step from a reference to a slot is written down,
and it is a judgement rather than a fact, so the tests here are of two kinds.
The first kind pins the judgement per role: a real reference of each naming
convention resolves to the role a reader would expect, and a reference whose
domain and name disagree with the rule resolves to nothing -- the negative half
is what makes the positive half mean anything, since a resolver that returned
every role for every reference would pass a test that only checked membership.

The second kind pins the presentation rules the task states and the licence
position requires. Every entry must cite the source repo whose convention
produced it, and across the entries at least three of the four repos must
appear: a lexicon seeded from one author is a spelling, and the corpus exists to
avoid exactly that. Those two live in `check_lexicon`, which the coordinator
wires into the validator, and the check is exercised on the committed lexicon and
on deliberately broken ones so a check that had stopped reporting would fail a
test rather than pass one.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest

from tools.catalog import lexicon, licenses
from tools.catalog.errors import Report
from tools.catalog.lexicon import LexiconEntry

if TYPE_CHECKING:
    from collections.abc import Iterable


def _entry(
    role: str,
    domain: str,
    pattern: str,
    scopes: tuple[str, ...] = ("room",),
    source_repos: tuple[str, ...] = ("ccostan", "renemarc", "johnkoht"),
    why: str = "a reason the pattern means the role",
) -> LexiconEntry:
    """One rule for a fixture, compiled the way the module compiles its own."""
    return LexiconEntry(
        role=role,
        domain=domain,
        pattern=re.compile(pattern),
        scopes=scopes,
        source_repos=source_repos,
        why=why,
    )


#: A lexicon that satisfies every rule, so each negative test below is asserting
#: the one defect it introduces rather than a hole in the fixture it was built on.
#: Its repo union is all four, and its rules are distinct roles on distinct
#: domains, so no two findings collide.
_VALID: tuple[LexiconEntry, ...] = (
    _entry("light_group", "light", r"_lights$", ("room", "house")),
    _entry(
        "motion_sensor",
        "binary_sensor",
        r"motion",
        ("room",),
        ("fwartner", "renemarc", "johnkoht"),
    ),
)


def _findings(entries: Iterable[LexiconEntry] | None = None) -> list[tuple[str, str]]:
    """`check_lexicon`'s findings over the committed lexicon or a supplied one.

    The report is built and the check run through the same `CheckError` handling
    `validate_all` applies, so a fatal fault is asserted as the diagnostic the
    command returns rather than allowed to escape as a traceback. `validate_all`
    is not called directly because the module is new and its entry point may not
    yet be wired into the validator registry; routing through the identical
    handling keeps the behaviour under test the same either way.
    """
    report = Report()
    if entries is None:
        lexicon.check_lexicon(report)
    else:
        saved = lexicon.LEXICON
        lexicon.LEXICON = tuple(entries)
        try:
            lexicon.check_lexicon(report)
        finally:
            lexicon.LEXICON = saved
    return [
        (d.where, d.message)
        for d in report.diagnostics
        if d.check == lexicon.LEXICON_CHECK
    ]


def _messages(findings: list[tuple[str, str]]) -> str:
    return "\n".join(f"{where}: {message}" for where, message in findings)


# --------------------------------------------------------------------------
# The fixture itself
# --------------------------------------------------------------------------


def test_the_lexicon_fixture_is_clean() -> None:
    """The default fixture satisfies every rule, so each negative test below is
    asserting the one defect it introduced rather than a hole in the fixture."""
    assert _findings(_VALID) == []


# --------------------------------------------------------------------------
# Task 4.2 -- a unit test per role
# --------------------------------------------------------------------------

#: One real reference per role, drawn from the repo whose naming convention the
#: role's tokens come from. Every name here is a fact about a source repository's
#: identifiers, which is what the licence position admits, and the pattern that
#: matches it is our own regex over the role's tokens.
ROLE_CASES: tuple[tuple[str, str, str], ...] = (
    ("climate_zone", "climate", "main_bedroom"),
    ("contact_sensor", "binary_sensor", "basement_exterior_door"),
    ("cover", "cover", "large_garage_door"),
    ("fan", "fan", "studio"),
    ("house_mode", "input_select", "presence_mode"),
    ("humidity_sensor", "sensor", "bedroom_humidity"),
    ("leak_sensor", "binary_sensor", "kitchen_leak_sensor"),
    ("light_group", "light", "foyer_lights"),
    ("lock", "lock", "front_door"),
    ("lux_sensor", "sensor", "kitchen_illuminance"),
    ("media_player", "media_player", "plex"),
    ("motion_sensor", "binary_sensor", "hlk_kuche_belegung"),
    ("scene_selector", "input_select", "scene"),
    ("temperature_sensor", "sensor", "bedroom_temperature"),
    ("vacuum", "vacuum", "main_floor_vacuum"),
)


@pytest.mark.parametrize(("role", "domain", "object_id"), ROLE_CASES)
def test_a_reference_resolves_to_its_role(
    role: str, domain: str, object_id: str
) -> None:
    """Each role's own case, so a role whose rule was dropped fails by name."""
    assert role in lexicon.candidates(domain, object_id), (
        f"{domain}.{object_id} should be a candidate for {role}"
    )


def test_every_role_in_the_vocabulary_has_a_rule() -> None:
    """A vocabulary term with no rule is a slot 5.3 can never produce, so a role
    added to the list and not to the rules would look covered and be empty."""
    covered = {entry.role for entry in lexicon.LEXICON}
    assert set(lexicon.ROLE_VOCABULARY) <= covered


def test_no_entry_names_a_role_outside_the_vocabulary() -> None:
    assert {entry.role for entry in lexicon.LEXICON} <= set(lexicon.ROLE_VOCABULARY)


def test_a_reference_of_the_wrong_domain_gets_no_candidate() -> None:
    """The domain half of the key has teeth: the motion tokens on a `sensor`
    name no role, because no rule admits a motion sensor on that domain."""
    assert lexicon.candidates("sensor", "kitchen_motion_sensor") == ()


def test_a_fixture_that_is_not_a_group_gets_no_light_role() -> None:
    """The pattern half of the key has teeth: a lone fixture is not the group a
    room switches together, and `light_group` must not claim it."""
    assert lexicon.candidates("light", "torchiere") == ()


def test_a_reference_matching_two_rules_yields_both_candidates() -> None:
    """A reference is evidence for every role whose rule it matches -- a door
    that is also a leak sensor supports both, and picking one would need a
    precedence nothing in the corpus supplies."""
    found = lexicon.candidates("binary_sensor", "basement_exterior_door_leak_sensor")
    assert "contact_sensor" in found
    assert "leak_sensor" in found


# --------------------------------------------------------------------------
# The room context
# --------------------------------------------------------------------------


def test_the_room_context_narrows_a_room_scoped_role_away() -> None:
    """A motion sensor is room-scoped, so asking for it at the house finds
    nothing -- the scope key is what keeps a room role out of the house."""
    assert lexicon.candidates("binary_sensor", "basement_motion_sensor", "house") == ()
    assert lexicon.candidates("binary_sensor", "basement_motion_sensor", "room") == (
        "motion_sensor",
    )


def test_an_omitted_context_returns_the_domain_and_pattern_matches() -> None:
    """Stage A's scope is coarse, so a caller without one still gets candidates;
    the room context refines the answer and is not a precondition for it."""
    assert lexicon.candidates("input_select", "house") == ("house_mode",)
    assert lexicon.candidates("input_select", "house", "house") == ("house_mode",)
    assert lexicon.candidates("input_select", "house", "room") == ()


def test_scope_for_reports_the_context_stage_a_cannot() -> None:
    """`house_mode` is house-scoped while stage A labels every `input_select` a
    room's, and this is the answer 5.3 revisits that inference with."""
    assert lexicon.scope_for("house_mode") == ("house",)
    assert lexicon.scope_for("motion_sensor") == ("room",)


def test_accepts_domains_lists_the_domains_of_a_role() -> None:
    assert lexicon.accepts_domains("motion_sensor") == ("binary_sensor",)
    assert lexicon.accepts_domains("light_group") == ("light",)


# --------------------------------------------------------------------------
# Task 4.2 -- every entry cites its source repo, and at least three repos seed it
# --------------------------------------------------------------------------


def test_every_entry_cites_its_source_repo() -> None:
    """The clause stated as a property of the data rather than through the
    diagnostic that enforces it: a rule with no provenance is an invention."""
    for entry in lexicon.LEXICON:
        assert entry.source_repos, f"{entry.role} on {entry.domain} cites no repo"


def test_every_cited_repo_is_one_the_project_draws_from() -> None:
    known = {record.repo for record in licenses.load_licences() if record.repo}
    assert known, "the licence records name no repos to compare against"

    for entry in lexicon.LEXICON:
        assert set(entry.source_repos) <= known, entry.role


def test_the_lexicon_is_seeded_from_at_least_three_repos() -> None:
    """A vocabulary drawn from one or two repos is one author's spelling, which
    is the thing the corpus exists to avoid."""
    seeded = {repo for entry in lexicon.LEXICON for repo in entry.source_repos}
    assert len(seeded) >= 3, sorted(seeded)


# --------------------------------------------------------------------------
# The check on the committed lexicon and on broken ones
# --------------------------------------------------------------------------


def test_the_committed_lexicon_passes_its_check() -> None:
    assert _findings() == []


def test_an_empty_lexicon_is_named() -> None:
    """A lexicon with no rules maps no reference to any slot; the finding names
    the module rather than passing silently over an empty tuple."""
    findings = _findings(())
    assert len(findings) == 1, _messages(findings)
    assert "defines no entries" in findings[0][1]


def test_an_entry_citing_no_repo_is_named() -> None:
    broken = (
        _VALID[0],
        _entry("motion_sensor", "binary_sensor", r"motion", source_repos=()),
    )
    findings = _findings(broken)
    assert len(findings) == 1, _messages(findings)
    assert "cites no source repo" in findings[0][1]
    assert "motion_sensor" in findings[0][0]


def test_an_entry_citing_an_unknown_repo_is_named() -> None:
    broken = (
        _VALID[0],
        _entry(
            "motion_sensor",
            "binary_sensor",
            r"motion",
            source_repos=("fwartner", "renemarc", "someoneelse"),
        ),
    )
    findings = _findings(broken)
    assert len(findings) == 1, _messages(findings)
    assert "someoneelse" in findings[0][1]
    assert "not one of the repositories the project draws from" in findings[0][1]


def test_a_role_outside_the_vocabulary_is_named() -> None:
    broken = (_VALID[0], _entry("banana_sensor", "binary_sensor", r"banana"))
    findings = _findings(broken)
    assert len(findings) == 1, _messages(findings)
    assert "banana_sensor" in findings[0][1]
    assert "outside the controlled vocabulary" in findings[0][1]


def test_an_invalid_domain_is_named() -> None:
    broken = (_VALID[0], _entry("motion_sensor", "Binary Sensor", r"motion"))
    findings = _findings(broken)
    assert len(findings) == 1, _messages(findings)
    assert "not a Home Assistant domain" in findings[0][1]


def test_an_empty_scope_set_is_named() -> None:
    broken = (_VALID[0], _entry("motion_sensor", "binary_sensor", r"motion", scopes=()))
    findings = _findings(broken)
    assert len(findings) == 1, _messages(findings)
    assert "scopes" in findings[0][1]


def test_a_scope_outside_the_closed_set_is_named() -> None:
    broken = (
        _VALID[0],
        _entry("motion_sensor", "binary_sensor", r"motion", scopes=("floor",)),
    )
    findings = _findings(broken)
    assert len(findings) == 1, _messages(findings)
    assert "scopes" in findings[0][1]


def test_a_pattern_that_matches_everything_is_named() -> None:
    """A pattern that matches the empty string matches every object id, so it
    decides every reference of its domain and names no role."""
    broken = (_VALID[0], _entry("motion_sensor", "binary_sensor", r".*"))
    findings = _findings(broken)
    assert len(findings) == 1, _messages(findings)
    assert "matches the empty string" in findings[0][1]


def test_a_lexicon_seeded_from_too_few_repos_is_named() -> None:
    """Two repos is one author's spelling twice over; the finding says how many
    it found rather than only that it found too few."""
    thin = (
        _entry(
            "light_group",
            "light",
            r"_lights$",
            ("room", "house"),
            ("ccostan", "renemarc"),
        ),
        _entry(
            "motion_sensor",
            "binary_sensor",
            r"motion",
            ("room",),
            ("ccostan", "renemarc"),
        ),
    )
    findings = _findings(thin)
    assert len(findings) == 1, _messages(findings)
    assert "fewer than three of the four" in findings[0][1]


def test_the_check_reports_every_defect_rather_than_the_first() -> None:
    """The check collects diagnostics rather than returning on the first, so a
    lexicon with two broken entries is fixed in one pass."""
    broken = (
        _entry("banana_sensor", "binary_sensor", r"motion"),
        _entry("motion_sensor", "binary_sensor", r"motion", source_repos=()),
    )
    findings = _findings(broken)
    assert len(findings) == 2, _messages(findings)
    where = {location for location, _ in findings}
    assert len(where) == 2, where
