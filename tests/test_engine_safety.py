"""The safety veto: no non-user origin may unlock a lock or open a cover.

Task 11.1, and the enforcement point of the second product rule
(`product-invariants`: "nothing in the system may ever auto-unlock a door or
auto-open a garage"). The gate asks one question -- is this command an egress
action, and if so is its origin *positively* the user's? -- so the tests come in
pairs: every non-user origin is refused, the user's is permitted, and a command
that is not an egress action is permitted whatever its origin.

Two of the tests are about the gate's *shape* rather than its verdicts, and they
are the ones a plausible implementation gets wrong. The fail-closed test pins
that an absent context is refused -- a gate that read `None` as "not the engine's,
so fine" would open a door for every caller that forgot to say who it was. The
spelling test pins that the action set is per domain and closed: the port writes
a *state*, so a command that writes `on` to a lock is an unlock, and a gate that
matched only the literal strings `unlock` and `open` would pass it through.

Each test says, in its docstring, what a falsifying implementation would look
like, because a test that would pass against any implementation exercises nothing.
"""

from __future__ import annotations

import pytest

from engine.adapter import ChangeContext, ChangeOrigin, InvalidEntityIdError
from engine.safety import EGRESS_ACTIONS, is_egress, refuses

#: The two domains with an egress action, and the two with none, named once so
#: a test that means "a domain without an egress action" says so without
#: restating the list this module is about.
_EGRESS_DOMAINS = ("lock", "cover")
_OPEN_DOMAINS = ("light", "switch", "fan", "input_select")


# --------------------------------------------------------------------------
# The verdict, by origin
# --------------------------------------------------------------------------


def test_a_user_unlocking_a_lock_is_permitted() -> None:
    """A person opening their own door is the one case the rule does not cover.

    A falsifying implementation that refused every unlock would be a house that
    will not obey its occupant, and the product rule is a ban on the *system*
    acting rather than on the user.
    """
    assert refuses("lock.front_door", "unlocked", context=ChangeContext.user()) is False


def test_a_user_opening_a_cover_is_permitted() -> None:
    """The same, for the second egress domain: a person opening their garage."""
    assert refuses("cover.garage", "open", context=ChangeContext.user()) is False


@pytest.mark.parametrize(
    "origin",
    [ChangeOrigin.ENGINE, ChangeOrigin.WORLD, ChangeOrigin.FAULT],
)
@pytest.mark.parametrize(
    "entity_id,action", [("lock.front_door", "unlocked"), ("cover.garage", "open")]
)
def test_no_non_user_origin_may_unlock_or_open(
    origin: ChangeOrigin, entity_id: str, action: str
) -> None:
    """Engine, world and fault origins are all refused, on both egress domains.

    A falsifying implementation that refused only its own writes would let an
    external integration -- or a fault that has gone wrong -- open the door, and
    the rule is about the system rather than about the engine's half of it.
    """
    context = ChangeContext(origin)
    assert refuses(entity_id, action, context=context) is True


def test_an_absent_context_is_refused() -> None:
    """The ambiguous case fails closed, which is the requirement's own wording.

    A falsifying implementation that read `None` as "not positively the engine's,
    so let it through" would open the door for every caller that forgot to say
    who it was -- and the case is unreachable from any well-typed call site, so
    nothing else in the suite would notice.
    """
    assert refuses("lock.front_door", "unlocked", context=None) is True


def test_an_absent_context_permits_only_a_command_with_no_egress_action() -> None:
    """Failing closed is a rule about egress commands, not about every command.

    The gate's one question is "is this an unlock or an open, and if so whose?",
    so a command to a light is permitted whatever its origin -- including an
    origin nobody stated. A falsifying implementation that read `None` as "refuse
    everything" would be safe and useless: it would stop the engine lighting a
    room, which is not a command the product rule has anything to say about.

    The pairing is the point: `None` is *refused* for an egress action and
    *permitted* for a light, so the refusal is a fact about the action and not a
    blanket answer to a missing context.
    """
    assert refuses("light.kitchen", "on", context=None) is False
    assert refuses("lock.front_door", "unlocked", context=None) is True


