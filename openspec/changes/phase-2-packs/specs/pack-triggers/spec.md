# Spec Delta — pack-triggers

## Purpose

The two ways in `spec.txt:59` names — "triggers from a dashboard button or a
physical button" — and the rule that makes a trigger a way *in* rather than a
second way *through*. A pack declares behaviours; a trigger decides when one is
evaluated. The danger is the obvious one: if a button could carry its own action,
a pack would have a path around the sandbox, because the button would be a place
to put a command the manifest never declared. So a trigger names a behaviour the
pack already declares, and the act that follows is that behaviour's, evaluated by
the engine the same way any other tick evaluates it.

The two buttons turn out to resolve differently, and the vocabulary decides why.
`behavior-vocabulary/1.1.0.json` publishes fifteen trigger kinds, described in its
own words as "trigger block kinds the extraction observed across the four
estates" — `calendar`, `conversation`, `device`, `event`, `homeassistant`,
`mqtt`, `numeric_state`, `state`, `sun`, `tag`, `template`, `time`,
`time_pattern`, `webhook`, `zone`. **No term among them is a button.** A physical
button is an ordinary Home Assistant device: a Zigbee or Bluetooth remote's press
arrives as a `device` trigger or an `event`, both published, so a physical button
needs no new term and this phase adds none. A dashboard button is not an HA
trigger kind at all — it is a person acting on this project's control surface,
which is Phase 1's `user_action` — so it resolves to an existing operation rather
than to a vocabulary term, and inventing a `button` trigger would put a term in a
vocabulary whose own description says the terms are what the four estates were
observed to use.

One consequence is fixed here and is the only arbitration decision this
capability makes. A press is a **trigger**, so the proposals that follow are the
*named behaviour's*, ranked by the priority that behaviour's manifest declares.
A press is not promoted to a user action. Phase 1's total order puts a user action
above every behaviour, and promoting a press would let a pack's button outrank the
house's own rules while the pack's declared priority became decorative — the
opposite of what a declared priority is for.

## ADDED Requirements

### Requirement: A pack's behaviour may be triggered by a dashboard button or a physical button, and both produce the same act

A pack SHALL be able to bind one of its declared behaviours to a dashboard button
and to a physical button, and a press of either SHALL evaluate that behaviour and
produce the same commands through the same engine path. Neither button SHALL
produce a command the behaviour would not have produced on any other trigger.

#### Scenario: The two buttons produce the same commands

- **WHEN** the Bedtime behaviour is triggered once by a dashboard button and once
  by a physical button in the same house state
- **THEN** the two ticks produce the same commands, and the decision logs differ
  only in the record of what triggered them

#### Scenario: A press of an unknown behaviour is refused

- **WHEN** a trigger names a behaviour the pack does not declare
- **THEN** validation fails, naming the pack, the trigger and the behaviour

### Requirement: A dashboard button resolves to the control surface's `user_action` and adds no vocabulary term

A dashboard button SHALL be expressed as the control surface's existing
`user_action` operation naming the pack's behaviour, and this phase SHALL NOT add
a button term to `behavior-vocabulary/1.1.0.json`, whose own description fixes its
terms as the trigger block kinds the extraction observed. The button SHALL be
reachable the way `user_action` is reachable — from the CLI, the library and the
MCP server — with no dashboard, panel or browser required, because the panel is
Phase 5's and this phase ships a trigger and not a UI.

#### Scenario: A dashboard press needs no UI

- **WHEN** a dashboard button is pressed with no panel running
- **THEN** the press is delivered as a `user_action` naming the behaviour, and the
  behaviour is evaluated

#### Scenario: The vocabulary is unchanged

- **WHEN** the trigger vocabulary is compared before and after this phase
- **THEN** it is identical, because a dashboard button is the control surface's
  act and not a Home Assistant trigger kind

#### Scenario: The press is reachable on every face

- **WHEN** a dashboard button is pressed through the CLI, the library and the MCP
  server
- **THEN** each reaches the same behaviour, matching the control-surface rule that
  an operation is not available on one face only

