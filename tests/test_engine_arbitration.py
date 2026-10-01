"""Arbitration: one entity, one command -- task 6.1.

What these tests hold is the *ordering rule* and its totality: a user outranks
every behaviour, a higher priority outranks a lower one, a tie is broken by
behaviour id, and none of it depends on the order the proposals arrived in. The
last is the one that matters most and the one a naive implementation gets wrong,
so several tests vary the input order while holding everything else fixed.

Each test names the behaviour and says, in its docstring, what a falsifying
implementation would look like, because a test that would pass against any
implementation exercises nothing.
"""

from __future__ import annotations

from collections.abc import Sequence

from engine.adapter import ChangeContext
from engine.arbitration import Contention, Proposal, arbitrate
from engine.decision_log import ProposedCommand


def _proposal(
    actor: str,
    *,
    entities: tuple[str, ...] = ("light.kitchen",),
    action: str = "on",
    priority: int = 0,
    rule: str | None = "lighting.motion_light_on",
    context: ChangeContext | None = None,
) -> Proposal:
    return Proposal(
        actor=actor,
        rule=rule,
        priority=priority,
        command=ProposedCommand(
            slot="light_group",
            entities=entities,
            action=action,
            context=context if context is not None else ChangeContext.engine(),
        ),
    )


def _user(
    entities: tuple[str, ...] = ("light.kitchen",), action: str = "off"
) -> Proposal:
    return _proposal(
        "user",
        rule=None,
        entities=entities,
        action=action,
        context=ChangeContext.user(),
    )


def _winner(proposals: Sequence[Proposal], entity_id: str = "light.kitchen") -> str:
    contentions = arbitrate(proposals)
    for contention in contentions:
        if contention.entity_id == entity_id:
            return contention.winner.actor
    raise AssertionError(f"no contention for {entity_id}")


# --------------------------------------------------------------------------
# The reduction itself
# --------------------------------------------------------------------------


def test_two_behaviours_on_one_entity_yield_one_winner_and_one_loser() -> None:
    """One command survives per entity, and the rest come back as losers.

    A falsifying implementation that returned every proposal would apply two
    commands to one light in one tick -- on and off -- and the light's final
    state would depend on which actuation happened second, which is the last-
    writer-wins behaviour arbitration exists to replace.
    """
    motion = _proposal("motion_lighting", priority=0)
    away = _proposal("away_shutdown", action="off", priority=0)
    contentions = arbitrate([motion, away])
    assert len(contentions) == 1
    assert contentions[0].entity_id == "light.kitchen"
    assert contentions[0].winner.actor == "away_shutdown"
    assert [loser.actor for loser in contentions[0].losers] == ["motion_lighting"]


def test_an_uncontended_proposal_wins_with_no_losers() -> None:
    """A command nobody else wants is applied outright.

    A falsifying implementation that only produced a contention when there were
    two proposals would drop the commonest case -- one behaviour, one command --
    and the house would only work when behaviours conflicted.
    """
    contentions = arbitrate([_proposal("motion_lighting")])
    assert len(contentions) == 1
    assert contentions[0].winner.actor == "motion_lighting"
    assert contentions[0].losers == ()


def test_no_proposals_produce_no_contentions() -> None:
    """An empty tick arbitrates to nothing rather than to an empty winner.

    A falsifying implementation that returned a contention with a `None` winner
    would force every caller to special-case it, and the special case would be
    one a behaviour could forget.
    """
    assert arbitrate([]) == ()


def test_two_entities_produce_two_contentions_ordered_by_entity_id() -> None:
    """One contention per entity, in a fixed order.

    A falsifying implementation that ordered contentions by the order the
    proposals arrived in would make the tick's command order depend on the
    behaviour iteration order, and a replay that visited behaviours differently
    would write its records in a different sequence.
    """
    proposals = [
        _proposal("away_shutdown", entities=("light.kitchen",), action="off"),
        _proposal("motion_lighting", entities=("light.bedroom",)),
    ]
    assert [contention.entity_id for contention in arbitrate(proposals)] == [
        "light.bedroom",
        "light.kitchen",
    ]


def test_a_proposal_naming_two_entities_contends_for_both() -> None:
    """A house-scoped command is decided per entity, not per command.

    A falsifying implementation that keyed contention on the command would let
    one behaviour's two-entity command block a different behaviour's command to
    one of the same entities, and the second entity would receive two commands.
    """
    broad = _proposal("motion_lighting", entities=("light.kitchen", "light.bedroom"))
    narrow = _proposal(
        "away_shutdown", entities=("light.bedroom",), action="off", priority=5
    )
    contentions = {
        contention.entity_id: contention for contention in arbitrate([broad, narrow])
    }
    assert contentions["light.kitchen"].winner.actor == "motion_lighting"
    assert contentions["light.kitchen"].losers == ()
    assert contentions["light.bedroom"].winner.actor == "away_shutdown"
    assert [loser.actor for loser in contentions["light.bedroom"].losers] == [
        "motion_lighting"
    ]


# --------------------------------------------------------------------------
# The order
# --------------------------------------------------------------------------