# --------------------------------------------------------------------------
# What counts as an egress action
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "action", sorted(EGRESS_ACTIONS["lock"] | EGRESS_ACTIONS["cover"])
)
def test_every_spelling_in_the_set_is_an_egress_action(action: str) -> None:
    """Every member of the declared set is refused for a non-user origin.

    A falsifying implementation that matched only `unlock` and `open` would pass
    a command that writes `on` to a lock straight through, which is exactly the
    wire form the port uses -- `actuate(entity_id, state)` -- so the mistake would
    be invisible at every call site that reads naturally.
    """
    domain = next(d for d in _EGRESS_DOMAINS if action in EGRESS_ACTIONS[d])
    assert is_egress(domain, action) is True
    assert refuses(f"{domain}.thing", action, context=ChangeContext.engine()) is True


def test_locking_a_lock_is_not_an_egress_action() -> None:
    """Writing the *safe* state of an egress domain is permitted to the engine.

    A falsifying implementation that matched the whole `lock` domain rather than
    its egress actions would refuse the engine re-locking a door -- which is the
    safe direction, so no safety test would catch it, but it would also make the
    one command that *increases* safety the one the engine cannot send.
    """
    assert is_egress("lock", "locked") is False
    assert is_egress("lock", "off") is False
    assert refuses("lock.front_door", "locked", context=ChangeContext.engine()) is False


def test_closing_a_cover_is_not_an_egress_action() -> None:
    """The same for the second domain: closing a garage is not opening it."""
    assert is_egress("cover", "closed") is False
    assert is_egress("cover", "off") is False
    assert refuses("cover.garage", "closed", context=ChangeContext.engine()) is False


@pytest.mark.parametrize("domain", _OPEN_DOMAINS)
def test_a_domain_with_no_egress_action_is_never_refused(domain: str) -> None:
    """A light, a switch, a fan and a selector may be driven by the engine.

    A falsifying implementation written as a whitelist of *safe* domains would
    refuse every domain a later phase adds until somebody remembered to list it,
    so the check is stated as a denial list and a domain absent from it is safe.
    """
    assert is_egress(domain, "on") is False
    assert refuses(f"{domain}.thing", "on", context=ChangeContext.engine()) is False


def test_a_domain_with_no_egress_action_ignores_the_action_spelling() -> None:
    """Even the words `open` and `unlocked` are safe outside an egress domain.

    This is the case a single global word list gets wrong: `media_player` has no
    unlock, so a command to it is never refused on this ground, whatever the
    command is called.
    """
    assert is_egress("media_player", "open") is False
    assert (
        refuses("media_player.living_room", "unlocked", context=ChangeContext.engine())
        is False
    )


def test_the_egress_set_names_exactly_the_two_domains() -> None:
    """The closed set is `lock` and `cover`, and nothing else has an egress action.

    A falsifying implementation that added a third domain -- or that carried the
    set as a list of *permitted* domains -- would change which commands the
    engine may send without any of the verdict tests above moving, because none
    of them names a domain outside these two.
    """
    assert set(EGRESS_ACTIONS) == {"lock", "cover"}
    assert EGRESS_ACTIONS["lock"] == frozenset({"on", "unlocked", "unlock"})
    assert EGRESS_ACTIONS["cover"] == frozenset({"on", "open", "opening"})


def test_the_action_sets_are_frozen() -> None:
    """A caller cannot widen the egress set by mutating what it was handed.

    A falsifying implementation that exported plain sets would let a test, a
    fixture or a pack add an action to the whole process's notion of an unlock.
    """
    for actions in EGRESS_ACTIONS.values():
        assert isinstance(actions, frozenset)


# --------------------------------------------------------------------------
# The id is validated before a domain is derived from it
# --------------------------------------------------------------------------


def test_a_malformed_entity_id_is_refused_rather_than_read_as_safe() -> None:
    """An id that is not `domain.object_id` cannot be judged, so it fails.

    A falsifying implementation that split the id and looked the domain up would
    read `front_door` as a domain with no egress action and permit the command,
    so an id that could never be bound would be the one shape the veto waves
    through.
    """
    with pytest.raises(InvalidEntityIdError):
        refuses("front_door", "unlocked", context=ChangeContext.engine())
