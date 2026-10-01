# Arbitration: one entity, one command

A tick collects every command its behaviours propose, and two of them may name
the same entity -- two packs both asking for one light, a house-scoped shutdown
and a room-scoped motion rule both wanting a lamp off. `engine/arbitration.py`
reduces each entity's proposals to exactly one command, by a **total order over
the proposals' own fields**, and returns the losers beside the winner.
`tests/test_pack_arbitration.py` asserts the rule this page states, end to end,
over two installed packs.

## The rule

The key is `engine/arbitration.py`'s `_rank`, read smallest-first:

| Order | Field | Read as |
| --- | --- | --- |
| 1 | the command's **origin** | a `user`-origin command outranks every behaviour, whatever its priority |
| 2 | the behaviour's **priority**, negated | the higher declared priority wins |
| 3 | the behaviour's unit **`id`** | ascending, so a tie goes to the `id` that sorts first |
| 4 | the command's **slot** | ascending |
| 5 | the command's **action** | ascending |

The last two exist to make the key *total* rather than merely total for the
proposals this phase produces. Two proposals from one behaviour to one entity
would otherwise tie, and `sorted` being stable would then let the order they were
evaluated in decide the winner -- which is the dependence the tie-break exists to
remove. They are reachable only by a unit that proposes twice to one entity, and
when they are reached the order between the two is arbitrary but fixed.

Consequences a reader should be able to rely on:

- **The winner is determined by the inputs alone.** Nothing in the key is an
  index, a timestamp or an insertion order, so the same two packs produce the same
  winner whichever order they were installed in.
- **The losers come back in the same ranked order**, so the records naming them
  are written in the same sequence on every replay.
- **A proposal names entities, and contends for each of them separately.** One
  behaviour may win one light group and lose another, which is why the write is
  the winner's action against the contested entity alone and not the command's
  own entity list.

The rule is not "last writer wins": that was rejected (`design.md` D6) because
the outcome would depend on the order behaviours happened to be visited in, so
the same house would behave differently after a refactor of the evaluation loop.
The engine does fix the evaluation order -- `engine/engine.py`'s `_by_id` keys
the units by ascending `id` -- and that is deliberate: it makes the *records* come
out in the same sequence on every run. It is not what decides a winner, and it
could not be, because the tie-break uses the same key.

## Where a pack's priority comes from

A pack's behaviour declares `priority` in its manifest, and the clause is
optional: `engine/behaviours/declared.py` takes the declared number when there is
one and `catalog/pack-policy.yaml`'s published `default_priority` when there is
not. The default is an artifact rather than a constant in the code, so two authors
comparing packs compare a stated number rather than one module's opinion -- and
`openhouse/facade.py` reads it once, when the manifest is turned into a unit,
rather than at evaluation time.

The resolved priority is a layered setting like any other, under
`engine/behaviours/base.py`'s `priority_key`. It is resolved at **house scope**
and not at the scope the behaviour is evaluated in, because arbitration is per
entity and an entity can be named by units evaluated in different rooms: a
priority that varied by room would leave the same pair of proposals ranked
differently depending on which room was asked.

## What a record says afterwards

Each evaluation leaves exactly one record (`engine/decision_log.py`), and a record
carries one outcome. The engine folds the fates of that evaluation's proposals by
a fixed precedence -- `acted` first, then `lost arbitration`, then `overridden`,
then `rate-limited`, then `refused: unsafe` -- so:

- a behaviour that **won** its contention records `acted`, with a `state_delta`
  naming exactly the entities that were written;
- a behaviour that **lost** records `lost arbitration`, with an empty
  `state_delta`;
- a behaviour that won one entity and lost another records `acted`, and its
  `state_delta` names only the entity it won. That is the honest report of a
  partial win: the evaluation did act.

The losers are recorded rather than dropped, because "the pack proposed and was
outranked" and "the pack never proposed" are different facts about a house, and
only the log can tell them apart.
