# The pack sandbox

A pack is a description of intent the engine carries out, never a program the
engine runs. `engine/sandbox.py` is where that claim is enforced, and this page
states the four rules it enforces and the sixteen reasons a refusal can carry.
Every reason named below is one a test produces, and every repository path
spelled in backticks is asserted to exist by `tests/test_reference_docs.py`.

The page exists because the sandbox is the one part of the pack system a person
can be *surprised* by. A pack author who wrote `repeat` and is told the term is
unknown will look for a spelling mistake; one who is told the term is published
and forbidden will go and read the spec. That difference is a distinction of its
own, not a wording, and it is the reason this list is documented rather than left
in the source.

## The four rules

### 1. The declarative subset

A behaviour's trigger, condition and action are terms published by
`schemas/behavior-vocabulary/1.1.0.json`, and none of them is a term the
declarative subset forbids. The subset is `declarative_subset` in
`catalog/pack-policy.yaml` and it names the terms a pack **may not** use — an
allowed-list was rejected, because it would silently admit whatever term a future
vocabulary version adds.

A pack states what it wants. It does not branch, loop, order, or assign, and it
does not evaluate an expression over the house. A condition that reads state is
declarative; a condition that computes is not, which is why a `template`
condition is refused for the same reason `variables` is.

### 2. Entity reach

A behaviour reaches an entity only through a slot the pack declares and its own
`slots` clause names, in the room the pack is installed in. A literal entity id,
device id or room name is not a slot, and a pack that names one is a pack that
only works in the house it was written in and can reach past its grant.

Reach is bound to what the house actually supplied. A slot the room binds nowhere
contributes nothing, so a behaviour reaching only through it is *inert*: the pack
installs, because the slot was optional, and nothing happens, because there is no
entity to act on. An inert behaviour is not a refusal — there is nothing to
refuse.

### 3. Declared services

The services a manifest's behaviours name **are** the pack's permission set, and
it is computed from those clauses rather than written a second time. A service
added to a behaviour therefore widens the set with nothing else edited, which is
the property a hand-maintained second list would lose.

A call outside the set is refused. A call inside it is judged by two further
published lists, and they are deliberately different:

- a service on `banned_services` refuses the pack, because it acts on the system
  that *hosts* the pack rather than on a device in the house;
- a service on `flagged_services` is dangerous and legitimate, so the pack
  installs and the flag is surfaced by the install result.

A flag never becomes a refusal, and the two lists are read by two branches, so
they cannot be collapsed into each other by a code path that treats them alike.
Locking a door on a schedule is an ordinary pack; unlocking is the one that needs
saying out loud, which is why the pair is judged by what a pack can *do* and not
by the domain it touches.

### 4. `provides` containment and class

Every `provides` entry names a path the schema calls repo-relative, and that path
must resolve to a file inside the pack's own directory — the directory its
manifest lies in. Resolving against the repository root is only sound if the
answer is still inside the pack, which is what the containment test makes true.

The file must also be of the class the entry declares, drawn from the schema's
closed class enum. The class is what stops a pack conferring an artifact nobody
can pin, so the class is checked against the file's own shape rather than taken
on the entry's word.

## The refusal reasons

Every refusal carries exactly one reason, and it is one of the sixteen below.
This is the finest distinction the sandbox draws, and it is deliberately finer
than the *class* a `pack-cli` report carries: that list — `schema`, `unknown
term`, `non-declarative term`, `licence`, `derivation` and the rest — is
published by the face, and its `unknown term` covers the three `unknown_*`
reasons here. The grouping belongs to the report a reader meets it in, which is
the CLI's; a second, coarser list living in the sandbox would be a taxonomy whose
members nothing in the engine enumerates.

| Reason | What it means |
| --- | --- |
| `unknown_trigger` | the vocabulary does not publish the term on the trigger axis, so no version of it resolves the term |
| `unknown_condition` | the same, on the condition axis |
| `unknown_action` | the same, on the action axis |
| `forbidden_trigger` | the vocabulary publishes the term and the declarative subset forbids it |
| `forbidden_condition` | the same, on the condition axis — `template` is the one term here |
| `forbidden_action` | the same, on the action axis — `if`, `choose`, `repeat` and `wait_template` are terms here |
| `unknown_behaviour` | a command names a behaviour the pack does not declare |
| `literal_reference` | a `domain.object_id` stands where a slot name belongs |
| `slot_not_declared` | a behaviour reaches through a slot the pack neither requires nor takes as optional |
| `slot_not_claimed` | a behaviour reaches through a slot the pack declared and this behaviour did not name |
| `not_in_reach` | a command names an entity the behaviour's own slots do not put in its reach |
| `undeclared_service` | a behaviour calls a service its own `services` clause omits |
| `banned_service` | a behaviour declares or calls a service `catalog/pack-policy.yaml` bans |
| `dangling_path` | a `provides` entry names a path that resolves to no file |
| `escaping_path` | a `provides` entry names a path outside the pack's own directory |
| `class_mismatch` | a `provides` entry declares a class the file is not |

The three `unknown_*` reasons are separate from the three `forbidden_*` ones so
that an author who wrote a term the project knows and refuses is not told the
project has never heard of it — which is what a single "invalid term" message
would tell them. A `pack-cli` report folds the six back into the two classes
`unknown term` and `non-declarative term`, which is what a class is for: a
summary a caller branches on, published by the face that reports it.

Two of the pairs are worth stating, because a reader will otherwise assume they
are one failure:

- `literal_reference` is a `domain.object_id` where a slot belongs. A slot name
  can never contain a dot, so the shape decides it and no list of domains is
  needed.
- `slot_not_claimed` is a slot the pack **does** declare that the behaviour did
  not name in its own `slots` clause. It is the reach failure and not a service
  failure: the slot was granted and this behaviour did not claim it, so its
  message talks about nothing else.

## Where the vocabularies come from

The banned list, the flags, the subset and the published terms are all read
through `engine/vocabulary.py`, the one module in the engine that opens a frozen
artifact, so the sandbox holds no second definition of anything the project
publishes. Banning a service, flagging one, or forbidding a term is an edit to
`catalog/pack-policy.yaml` and not a release of the engine.

An artifact that is missing is not a pack failure. It raises and names the path,
because a missing vocabulary is the checkout's fault and not the pack's, and
reporting it against a pack would send the author to fix a file that is fine.

## What the sandbox does not do

It does not check a binding's domain against the slot it is bound to. A
`light_group` slot bound to a `cover` is a mis-binding this passes; the sandbox's
question is narrower, which is whether a command names an entity the pack's own
slots put in its reach at all.

It does not guard at runtime. Every rule above is decided at validation, so a pack
that validates and installs cannot exceed its grant *because the interpreter has
no facility to express the excess* — a command is a service, a slot and the
entities it names, and there is no field through which a pack could express a
branch, a loop, a variable or an expression. A runtime guard would imply the
interpreter can express the thing being guarded, and a bug in the guard would be
a security bug rather than a validation bug.

And it does not run in the composition root. `engine/sandbox.py` holds no state
and reads no clock, and it imports nothing from `sim/` or `openhouse/`, so it
runs unchanged against a real Home Assistant adapter in Phase 4.
