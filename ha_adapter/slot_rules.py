"""A slot's rule: the logic a person puts on a slot row instead of a device.

The requirement, in the user's words: make "Set it to" work on slots, not only on
module inputs. A module's input row can already be answered with a cast -- a
template, a condition, a flow, a script -- and the slot row could not: a slot's
device was a thing a person picked out of the house, and nothing else.

This is what "a slot holds logic" means, and it is one sentence: **the logic
decides which device the slot is pointed at.** Not what the module does with it,
not whether the module runs -- *which entity*, for this module, in place of the
one the person would have chosen. Everything else follows from that:

* A **template** renders to an entity id, and that is the device.
* A **script** is called, returns an entity id, and that is the device.
* A **flow** writes an entity of its own (`sensor.open_house_flow_*`), and the id
  that entity holds is the device -- the flow is the one kind that was already
  running before anybody asked it to decide anything.
* A **condition** cannot name a device -- Home Assistant's conditions answer yes
  or no and never an entity -- so it decides *whether*: the device it gates is
  used while the condition holds, and the slot falls back to the room's own
  binding while it does not. That is a real and useful rule ("the office socket,
  after dark"), and it is the one of the four whose sentence a person has to read
  rather than infer.

**Which is why a condition carries a device of its own** (`SlotRule.device`) and
the other three do not. The device a slot row shows above its controls is
`slot.<slot>.entity`, and that one key is *both* "what a person picked" and "what
is in force" -- a producing rule writes it and thereby displaces the pick, which
is why picking a rule of one of those three forgets it. A condition has to move
between the thing it gates and the room's binding, which is a distinction one key
cannot hold: forgetting the key to fall back to the room would forget the pick
itself. So the gated device is recorded *inside the rule* and the key above it is
the watcher's to write -- the pick is what the rule is about, not a second
answer standing beside it.

**Why this is a watcher and not a build.** Every other piece of logic in the
product is worked out *inside a run*: a module's automation evaluates its casts
whenever the automation fires, which is what makes attaching one cost nothing. A
slot is not a run. It is a standing fact about a room, read at build time and at
every tick, and a rule on it has to be re-decided when the world moves -- so this
is the one place with a listener of its own (`custom_components.open_house
.slot_rules`), and the reason this module is pure and that one is not.

Nothing here talks to Home Assistant. A rule goes in, the facts to record come
out, and the sentence a person reads comes out beside them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from engine.behaviours.declared import (
    SLOT_RULE_DEVICE,
    SLOT_RULE_KIND,
    SLOT_RULE_VALUE,
    SLOT_RULE_WHEN,
    slot_rule_key,
)

from . import cast_document
from .cast_document import VALUE, Detached
from .cast_document import watched_by as _condition_entities
from .pack_authoring import AuthoringError

__all__ = [
    "KINDS",
    "RESPONSE",
    "SlotRule",
    "detached",
    "device_of",
    "facts_of",
    "keys_of",
    "picks_the_device",
    "recorded_rule",
    "rule_from",
    "summary",
    "watched_by",
]

#: The four rules a slot row may be answered with, in the spelling the panel and
#: the recorded settings use. The same four a module input row offers, and the
#: same four `cast_document.CASTS` names -- a person who has put a template on an
#: input and then on a slot has done one thing twice, and a second vocabulary for
#: it would make the two screens disagree about what they offered.
KINDS = ("condition", "template", "flow", "script")

#: The key a script rule's answer comes back under, and so the name its `stop:`
#: has to hand it back with.
#:
#: The same spelling a detached cast's value takes (`cast_document.VALUE`), and
#: deliberately the same string rather than a second constant that happens to
#: agree: a script is a script, and one that answers a slot and one that answers
#: a row should not have to know which screen called it. `cast_document` owns the
#: name and this reads it there.
#:
#: A script answers with `stop: response_variable:`, which is the only way a
#: script hands a value back to its caller, and the mapping that comes back is
#: what this key is looked up in.
RESPONSE = VALUE


@dataclass(frozen=True)
class SlotRule:
    """One slot's logic: which kind it is, what it says, and what watches it.

    `value` is the payload and its *shape follows the kind* -- a template's text,
    a condition's builder config, a flow's entity id, a script's id. One field for
    four shapes rather than four fields, because only one of them is ever set and
    a reader already has `kind` in hand; four optional fields would make every
    reader ask which one it should look at, and none of them would be any the
    truer for it.

    `when` is only ever a script's. Nothing else needs it: Home Assistant's own
    tracker watches what a template reads, a flow writes the entity it is watched
    by, and a condition names the entities it decides about inside itself. A
    script runs when somebody calls it, and *who calls it* is a person's answer.

    `device` is only ever a condition's, and the module docstring says why: a
    condition gates a device rather than producing one, so the device it gates has
    nowhere else to live.
    """

    kind: str
    value: Any
    when: tuple[str, ...] = field(default=())
    device: str = ""


def rule_from(
    kind: object, value: object = None, when: object = (), device: object = None
) -> SlotRule | None:
    """The rule three recorded settings spell, or `None` when there is none.

    **An empty kind is no rule**, which is the whole of the clearing story: the
    panel clears a rule by writing an empty kind, exactly as it clears a device
    override by writing nothing, and a person who has opened the menu and not
    chosen anything has decided nothing. A kind that *is* set and a payload that
    is not is a different case and is refused -- a rule that says "a script" and
    names no script is a row that would sit there looking answered and never fire,
    and the alternative to refusing it is a silence nobody can find.

    `when` is read for a script and dropped for the other three rather than
    refused: a list of entities is a harmless thing to have been left beside a
    template, and a person who switched a row from a script to a template should
    not be told their entity list is wrong. `device` is the other way round --
    read for a condition, and a condition without one is refused, because a
    condition that gates nothing is a rule with no slot in it.
    """
    if not isinstance(kind, str) or not kind.strip():
        return None
    name = kind.strip()
    if name not in KINDS:
        raise AuthoringError(
            f"{name!r} is not a kind of slot rule: a slot is answered with a "
            "condition, a template, a flow or a script, and there is nothing "
            "else to answer it with"
        )
    if name == "condition":
        if value is None or value == {} or value == []:
            raise AuthoringError(
                "the condition rule is empty: a condition nobody has written "
                "decides nothing, so the slot would never move"
            )
        gated = device.strip() if isinstance(device, str) else ""
        if not gated:
            raise AuthoringError(
                "the condition rule gates no device: a condition decides whether "
                "the slot's device is used, so it has to be told which device "
                "that is"
            )
        return SlotRule(kind=name, value=value, device=gated)
    if name == "template":
        text = value.strip() if isinstance(value, str) else ""
        if not text:
            raise AuthoringError(
                "the template rule is empty: there is nothing written for the "
                "slot to be decided by"
            )
        return SlotRule(kind=name, value=text)
    if name == "flow":
        entity = value.strip() if isinstance(value, str) else ""
        if not entity:
            raise AuthoringError(
                "this slot's flow has no entity yet: the flow is pushed when the "
                "module is saved, so save it first and set the rule after"
            )
        return SlotRule(kind=name, value=entity)
    call = _script_id(value)
    watched = _entities(when)
    if not watched:
        raise AuthoringError(
            "a script rule needs to know when to run: a script runs when "
            "something calls it, so pick what should start it -- the entities "
            "whose change should move this slot"
        )
    return SlotRule(kind=name, value=call, when=watched)


def recorded_rule(
    kind: object, value: object = None, when: object = (), device: object = None
) -> SlotRule | None:
    """The rule some recorded settings spell, read without refusing anything.

    **Reading a state file is not writing one.** `rule_from` is the *writer's*
    reader and refuses a half-rule loudly, because the writer is a screen and a
    screen that sent "a script" with no script is a mistake worth naming. This is
    the *reporting* reader -- what a card draws under a device -- and the state
    file it reads was written by an older version, or by hand, or by a run that
    died between two of its writes. Refusing to say anything about such a rule
    would leave a row that visibly does nothing and a person with nothing to read;
    saying "decided by a script" about it is true, and the row's own controls are
    right there to fix it.

    So the only thing it treats as no rule is the absence of a kind, which is the
    same thing `rule_from` treats that way. Everything else is reported as the
    kind it claims to be -- including a condition whose gated device is missing,
    which is a rule written before there was one or a file edited by hand, and is
    still a condition.
    """
    if not isinstance(kind, str) or not kind.strip():
        return None
    name = kind.strip()
    gated = device.strip() if isinstance(device, str) else ""
    if name == "script":
        return SlotRule(kind=name, value=value, when=_entities(when))
    if name == "condition":
        return SlotRule(kind=name, value=value, device=gated)
    return SlotRule(kind=name, value=value)


def facts_of(rule: SlotRule) -> Mapping[str, object]:
    """The settings a rule is recorded as, keyed by *fact* and not by slot.

    The unprefixed half, the same shape `slot_entity_key` and `slot_label_key`
    take: the caller wraps each with `option_key(pack, slot_rule_key(slot, fact))`
    so the keys land in the module's own namespace in the room whose module it is.
    A rule is a module's rule because a slot's *device* is per module -- the whole
    of `live_modules.set_slot` -- and a rule that decided the room's binding would
    be deciding for every module in the room at once, which is a different feature
    and not this one.

    A kind writes the facts it needs and no others, so nothing is left behind that
    a later kind would have to remember to ignore: a script adds `when`, a
    condition adds `device`, and a template writes the same two facts as always.
    The caller forgets the ones a rule does not name (see `keys_of`), which is
    what makes switching a row from a script to a template leave no `when` behind.
    """
    facts: dict[str, object] = {
        SLOT_RULE_KIND: rule.kind,
        SLOT_RULE_VALUE: rule.value,
    }
    if rule.kind == "condition":
        facts[SLOT_RULE_DEVICE] = rule.device
    if rule.when:
        facts[SLOT_RULE_WHEN] = list(rule.when)
    return facts


def device_of(rule: SlotRule) -> str:
    """The device a condition rule gates, and the empty string for every other kind.

    A reader rather than a field test at each site, because the field is empty for
    three kinds *by construction* and a caller that has to know which kind it is
    holding before it may read a device is a caller that will one day read it on
    the wrong kind.
    """
    return rule.device if rule.kind == "condition" else ""


def keys_of(slot: str) -> tuple[str, ...]:
    """Every key a rule about `slot` may be recorded under, in reading order.

    All of them rather than the ones the rule happens to use, because the caller
    that *clears* a rule has no rule to ask: forgetting is forgetting everything
    the last one wrote, including a `when` list or a gated device it left behind.
    """
    return tuple(
        slot_rule_key(slot, fact)
        for fact in (SLOT_RULE_KIND, SLOT_RULE_VALUE, SLOT_RULE_WHEN, SLOT_RULE_DEVICE)
    )


def picks_the_device(rule: SlotRule) -> bool:
    """Whether this rule says *which* device, rather than *whether* to use one.

    True for three of the four and false for a condition, and it is the row's
    question: "is the device above decided by the rule, or does the rule leave it
    alone?" -- a template, a flow and a script all *produce* an entity id and so
    decide it, and a condition produces a yes or a no and so does not.

    What it is no longer about is what happens to the device a person picked. A
    rule of any kind takes that key away from the person (`live_modules
    .set_slot_rule` forgets it either way), because a producing rule overwrites it
    and a condition's watcher has to be able to empty it; where a condition's
    device is kept is in the rule itself (`device_of`).
    """
    return rule.kind != "condition"


def watched_by(rule: SlotRule) -> tuple[str, ...]:
    """The entities something has to listen for, for this rule to re-decide.

    Empty for a template, and that is not a gap: a template is watched by what it
    reads, and Home Assistant's own tracker works that out from the text -- a
    reader here would be re-implementing a template parser to answer a question
    the platform already answers better. A condition names its entities inside
    itself (`cast_document.watched_by` walks for them), a flow is watched by the
    entity it writes, and a script by the list the person gave.
    """
    if rule.kind == "condition":
        return _condition_entities(rule.value)
    if rule.kind == "flow":
        return (str(rule.value),)
    if rule.kind == "script":
        return rule.when
    return ()


def detached(
    rule: SlotRule,
    *,
    slot: str,
    title: str,
    trigger: Sequence[str] = (),
) -> Detached:
    """This rule as a module of its own, for a detach to host.

    **A rule and a cast are one thing seen from two places**, which is why the
    document is built by `cast_document` and this is only the mapping: the four
    kinds are spelled the same on a slot row as on an input row (`KINDS` says so),
    so the thing that turns one into an automation is already written and this
    exists so that it is written once. `cast_document.detached_document` refuses a
    half-written cast in its own words; those refusals come back here as
    `AuthoringError` and are the ones a person reads.

    **What the slot adds is the answer's *name*.** An input's cast is keyed by the
    input's name (`cast_document.key_for`), and so is this: the output a detached
    slot rule publishes is named after the slot, so a person reading the new
    module sees `light_group` and not `value`. And what it adds to the *watched*
    list is the rule's own: a script's `when` is the entities it is called on,
    which is a fact about the rule rather than anything the person is asked for
    again at detach time.

    The `trigger` a person gives is added to the rule's rather than replacing it,
    the same reading `detached_document` makes -- a flow's entity and a script's
    entities are already watched, and anything named beside them is watched as
    well.
    """
    return cast_document.detached_document(
        title=title,
        input_name=slot,
        cast=rule.kind,
        template=str(rule.value) if rule.kind == "template" else "",
        condition=rule.value if rule.kind == "condition" else None,
        script=str(rule.value) if rule.kind == "script" else "",
        flow_entity=str(rule.value) if rule.kind == "flow" else "",
        trigger=(*rule.when, *trigger),
    )


def summary(rule: SlotRule) -> str:
    """One sentence under the device line: what is deciding this slot.

    A sentence rather than a symbol, because three of the four say something a
    person cannot infer from the word: a condition decides *whether* (so the
    device above is still used, sometimes), and a script waits for something (so
    the entities are the interesting part). Written here rather than in the panel
    so the card, the room's slot table and anything else that reports a rule say
    the same thing about it.
    """
    if rule.kind == "condition":
        if not rule.device:
            return "decided by a condition: the device above while it holds"
        return f"decided by a condition: {rule.device} while it holds"
    if rule.kind == "script":
        return f"decided by a script, when {_list(rule.when)} changes"
    return f"decided by a {rule.kind}"


def _script_id(value: object) -> str:
    """The id a `script.` call takes, from however the panel handed it over."""
    text = value.strip() if isinstance(value, str) else ""
    call = text[7:] if text.startswith("script.") else text
    if not call:
        raise AuthoringError(
            "this slot's script rule names no script: pick the script the slot "
            "is to be decided by, then save it"
        )
    return call


def _entities(when: object) -> tuple[str, ...]:
    """The entity ids a recorded `when` holds, deduplicated and in order."""
    if not isinstance(when, (list, tuple)):
        return ()
    found: list[str] = []
    for item in when:
        if isinstance(item, str) and item and item not in found:
            found.append(item)
    return tuple(found)


def _list(entities: Sequence[str]) -> str:
    """A list of entity ids as prose, with the word "any of" when it is a set."""
    if len(entities) == 1:
        return entities[0]
    return f"any of {', '.join(entities)}"