### Requirement: A physical button resolves to a published trigger kind

A physical button SHALL be expressed with a trigger kind the vocabulary already
publishes — `device` for a device automation's press and `event` for a button's
event — and SHALL require no new term. The pack SHALL NOT name the physical
button's device id, because that would be the literal reference the sandbox
refuses; it SHALL reach the button through a slot the behaviour names in its own
`slots` clause, and the press SHALL arrive as that slot's state or event — so a
pack that binds two slots cannot leave the button's identity to whichever one the
engine happened to visit.

#### Scenario: A press arrives as a device event

- **WHEN** the physical button reports a press
- **THEN** the pack's trigger matches the published `device` or `event` kind, and
  the behaviour is evaluated

#### Scenario: A physical button is reached through a named slot

- **WHEN** a pack declares a physical-button trigger
- **THEN** the button is reached through a slot its behaviour names in `slots`,
  and a pack naming a device id literally fails validation under the sandbox's
  rule

#### Scenario: No term is invented for a button

- **WHEN** the trigger kinds this phase uses are compared with the published
  vocabulary
- **THEN** every one is published, and no button or press term has been added

### Requirement: A trigger is a way in and not a second execution path

A trigger SHALL name a behaviour and SHALL NOT carry an action, an entity, a
service or a condition of its own. The commands a press produces SHALL be the
named behaviour's, derived from its manifest clause, and SHALL pass the sandbox
unchanged — so a trigger cannot declare a service the manifest did not declare,
nor reach an entity the pack's slots do not bind. There SHALL be no code path that
executes a trigger's own content.

#### Scenario: A trigger cannot carry a command

- **WHEN** a trigger is declared with an action of its own
- **THEN** validation fails, naming the trigger, because a behaviour is the only
  place a pack's actions live

#### Scenario: The sandbox applies to a triggered act

- **WHEN** a trigger fires a behaviour whose clause would call an undeclared
  service
- **THEN** the failure is the same validation failure any other trigger would
  produce, and the trigger is not a route around it

#### Scenario: A button named for a disabled behaviour does nothing

- **WHEN** a trigger's behaviour is disabled and the button is pressed
- **THEN** no command is produced, because installation is not activation and a
  trigger is not an enabling act

### Requirement: A press produces the named behaviour's proposals, ranked by its declared priority

A press SHALL enter arbitration as the named behaviour's proposals, ranked by the
priority the manifest declares, and SHALL NOT be promoted to a user action.
Pressing a button SHALL therefore not outrank the house's other behaviours by
virtue of being a press; the pack states the priority it wants, and that state is
what arbitration reads.

#### Scenario: A press competes as its behaviour

- **WHEN** a press and another behaviour both propose for one entity in one tick
- **THEN** the winner is decided by the declared priorities, as any two
  behaviours would be

#### Scenario: A press does not silently outrank

- **WHEN** a press's behaviour has a lower priority than another behaviour
- **THEN** the other behaviour wins, because a press is a trigger and not a user
  action

#### Scenario: The user's own command still outranks a press

- **WHEN** the user commands an entity directly in the same tick as a press
- **THEN** the user's command wins, and the press's behaviour is recorded as a
  loser — a press does not consume the user's precedence, and the user's direct
  act keeps the rank Phase 1 gave it

### Requirement: The buttons work with no Home Assistant installed

The triggers SHALL be exercised on the fake house, and a physical button's press
SHALL be injectable there, so the whole of this capability is testable without HA.
A pack using these triggers SHALL behave identically when the adapter is Phase 4's
real one, because the trigger is resolved against the adapter's interface and not
against a fake-only facility.

#### Scenario: A press is injectable with no HA

- **WHEN** a physical button's press is injected into a fake house and no HA is
  installed
- **THEN** the pack's behaviour is evaluated, and the scenario passes

#### Scenario: The trigger names no fake-only facility

- **WHEN** the trigger's resolution is checked against the adapter interface
- **THEN** it uses only what the interface offers, so a real adapter in Phase 4
  supplies the same press rather than a different one
