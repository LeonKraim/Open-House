"""Arbitration: one entity, one command, by a fixed total order -- task 6.1.

A tick collects every command its behaviours propose, and two of them may name
the same entity -- away shutdown turning a light group off while motion lighting
turns it on, or a user action naming the light both behaviours wanted. This
module reduces those to exactly one command per entity, by priority, with a
user action outranking every behaviour and ties broken by ascending behaviour
`id`.

Last-writer-wins by scheduling order was rejected (`design.md` D6) because the
outcome would depend on the order behaviours happened to be visited in: the
same house would behave differently after a refactor of the evaluation loop, and
Phase 2's exit criterion -- "two modules cannot fight over one light" -- needs a
rule that names a winner and a reason rather than a rule that happens to work.

Two consequences are visible below. The comparison is a **total order**
(`_rank`), so the winner is determined by the inputs alone and not by the order
the proposals arrived in; and the losers are returned by the same order, so the
records naming them are written in the same sequence on every replay.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from engine.adapter import ChangeOrigin
from engine.decision_log import ProposedCommand


@dataclass(frozen=True, slots=True)
class Proposal:
    """One command a behaviour proposed, with what arbitration needs to rank it.

    The proposing behaviour's `id` and `rule` travel with the command rather than
    being read back out of a record, because arbitration runs *before* any record
    is written: the winner and the losers are what the records are then built
    from, not something they can be looked up in.
    """

    #: The behaviour unit `id`, or the user for a direct action.
    actor: str
    #: The corpus concept id the proposal implements, or `None` for a user action,
    #: which matches no corpus row.
    rule: str | None
    #: The behaviour's declared arbitration priority. Ignored for a user action,
    #: which outranks every behaviour whatever the behaviour's priority is.
    priority: int
    command: ProposedCommand


@dataclass(frozen=True, slots=True)
class Contention:
    """One entity's proposals, reduced to one winner and the losers it beat."""

    entity_id: str
    winner: Proposal
    losers: tuple[Proposal, ...]


def arbitrate(proposals: Sequence[Proposal]) -> tuple[Contention, ...]:
    """Reduce `proposals` to one winner per entity, ordered by entity id.

    A proposal naming several entities -- a house-scoped slot the messy fixture
    binds in two rooms -- contends for each of them separately, so one behaviour
    may win one light group and lose another. The entity id is the key because
    the spec's rule is about an *entity* receiving one command, and a command is
    the unit that carries the entity list rather than the unit that conflicts.

    **The caller applies one write per contention, not per command.** Because a
    broad command can win one entity and lose another, applying
    `winner.command` as it stands would write the entities the command lost as
    well; the write is `winner.command.action` against `entity_id` alone, and
    the command's own `entities` tuple is the contention's input rather than its
    output. Stated here because it is the contract the engine's tick has to
    honour and nothing in this module's return type enforces it.
    """
    by_entity: dict[str, list[Proposal]] = {}
    for proposal in proposals:
        for entity_id in proposal.command.entities:
            by_entity.setdefault(entity_id, []).append(proposal)
    contentions = []
    for entity_id in sorted(by_entity):
        ranked = sorted(by_entity[entity_id], key=_rank)
        contentions.append(
            Contention(
                entity_id=entity_id,
                winner=ranked[0],
                losers=tuple(ranked[1:]),
            )
        )
    return tuple(contentions)


def _rank(proposal: Proposal) -> tuple[int, int, str, str, str]:
    """The total order over proposals for one entity, smallest first.

    A user-origin command outranks every behaviour; between two behaviours the
    higher declared `priority` wins; a tie is broken by ascending `actor`. The
    last two components -- the target slot and the action -- exist to make the
    key *total* rather than merely total for the inputs this phase produces: two
    proposals from one behaviour to one entity would otherwise tie, and `sorted`
    being stable would then let the order they were evaluated in decide the
    winner, which is the dependence the tie-break is here to remove. They can
    only be reached by a future pack whose unit proposes twice to one entity, and
    when they are reached, the order between the two is arbitrary but fixed --
    which is what a deterministic replay needs and all it needs.

    A **safety** command sits between the user and the behaviours: it outranks
    every behaviour whatever that behaviour's priority, which is the safety
    audit's "not by arbitration losing to another behaviour" -- a hazard alert
    must not be shouted over by a motion rule or any pack. It still ranks below a
    person's own command, because the audit asks that alerts not be *suppressed*
    and not that they override somebody standing at the switch.
    """
    is_user = proposal.command.context.origin is ChangeOrigin.USER
    if is_user:
        tier = 0
    elif proposal.command.safety:
        tier = 1
    else:
        tier = 2
    return (
        tier,
        -proposal.priority,
        proposal.actor,
        proposal.command.slot,
        proposal.command.action,
    )