def test_a_higher_priority_behaviour_wins() -> None:
    """Between two behaviours, the higher declared `priority` decides.

    A falsifying implementation that ignored priority would make the order the
    behaviours were written in decide every conflict, and a house could not be
    tuned to let one behaviour have its way without editing code.
    """
    assert (
        _winner(
            [
                _proposal("motion_lighting", priority=1),
                _proposal("away_shutdown", priority=9),
            ]
        )
        == "away_shutdown"
    )
    assert (
        _winner(
            [
                _proposal("motion_lighting", priority=9),
                _proposal("away_shutdown", priority=1),
            ]
        )
        == "motion_lighting"
    )


def test_a_user_action_outranks_every_behaviour_whatever_its_priority() -> None:
    """The user's command wins against a behaviour of any priority.

    A falsifying implementation that ranked the user as a behaviour with a very
    high priority would still lose to a behaviour declaring a higher one -- and a
    pack author who did so would have overridden the person in the room.
    """
    behaviour = _proposal("away_shutdown", action="on", priority=1000)
    assert _winner([behaviour, _user()]) == "user"


def test_a_tie_is_broken_by_ascending_behaviour_id() -> None:
    """Equal priorities are decided by `id`, in ascending order.

    A falsifying implementation that fell back on the input order would produce a
    different winner for the same inputs whenever the evaluation loop visited
    behaviours in a different sequence -- which is exactly the instability the
    tie-break exists to remove.
    """
    assert (
        _winner(
            [
                _proposal("motion_lighting", priority=3),
                _proposal("away_shutdown", priority=3),
            ]
        )
        == "away_shutdown"
    )
    assert (
        _winner(
            [
                _proposal("away_shutdown", priority=3),
                _proposal("motion_lighting", priority=3),
            ]
        )
        == "away_shutdown"
    )


def test_two_proposals_from_one_actor_are_still_ordered() -> None:
    """One actor's two commands to one entity are decided, not left to the sort.

    A falsifying implementation whose key stopped at the actor would leave them
    tied, and because `sorted` is stable the input order would decide -- the
    dependence the tie-break exists to remove. Nothing in this phase produces two
    commands from one unit to one entity; the case is asserted because the
    ordering rule is a claim about every input and not only about the ones that
    occur today.
    """
    on = _proposal("motion_lighting", action="on", priority=3)
    dim = _proposal("motion_lighting", action="dim", priority=3)
    assert _winner([on, dim]) == _winner([dim, on])


def test_the_winner_does_not_depend_on_the_order_the_proposals_arrived_in() -> None:
    """Every permutation of one tick yields the same winner and the same losers.

    This is the property the whole module is for, asserted over the permutations
    rather than over one reordering, because a falsifying implementation can
    happen to be stable for one pair and not for the next.
    """
    proposals = [
        _proposal("motion_lighting", priority=3),
        _proposal("away_shutdown", action="off", priority=3),
        _proposal("override", action="dim", priority=3),
    ]
    outcomes: set[tuple[str, tuple[str, ...]]] = set()
    for permutation in _permutations(proposals):
        contentions = arbitrate(permutation)
        assert len(contentions) == 1
        outcomes.add(
            (
                contentions[0].winner.actor,
                tuple(loser.actor for loser in contentions[0].losers),
            )
        )
    assert outcomes == {("away_shutdown", ("motion_lighting", "override"))}


def test_the_losers_come_back_in_ranked_order() -> None:
    """The losers are ordered by the same rule that ordered the winner.

    A falsifying implementation that returned the losers in input order would
    write their records in an order that depends on the evaluation loop, and two
    runs of one scenario would differ in their log's sequence even though both
    ended in the same state.
    """
    contentions = arbitrate(
        [
            _proposal("override", priority=1),
            _proposal("motion_lighting", priority=5),
            _proposal("away_shutdown", priority=5),
        ]
    )
    assert contentions[0].winner.actor == "away_shutdown"
    assert [loser.actor for loser in contentions[0].losers] == [
        "motion_lighting",
        "override",
    ]


def test_a_contention_names_the_entity_and_the_proposal_it_chose() -> None:
    """The result carries the contended entity id and the proposal that won it.

    A falsifying implementation that returned a bare winner would leave the
    caller to work out which entity it was for, and a tick with two contended
    entities would have to guess; one that rebuilt the winner rather than
    returning the proposal it was given would lose the `rule` and the command the
    record is written from, so the winner is asserted against the proposal itself
    and not against a `Contention` built from it.
    """
    proposal = _proposal("motion_lighting", entities=("light.hall",))
    assert arbitrate([proposal]) == (
        Contention(entity_id="light.hall", winner=proposal, losers=()),
    )


def _permutations(items: Sequence[Proposal]) -> list[list[Proposal]]:
    """Every ordering of `items`, so a test can hold the inputs fixed and vary only the order."""
    if len(items) <= 1:
        return [list(items)]
    orderings: list[list[Proposal]] = []
    for index, item in enumerate(items):
        rest = list(items[:index]) + list(items[index + 1 :])
        for ordering in _permutations(rest):
            orderings.append([item, *ordering])
    return orderings
