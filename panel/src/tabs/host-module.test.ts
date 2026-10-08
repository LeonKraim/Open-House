/**
 * Unit tests for the import screen's two rules about data.
 *
 * The screen itself is checked by looking at it -- the browser round is where
 * the journey is walked. What is pinned here is what the screen decides *from*
 * the server's answer: what a row's decision becomes as a binding, and how an
 * output's name is spelled. Both are places where a mistake is invisible in the
 * panel and wrong on the server: a binding sent for an input the person left
 * alone overrides the blueprint's own default, and an output key and the
 * `"<module>/<key>"` a consumer picks must be the same spelling or a module
 * binds to an entity nothing publishes.
 */

import { strict as assert } from "node:assert";
import { test } from "node:test";

import type {
  HostedModule,
  ModuleBinding,
  ModuleEditSeed,
  ModuleInputRow,
} from "../api/models.ts";
import {
  bindingForSetting,
  boundElsewhere,
  bySelector,
  canCastSetting,
  canFlowWatch,
  castBinding,
  castModeForSetting,
  entityIds,
  isTemplate,
  readValue,
  settingSelector,
  unsetOptions,
} from "../components/hosted-module.ts";
import {
  bindingFor,
  castAnswers,
  castCondition,
  castFlowAnswers,
  castRowNames,
  castScriptAnswers,
  castModeOf,
  castModeSelector,
  editDecision,
  fieldIndex,
  hostedOutputs,
  howSelector,
  labelFor,
  scriptIdOf,
  splitOutput,
  valueSelector,
  type How,
  type InputDecision,
} from "./host-module.ts";
/** A blueprint input, with only the fields the rule under test reads. */
function input_(selector = "text", rest: Partial<ModuleInputRow> = {}): ModuleInputRow {
  return {
    name: "lux_sensor",
    title: "Lux sensor",
    description: "",
    default: null,
    has_default: false,
    multiple: false,
    bound: false,
    value: null,
    satisfied: false,
    in_trigger: false,
    selector,
    options: [],
    ...rest,
  };
}

/** One row's decision, as the screen keeps it. */
function decision_(how: How, value: unknown, selector = "text"): InputDecision {
  return { input: input_(selector), how, value, expose: false };
}

test("an input left alone is no binding, so the blueprint's default stands", () => {
  // The difference matters: `bind_inputs` fills only what it is given, and a
  // binding sent for an unfilled input would replace the author's default with
  // a blank. "Use the blueprint's default" is therefore the absence of a
  // binding and not a binding holding nothing.
  assert.equal(bindingFor(decision_("default", undefined)), null);
});

test("a literal the person left blank is still a binding", () => {
  // An empty string is a value somebody typed on purpose -- clearing a message
  // template, say -- and it is not the same act as leaving the row alone.
  assert.deepEqual(bindingFor(decision_("literal", "")), {
    kind: "literal",
    value: "",
  });
});

test("a device is bound by its entity id", () => {
  assert.deepEqual(bindingFor(decision_("entity", "light.kitchen", "entity")), {
    kind: "entity",
    value: "light.kitchen",
  });
});

test("a device row with nothing picked is no binding", () => {
  assert.equal(bindingFor(decision_("entity", "", "entity")), null);
});

test("a cast written beside a chosen device is the whole answer", () => {
  // "I have picked what this input reads, and now I want it cast." Both cannot
  // travel: a device and a cast written into the same field would be a document
  // Home Assistant reads the device out of, silently ignoring the logic the
  // person wrote -- so the cast replaces the choice while it is there. Empty is
  // how the choice comes back, which is what makes it on-demand.
  const picked = { ...decision_("entity", "light.kitchen", "entity") };
  assert.deepEqual(bindingFor({ ...picked, cast: "{{ is_state('light.kitchen','on') }}" }), {
    kind: "literal",
    value: "{{ is_state('light.kitchen','on') }}",
  });
  assert.deepEqual(bindingFor({ ...picked, cast: "" }), {
    kind: "entity",
    value: "light.kitchen",
  });
  assert.deepEqual(bindingFor({ ...picked, cast: "   " }), {
    kind: "entity",
    value: "light.kitchen",
  });
  // A cast on its own is an answer too: a row nothing was picked in but a cast
  // written into is a row this module is built with, which is what "cast on
  // demand" has to mean. The device is optional the moment the cast is there.
  assert.equal(bindingFor(decision_("entity", "", "entity")), null);
  assert.deepEqual(bindingFor({ ...decision_("entity", "", "entity"), cast: "{{ 1 }}" }), {
    kind: "literal",
    value: "{{ 1 }}",
  });
});

test("a cast is offered on every row, and the menu names every editor", () => {
  // "I am just seeing one cast for one entity selector and not all of them."
  // The cast used to be offered only beside a picked device, a slot or an
  // output, which meant the rows where the want arises most -- a value typed in
  // that should have been worked out, an input left to the module that should
  // have been a question about the house -- were the rows with no box.
  const options = (castModeSelector(false).select as { options: { value: string }[] })
    .options.map((option) => option.value);
  assert.deepEqual(options, ["none", "condition", "nodered", "script", "template"]);
});

test("a script cast is offered everywhere, and opens on the script behind it", () => {
  // "haos script logic": a Home Assistant script that *returns* the value. Of the
  // four casts it is the one that is a sequence -- an if, a call, a value handed
  // back at the end -- which an expression cannot say and a flow says only by
  // being a second program to write. It is offered on every row, like the other
  // three, for the reason they are: the want can arrive at any of them.
  for (const trigger of [false, true]) {
    const options = (castModeSelector(trigger).select as {
      options: { value: string }[];
    }).options.map((option) => option.value);
    assert.ok(options.includes("script"));
  }
  // A setting a script answers opens on the script, exactly as one a flow
  // answers opens on the flow: what the person authored is the script, and what
  // it returns is machinery they never chose.
  assert.equal(castModeForSetting(input_("entity", { script_id: "script.lux" })), "script");
  assert.equal(castModeForSetting(input_("entity", { script_id: "" })), "none");
  // The mode the person has flipped the menu to wins over both.
  assert.equal(
    castModeForSetting(input_("entity", { script_id: "script.lux" }), "none"),
    "none",
  );
});

test("the row a trigger names keeps the template option, and says what it does", () => {
  // Home Assistant matches a trigger's `entity_id` against the entities the
  // house has and never renders it, so a template written there is text that
  // matches nothing and an automation that installs and never fires. That is
  // not a reason to take the answer away -- it is the person's screen -- so the
  // option stays and its own label carries the warning.
  const options = (castModeSelector(true).select as {
    options: { value: string; label: string }[];
  }).options;
  const template = options.find((option) => option.value === "template");
  assert.ok(template?.label.toLowerCase().includes("will not work"));
  // The condition is the one that does work here, and it is offered as it is
  // everywhere else.
  assert.ok(options.some((option) => option.value === "condition"));
  assert.equal(castModeSelector(true).select !== castModeSelector(false).select, true);
});

test("the Node-RED option is offered on every row, whatever it holds", () => {
  // A flow is code, and the half Open House promises -- the output node that
  // hands a value back to this input -- is the same whichever way the row was
  // answered. So the option is not narrowed: withholding it from a row that
  // holds a number would take the cast away from exactly the inputs most worth
  // programming.
  for (const trigger of [false, true]) {
    const options = (castModeSelector(trigger).select as {
      options: { value: string }[];
    }).options.map((option) => option.value);
    assert.ok(options.includes("nodered"));
    assert.ok(options.includes("none"));
    assert.ok(options.includes("condition"));
    assert.ok(options.includes("template"));
  }
});

test("and what Open House builds for it narrows to the rows it can start", () => {
  // The option is offered everywhere; the *trigger* is what needs an entity. A
  // row whose own answer resolves to one arrives wired end to end, and every
  // other row arrives with the output node alone and an empty left-hand side --
  // which is the case the cast was asked for, not a refusal.
  assert.equal(canFlowWatch(input_("entity")), true);
  assert.equal(canFlowWatch(input_("target")), true);
  assert.equal(canFlowWatch(input_("number")), false);
  assert.equal(canFlowWatch(input_("text")), false);
  // One entity, not a list: a `target` with `multiple` set names several, and a
  // trigger watches one.
  assert.equal(canFlowWatch(input_("target", { multiple: true })), false);
});

test("a Node-RED cast keeps the row's own binding, alone among the casts", () => {
  // What the flow *watches* is the entity the row resolves to, so the row's own
  // answer is the wire the flow is built from. What the input *reads* is the
  // entity the flow writes, and the server binds the input to that itself, over
  // the top of this. A condition, by contrast, sends no binding at all.
  const row = decision_("entity", "binary_sensor.hall_motion", "entity");
  assert.deepEqual(
    bindingFor({ ...row, castMode: "nodered" }),
    { kind: "entity", value: "binary_sensor.hall_motion" },
  );
  // The names, and not ids: the flow is pushed by the house that installs the
  // module, so the id Node-RED assigns belongs to that house.
  assert.deepEqual(castFlowAnswers([
    { ...row, castMode: "nodered" },
    { ...decision_("literal", 4, "number"), castMode: "template", cast: "{{ 1 }}" },
  ]), [row.input.name]);
});

test("a setting a flow answers opens on the flow, and offers no third cast", () => {
  const flowing = input_("entity", { bound_kind: "flow", flow_id: "abc", bound_to: "sensor.open_house_flow_m_x" });
  assert.equal(castModeForSetting(flowing), "nodered");
  // The row's control is not a menu a cast is written over -- the flow *is* the
  // answer -- so the other two casts are held back, as they are on a condition.
  assert.equal(canCastSetting(flowing, "light.kitchen"), false);
  // But the option stays: the menu is the only way back to the editor, and to
  // the choice that takes the flow off again.
  assert.equal(canFlowWatch(flowing), true);
});

test("a condition cast is not a binding, because the server binds what it makes", () => {
  // Two answers for one input would be one too many, and the one that would
  // lose is the condition: the automation would be pointed at the device the
  // row picked and the logic the person wrote would sit in the record doing
  // nothing. So a condition answers the row *instead of* a binding, and the
  // server's own `_async_build` puts the entity it makes in the input's place.
  const row = { ...decision_("entity", "binary_sensor.hall_motion", "entity") };
  assert.equal(bindingFor({ ...row, castMode: "condition", condition: [{}] }), null);
  assert.deepEqual(
    bindingFor({ ...row, castMode: "template", cast: "{{ 1 }}" }),
    { kind: "literal", value: "{{ 1 }}" },
  );
  // A row written before the mode existed kept a bare `cast` string, and that
  // string was always a template -- so it goes on meaning what it meant.
  assert.equal(castModeOf({ ...row, cast: "{{ 1 }}" }), "template");
  assert.equal(castModeOf({ ...row, cast: "  " }), "none");
  assert.equal(castModeOf({ ...row, castMode: "condition" }), "condition");
});

test("an open condition editor with nothing in it is not an answer", () => {
  // `[]` is what the editor reports before a person adds their first row, and
  // `{}` is what one untouched reports. Sending either as the answer would
  // replace the choice above with a question that asks nothing -- and the row
  // would stop being counted as one that still needs an answer, which is the
  // reading the screen has to get right.
  const row = decision_("entity", "light.kitchen", "entity");
  assert.equal(castCondition({ ...row, castMode: "condition", condition: [] }), null);
  assert.equal(castCondition({ ...row, castMode: "condition", condition: {} }), null);
  assert.equal(castCondition({ ...row, castMode: "condition" }), null);
  assert.equal(castCondition({ ...row, castMode: "template", cast: "{{ 1 }}" }), null);
  const written = [{ condition: "state", entity_id: ["input_text.home_state"], state: "sleep" }];
  assert.deepEqual(castCondition({ ...row, castMode: "condition", condition: written }), written);
  assert.deepEqual(
    castAnswers([
      { ...row, castMode: "condition", condition: written },
      { ...decision_("literal", "1"), castMode: "condition", condition: [] },
      decision_("entity", "light.hall", "entity"),
    ]),
    { lux_sensor: written },
  );
});

test("the read is told which rows hold logic, and not only which hold values", () => {
  // What a row may publish is asked of the answer it holds, so the read has to
  // know that a condition, a flow or a script is behind a row -- none of those
  // three is a binding, and the server would otherwise see an unanswered input
  // where a person has a piece of logic running.
  const written = [{ condition: "state", entity_id: ["input_text.home_state"], state: "sleep" }];
  /** One row, under its own input name, so the lists can be told apart. */
  const named = (name: string, rest: Partial<InputDecision>): InputDecision => ({
    ...decision_("literal", 1),
    input: input_("text", { name }),
    ...rest,
  });
  const rows = castRowNames([
    named("when_asleep", { castMode: "condition", condition: written }),
    named("hall_light", { castMode: "nodered" }),
    named("lux", { castMode: "script", script: "work_out_the_lux" }),
    // A script row nobody has picked a script for is not answered by one, so it
    // is not offered as something the module publishes.
    named("unpicked", { castMode: "script" }),
    // A template is a binding, and travels in `bindings` where it belongs.
    named("dim", { castMode: "template", cast: "{{ 1 }}" }),
  ]);
  assert.deepEqual(rows, {
    casts: ["when_asleep"],
    flows: ["hall_light"],
    scripts: ["lux"],
  });
});

test("a condition a person wrote travels as the value it is", () => {
  // Not as an entity and not as a slot: Home Assistant renders a template in
  // the field the automation reads, so what the server needs is the text and
  // nothing interpreted. A screen that tried to read the template would have to
  // know every entity the house has and every cast the person used, which is
  // Home Assistant's job and not the panel's.
  const condition =
    "{{ true if is_state('input_text.home_state', 'sleep') else false }}";
  assert.deepEqual(bindingFor(decision_("template", condition, "entity")), {
    kind: "literal",
    value: condition,
  });
  // A blank one is not an answer, and neither is whitespace: the module would be
  // built holding a template that renders to nothing.
  assert.equal(bindingFor(decision_("template", "")), null);
  assert.equal(bindingFor(decision_("template", "   ")), null);
  assert.equal(bindingFor(decision_("template", undefined)), null);
});

test("another module's output is bound by the module and the key", () => {
  assert.deepEqual(
    bindingFor(decision_("output", "evening_scene/gate_open")),
    { kind: "output", module: "evening_scene", key: "gate_open" },
  );
});

test("a half-chosen output is no binding, not a binding to nowhere", () => {
  // The picker only ever offers complete options, so this is the shape of a row
  // that was chosen and then had its choices taken away -- a module unhosted
  // between the read and the press. Sending it would bind a template to an
  // entity no module publishes.
  assert.equal(bindingFor(decision_("output", "/gate_open")), null);
  assert.equal(bindingFor(decision_("output", "evening_scene/")), null);
  assert.equal(bindingFor(decision_("output", "evening_scene")), null);
  assert.equal(bindingFor(decision_("output", undefined)), null);
});

test("a slot is bound by its name, and lives in the module's room", () => {
  // The one binding that names a *role* rather than a device: what entity it
  // resolves to is the server's answer, read from the room the module is
  // imported into, and it changes there without this screen being involved.
  assert.deepEqual(bindingFor(decision_("slot", "ambient_light_sensor")), {
    kind: "slot",
    slot: "ambient_light_sensor",
  });
});

test("a global slot is bound as the house's, and comes back as one", () => {
  // The scope is what tells the two apart on the wire, and it has to survive
  // the round trip: a house-scoped answer that came back as the room's would be
  // saved as the room's on the next edit, and the module would quietly start
  // watching the kitchen's sensor where it was watching the house's.
  const bound = bindingFor(decision_("global_slot", "light_group"));
  assert.deepEqual(bound, {
    kind: "slot",
    slot: "light_group",
    scope: "house",
  });
  const row = { ...input_("text"), name: "lux_sensor", title: "Lux sensor" };
  const seed = seed_({
    bindings: { lux_sensor: { kind: "slot", slot: "light_group", scope: "house" } },
  });
  const back = editDecision(row, seed);
  assert.equal(back.how, "global_slot");
  assert.equal(back.value, "light_group");
  // And the plain answer is still the plain answer.
  assert.deepEqual(bindingFor(decision_("slot", "light_group")), {
    kind: "slot",
    slot: "light_group",
  });
  assert.equal(
    editDecision(row, {
      ...seed,
      bindings: { lux_sensor: { kind: "slot", slot: "light_group" } },
    }).how,
    "slot",
  );
});

test("a global slot offer lists only the roles the house answers", () => {
  // Naming a room-only role globally would promise a resolution the engine
  // never makes for it, and the module would wait for a device it cannot be
  // given. The room's own list keeps every role, because every role can be
  // bound in a room.
  const slots = [
    { name: "light_group", label: "Light group", house_scope: true },
    { name: "fridge_contact", label: "Fridge contact", house_scope: false },
  ];
  const room = valueSelector(decision_("slot", ""), [], slots) as {
    select: { options: { value: string }[]; custom_value?: boolean };
  };
  assert.deepEqual(
    room.select.options.map((option) => option.value),
    ["light_group", "fridge_contact"],
  );
  const house = valueSelector(decision_("global_slot", ""), [], slots) as {
    select: { options: { value: string }[]; custom_value?: boolean };
  };
  assert.deepEqual(
    house.select.options.map((option) => option.value),
    ["light_group"],
  );
  // Typing a name is offered for the room's list and not for the house's: the
  // house's roles are a closed list the engine resolves, so there is nothing to
  // type that the list does not already carry.
  assert.equal(room.select.custom_value, true);
  assert.equal(house.select.custom_value, false);
});

test("a slot row with no name chosen is no binding", () => {
  // An empty name is not a promise about anything. A name the room has not
  // bound yet *is* one -- the module waits rather than being refused -- so this
  // is about the row being blank, not about the slot being unfilled.
  assert.equal(bindingFor(decision_("slot", "")), null);
  assert.equal(bindingFor(decision_("slot", undefined)), null);
});

test("a setting the person gave is theirs to edit", () => {
  assert.equal(boundElsewhere(input_("number", { bound_kind: "literal" })), null);
  assert.equal(boundElsewhere(input_("number", { bound_kind: "entity" })), null);
  // A setting left on the blueprint's own default has no binding at all, and it
  // is still a value the person may change -- the default is where it started.
  assert.equal(boundElsewhere(input_("number")), null);
});

test("a setting another module publishes is shown, not edited", () => {
  // Typing into a box holding a copy of a live reading gives a number the next
  // publish overwrites, and an edit that cut the link would say nothing.
  const note = boundElsewhere(
    input_("number", { bound_kind: "output", bound_to: "lights/scene" }),
  );
  assert.ok(note?.includes("lights/scene"), note ?? "no note");
});

test("a setting its room's slot fills is shown, not edited", () => {
  // The case every imported module now has, because every input is kept by
  // default: the lux input answered with a slot is a setting whose value is
  // whatever the room binds -- and typing an entity there would replace the
  // promise with one hard-coded device without saying so.
  const note = boundElsewhere(
    input_("number", { bound_kind: "slot", bound_to: "ambient_light_sensor" }),
  );
  assert.ok(note?.includes("ambient_light_sensor"), note ?? "no note");
  assert.ok(!note?.includes("undefined"), note ?? "no note");
});

test("a module's own name may contain the separator's twin but not the separator", () => {
  // `splitOutput` cuts at the *first* separator, so a key containing one still
  // reads as the key: the module half is a slug and cannot contain it.
  assert.deepEqual(splitOutput("evening_scene/door/open"), [
    "evening_scene",
    "door/open",
  ]);
});

/** A hosted module, with only the fields the rule under test reads. */
function hosted_(slug: string, keys: string[]): HostedModule {
  return {
    slug,
    title: slug,
    blueprint: "",
    definition: "",
    room_id: "",
    room_name: "the whole house",
    slots: [],
    automation_id: "automation.x",
    config: "Default",
    configs: ["Default"],
    derived: {},
    flows: {},
    scripts: {},
    inputs: [],
    settings: [],
    outputs: keys.map((key) => ({
      key,
      kind: "number",
      expression: "",
      entity_id: `sensor.open_house_${slug}_${key}`,
      value: null,
    })),
  };
}

test("every hosted output is offered once, spelled the way a binding reads it", () => {
  // The two halves of one contract: the picker offers these strings and
  // `splitOutput` reads them back. A spelling that drifted would offer a
  // consumer a choice that resolves to nothing.
  const options = hostedOutputs([hosted_("lights", ["scene", "lux"]), hosted_("door", ["open"])]);
  assert.deepEqual(options, ["lights/scene", "lights/lux", "door/open"]);
  for (const option of options) {
    const [module, key] = splitOutput(option);
    assert.ok(module.length > 0 && key.length > 0, `${option} must split`);
  }
});

test("a house that hosts nothing offers nothing to bind from", () => {
  assert.deepEqual(hostedOutputs([]), []);
});

test("an output nothing has published reads as unknown, not as zero", () => {
  // `null` is what the server sends for an output whose publish step has not
  // run. Showing it as "0" or "" would tell a person the module had reported,
  // which is the one thing this must never say.
  assert.equal(readValue(null), "nothing yet");
  assert.equal(readValue(undefined), "nothing yet");
  assert.equal(readValue(0), "0");
  assert.equal(readValue(""), "(blank)");
  assert.equal(readValue(true), "on");
  assert.equal(readValue(false), "off");
  assert.equal(readValue(["a", "b"]), '["a","b"]');
});

test("a form field is addressed by the index that follows its kind", () => {
  // `ha-form` fields are data paths, so a blueprint input's own name -- which
  // may hold a colon -- cannot be one. The rows are keyed `how_<i>` and
  // `value_<i>`, and a field name read the wrong way would put one input's
  // answer onto another input.
  assert.equal(fieldIndex("how_0"), 0);
  assert.equal(fieldIndex("value_12"), 12);
  assert.equal(fieldIndex("how"), -1);
  assert.equal(fieldIndex("value_x"), -1);
  assert.equal(fieldIndex(""), -1);
});

test("each field of an input's row is labelled for the question it asks", () => {
  // The input's own name is the row's title, written above the form by the
  // screen itself, in the same weight as every other heading -- a dropdown's
  // label is only a small caption, which is why the title could not be left to
  // it. What these labels answer is what each control *is*: the choice of how
  // the input is filled, the value, and the act of keeping it as a setting.
  assert.equal(labelFor("how_0"), "What fills it");
  assert.equal(labelFor("value_0"), "Value");
  assert.equal(labelFor("expose_0"), "Keep as a setting");
  // The second field is named for what it holds: "Value" over a box holding
  // logic a person wrote is the wrong word for it.
  assert.equal(labelFor("value_0", "template"), "Condition or template");
  assert.equal(labelFor("value_0", "entity"), "Value");
});

test("a select input keeps the blueprint's own menu", () => {
  // A select rendered as a text box lets a person type a token the blueprint
  // never compares against, and the blueprint then silently takes its else
  // branch forever. The options are the declaration's, so they are carried.
  const selector = bySelector(
    input_("select", { options: ["day", "night"] }),
  );
  assert.deepEqual(selector, {
    select: { mode: "dropdown", options: ["day", "night"] },
  });
});

test("an action input gets the action editor", () => {
  // A string where Home Assistant requires a list of actions is an automation
  // the validator refuses, so this cannot be a text box.
  assert.deepEqual(bySelector(input_("action")), { ui_action: {} });
});

test("a selector nothing knows is a text box rather than a missing input", () => {
  assert.deepEqual(bySelector(input_("colour_rgb")), { text: {} });
});

test("an entity input gets the entity picker", () => {
  assert.deepEqual(bySelector(input_("entity")), { entity: {} });
});

test("a device row that names several is bound with all of them", () => {
  // A blueprint asking for "the lights" rather than "this light" gets the
  // control that takes several, and the binding carries the ids: which shape
  // the value takes is the server's to decide from the input's own selector
  // (`module_host._bound_value`), so this sends the ids and no wrapping.
  const many = input_("target", { multiple: true });
  const decision = (value: unknown): InputDecision => ({
    input: many,
    how: "entity",
    value,
    expose: true,
  });
  assert.deepEqual(bindingFor(decision(["light.kitchen", "light.hall"])), {
    kind: "entity",
    value: ["light.kitchen", "light.hall"],
  });
  // One pick on an input that takes several is still a list, because a list is
  // what the control took and what the input holds. On an input that takes one
  // it is the id itself, whichever shape the control reported.
  assert.deepEqual(bindingFor(decision(["light.kitchen"])), {
    kind: "entity",
    value: ["light.kitchen"],
  });
  const one = { ...many, multiple: false };
  const single = (value: unknown): InputDecision => ({
    input: one,
    how: "entity",
    value,
    expose: true,
  });
  assert.deepEqual(bindingFor(single("light.kitchen")), {
    kind: "entity",
    value: "light.kitchen",
  });
  assert.deepEqual(bindingFor(single(["light.kitchen"])), {
    kind: "entity",
    value: "light.kitchen",
  });
  // And nothing picked is no binding at all.
  assert.equal(bindingFor(decision([])), null);
});

test("the ids in a control's answer are read out of either shape", () => {
  // A target answers with `{entity_id: [...]}` and an entity control with the
  // id itself -- as a string when the input takes one, a list when it takes
  // several. What the server wants is the ids, wrapped again for whichever
  // selector the blueprint declared.
  assert.deepEqual(entityIds("light.a"), ["light.a"]);
  assert.deepEqual(entityIds(["light.a", "light.b"]), ["light.a", "light.b"]);
  assert.deepEqual(entityIds({ entity_id: "light.a" }), ["light.a"]);
  assert.deepEqual(entityIds({ entity_id: ["light.a"] }), ["light.a"]);
  assert.deepEqual(entityIds({ entity_id: [] }), []);
  assert.deepEqual(entityIds(""), []);
  assert.deepEqual(entityIds(undefined), []);
});

test("a stored condition is shown as a condition, not as an unknown entity", () => {
  // The trap this closes: an entity input answered with a template holds a
  // string, and an entity picker handed one says "unknown entity selected" about
  // a value the automation renders perfectly well. The value decides, because it
  // is the only thing that knows.
  assert.equal(isTemplate("{{ states('sensor.lux') }}"), true);
  assert.equal(isTemplate("{% if x %}y{% endif %}"), true);
  assert.equal(isTemplate("light.kitchen"), false);
  assert.equal(isTemplate(42), false);
  assert.equal(isTemplate(null), false);
  assert.deepEqual(settingSelector(input_("entity"), "{{ states('sensor.lux') }}"), {
    template: {},
  });
  assert.deepEqual(settingSelector(input_("entity"), "light.kitchen"), { entity: {} });
  // A number input holding a template is a template too -- that is where a cast
  // or a bit of arithmetic a person wrote ends up.
  assert.deepEqual(settingSelector(input_("number"), "{{ 1 + 1 }}"), { template: {} });
});

test("a cast is offered on the card's rows, and only held back where it would be a second box", () => {
  // Every row a person can answer is a row they can cast, on the screen a module
  // is lived with as well as during the import -- a rule that held only during
  // the import would be a rule they lose the moment they leave.
  assert.equal(canCastSetting(input_("entity"), "light.kitchen"), true);
  assert.equal(canCastSetting(input_("target"), { entity_id: ["light.a"] }), true);
  assert.equal(canCastSetting(input_("number"), 42), true);
  assert.equal(canCastSetting(input_("text"), "hello"), true);
  // A row a trigger names is castable too: a condition there works -- Open House
  // works it out and points the trigger at the entity it makes. The template
  // option is what the menu's own label warns about, not a reason for no box.
  assert.equal(canCastSetting(input_("entity", { in_trigger: true }), "light.kitchen"), true);
  // The two rows that are not: a setting already holding logic, whose control *is*
  // the template box; and one a condition already sits behind, whose editor is
  // drawn instead of a menu.
  assert.equal(canCastSetting(input_("entity"), "{{ states('light.kitchen') }}"), false);
  assert.equal(
    canCastSetting(input_("entity", { bound_kind: "condition" }), "light.kitchen"),
    false,
  );
});

test("a setting a condition sits behind opens on the condition editor", () => {
  // The condition is what the person authored; the entity id the input is bound
  // to is machinery they never chose, so it is not what the row opens showing.
  assert.equal(castModeForSetting(input_("entity", { cast: { condition: "state" } })), "condition");
  assert.equal(castModeForSetting(input_("entity")), "none");
  // The server sends `null` for every setting that has no condition, and a
  // presence test would read each of those as one -- which is not a cosmetic
  // mix-up: the row is then saved as a *cast* and the value the person typed is
  // never sent, so the module keeps the number it already had.
  assert.equal(castModeForSetting(input_("number", { cast: null })), "none");
  // A menu the person has flipped this session is what the row shows.
  assert.equal(castModeForSetting(input_("entity", { cast: { condition: "state" } }), "none"), "none");
  assert.equal(castModeForSetting(input_("entity"), "template"), "template");
});

test("a cast is the whole binding, and an empty one is the choice instead", () => {
  // The card's half of the import screen's rule: what the person wrote wins over
  // the device they picked while writing it, and clearing the box hands the
  // answer back to the choice rather than leaving the module with nothing.
  assert.deepEqual(castBinding("{{ is_state('input_text.home_state', 'sleep') }}"), {
    kind: "literal",
    value: "{{ is_state('input_text.home_state', 'sleep') }}",
  });
  assert.equal(castBinding(""), null);
  assert.equal(castBinding("   "), null);
  assert.equal(castBinding(undefined), null);
});

test("a setting a device fills is saved as a device", () => {
  // Not as whatever the control happened to report: a target control's
  // `{entity_id: [...]}` sent through as a literal would put a mapping where
  // the automation's target belongs.
  const settings = [input_("target", { name: "lights", title: "Lights" })];
  assert.deepEqual(
    bindingForSetting(settings, "lights", { entity_id: ["light.a", "light.b"] }),
    { kind: "entity", value: ["light.a", "light.b"] },
  );
  assert.deepEqual(bindingForSetting(settings, "lights", { entity_id: "light.a" }), {
    kind: "entity",
    value: "light.a",
  });
  // Everything else is the value the person typed, and a setting row this card
  // was not told about is one too.
  assert.deepEqual(bindingForSetting(settings, "threshold", 42), {
    kind: "literal",
    value: 42,
  });
  assert.deepEqual(bindingForSetting(settings, "gone", "x"), {
    kind: "literal",
    value: "x",
  });
});

test("the options a module waits for are the ones nothing has set", () => {
  // Kept, given no default by the blueprint, and answered by nobody: the module
  // installs and does not run until one of these is set, which is what the card
  // has to say out loud.
  const module = {
    settings: [
      input_("text", { name: "message", has_default: false, bound: false }),
      input_("number", { name: "threshold", has_default: true, bound: false }),
      input_("target", { name: "lights", has_default: false, bound: true }),
    ],
  } as unknown as HostedModule;
  assert.deepEqual(
    unsetOptions(module).map((row) => row.name),
    ["message"],
  );
});

// -- editing a module that is already in the house ---------------------------

/**
 * A module's own document and answers, as `modules/read` hands them back.
 *
 * Empty by default so each test says only the part it is about: a seed with no
 * answers at all is a module that answered nothing, which is exactly what the
 * blueprint's own defaults are.
 */
function seed_(rest: Partial<ModuleEditSeed> = {}): ModuleEditSeed {
  return {
    module: "dim_a_light",
    definition: "dim_a_light",
    text: "alias: dim\n",
    title: "Dim the light",
    description: "",
    author: "",
    version: "1.0.0",
    licence: "no_licence",
    blueprint: "",
    bindings: {},
    settings: [],
    casts: {},
    flows: {},
    scripts: {},
    picks: [],
    installs: [{ slug: "dim_a_light_kitchen", room_id: "kitchen", room_name: "Kitchen" }],
    ...rest,
  };
}

test("a row comes back on the module's own answer, not on the reading's", () => {
  // **The reading is the trap this exists for.** The module's answers are laid
  // over its document before it is read, so every input in the reading comes
  // back *filled* -- and a screen that took that for an answer would save the
  // module as a document with every default written into it, which is a module
  // that has stopped following its own blueprint.
  const seed = seed_({
    bindings: { lux_sensor: { kind: "slot", slot: "ceiling_light" } },
  });
  const row = input_("entity", { bound: true, value: "light.kitchen", satisfied: true });
  const decision = editDecision(row, seed);
  assert.equal(decision.how, "slot");
  assert.equal(decision.value, "ceiling_light");
});

test("an input the module never answered is left to the blueprint", () => {
  // The other half of the same rule, and the one that makes an edit safe: an
  // input the seed says nothing about goes on being whatever the blueprint says,
  // so editing one row of a module does not freeze the other nine.
  const row = input_("number", { bound: true, value: 40, satisfied: true });
  const decision = editDecision(row, seed_());
  assert.equal(decision.how, "default");
  assert.equal(bindingFor(decision), null);
});

test("and a device input the module never answered opens on a slot", () => {
  // The one exception, and it is the blueprint's own rule rather than this
  // screen's: `declares_default` says an input that takes a device has no default
  // whatever the author wrote, so there is no "Blueprint default" this row could
  // be left on. It opens on the slot answer with the name still to be given, and
  // until it is named the row is unanswered -- the same state a fresh import
  // shows, rather than a menu offering a default that does not exist.
  const row = input_("entity", { bound: true, value: "light.kitchen", satisfied: true });
  const decision = editDecision(row, seed_());
  assert.equal(decision.how, "slot");
  assert.equal(decision.value, undefined);
  assert.equal(bindingFor(decision), null);
});

test("a device input is offered the two slot answers and nothing else", () => {
  // "You aren't able to say that a device selector is exposed as a selector in
  // the module's options": a device input is a device, and a module's whole point
  // is that it is not one house's device. A slot is the answer that follows the
  // room the module lands in, so that is what the row offers -- and what it does
  // not offer is a value to type, a device to pick, or a setting to keep.
  const device = (selector: string) =>
    (howSelector(input_(selector), "slot").select as {
      options: { value: string }[];
    }).options.map((option) => option.value);
  assert.deepEqual(device("entity"), ["slot", "global_slot"]);
  assert.deepEqual(device("target"), ["slot", "global_slot"]);
  // Every other kind of input keeps the whole menu: a number is a value, and
  // "leave it to the blueprint" is a real answer for one.
  const other = (howSelector(input_("number"), "default").select as {
    options: { value: string }[];
  }).options.map((option) => option.value);
  assert.deepEqual(other, [
    "default",
    "literal",
    "entity",
    "slot",
    "global_slot",
    "output",
    "template",
  ]);
});

test("a device input already holding a device still shows it", () => {
  // An edit must not silently rewrite what a module already answers. Modules
  // imported before this rule hold devices, and the row that has one shows it
  // beside the two slots so the person can keep it or move it to a slot -- which
  // is the only thing this adds back, and it never offers a *new* device answer.
  const options = (
    howSelector(input_("entity"), "entity").select as {
      options: { value: string }[];
    }
  ).options.map((option) => option.value);
  assert.deepEqual(options, ["slot", "global_slot", "entity"]);
  // A row left on the blueprint's default is not a device answer, and a device
  // input is not offered one: it opens on the slot answer instead (`editDecision`).
  const fresh = (
    howSelector(input_("entity"), "default").select as {
      options: { value: string }[];
    }
  ).options.map((option) => option.value);
  assert.deepEqual(fresh, ["slot", "global_slot"]);
});

test("a reading and a save agree on every kind of answer", () => {
  // The round trip, and the reason the seed is read rather than the reading:
  // what a row shows has to be a binding the server reads back as the same
  // thing, or opening a module and pressing Save would change it.
  const answers: Record<string, ModuleBinding> = {
    a_slot: { kind: "slot", slot: "ceiling_light" },
    a_device: { kind: "entity", value: "light.kitchen" },
    an_output: { kind: "output", module: "other", key: "min_lux" },
    a_value: { kind: "literal", value: 40 },
    a_template: { kind: "literal", value: "{{ states('sun.sun') }}" },
  };
  for (const [name, bound] of Object.entries(answers)) {
    const seed = seed_({ bindings: { [name]: bound } });
    const decision = editDecision(input_("text", { name }), seed);
    assert.deepEqual(bindingFor(decision), bound, `${name} did not round-trip`);
  }
});

test("a condition comes back on the condition editor, not as the entity it made", () => {
  // The server answers a condition by making a real entity out of it and binding
  // the input to that -- so a row seeded from the *binding* would show an entity
  // nobody picked, and saving would leave the condition behind. What the person
  // wrote is in the seed's casts, and that is what the row opens on.
  const condition = { condition: "state", entity_id: "input_boolean.sleep", state: "on" };
  const seed = seed_({
    casts: { someone_home: condition },
    bindings: {
      someone_home: { kind: "entity", value: "binary_sensor.open_house_dim_a_light_someone_home" },
    },
  });
  const decision = editDecision(input_("entity", { name: "someone_home" }), seed);
  assert.equal(decision.castMode, "condition");
  assert.deepEqual(decision.condition, condition);
  assert.equal(bindingFor(decision), null, "a condition is not also a binding");
});

test("a flow comes back on the flow editor with its own answer under it", () => {
  // A flow is the one cast that *keeps* the row's own answer: that answer is the
  // entity the flow watches, so a row that opened with only the flow on it would
  // rebuild the flow out of nothing.
  const seed = seed_({
    // The seed's flows are a map of input name to flow id, which is what lets
    // `editDecision` tell a row answered by a *flow* from one answered by a
    // script: both arrive as an editor rather than as a value, and only one of
    // the two editors exists for each.
    flows: { heat_demand: "flow_heat" },
    bindings: { heat_demand: { kind: "entity", value: "sensor.living_room_temperature" } },
  });
  const decision = editDecision(input_("entity", { name: "heat_demand" }), seed);
  assert.equal(decision.castMode, "nodered");
  assert.equal(decision.how, "entity");
  assert.equal(decision.value, "sensor.living_room_temperature");
});

test("what the module kept settable is what comes back ticked", () => {
  // A setting is a dial on the module and a binding is what it currently reads.
  // Seeding every answered row as a dial would put a control on the card for
  // every input the blueprint has; seeding none would take them all away.
  const seed = seed_({
    settings: ["min_lux"],
    bindings: { min_lux: { kind: "literal", value: 10 }, max: { kind: "literal", value: 255 } },
  });
  assert.equal(editDecision(input_("number", { name: "min_lux" }), seed).expose, true);
  assert.equal(editDecision(input_("number", { name: "max" }), seed).expose, false);
});

test("a value with a template in it comes back as a template", () => {
  // The two are the same answer -- Home Assistant renders a template wherever it
  // lands -- so this is not a guess about what the person meant, it is which of
  // the two editors can show it.
  const seed = seed_({ bindings: { message: { kind: "literal", value: "{{ now() }}" } } });
  assert.equal(editDecision(input_("text", { name: "message" }), seed).how, "template");
});

test("a script comes back on the script editor, and stops the row there", () => {
  // **A script is not a flow, and the difference is what the row holds.** A flow
  // keeps the row's own answer because that answer is the entity it watches; a
  // script is *called* and hands its answer back, so nothing is wired into it
  // and there is no binding under it to read -- which is why the decision returns
  // here rather than falling through to the bindings below.
  const seed = seed_({
    scripts: { heat_demand: "work_out_the_heat" },
    bindings: { heat_demand: { kind: "entity", value: "sensor.living_room_temperature" } },
  });
  const decision = editDecision(input_("entity", { name: "heat_demand" }), seed);
  assert.equal(decision.castMode, "script");
  assert.equal(decision.script, "work_out_the_heat");
  assert.equal(bindingFor(decision), null, "a script is not also a binding");
  // The row's own `script_id` is the other half of the answer, and the two are
  // read together because a definition may name an input a room has not picked a
  // script for: the name is the module's, the id is the house's.
  const row = editDecision(
    input_("entity", { name: "heat_demand", script_id: "work_it_out_again" }),
    seed_(),
  );
  assert.equal(row.castMode, "script");
  assert.equal(row.script, "work_it_out_again");
});

test("the inputs answered by a script are sent as a name and an id", () => {
  // **A mapping and not the list the flows are**, because nothing fills in the
  // half that is missing: a flow is pushed at save and its id is minted then, so
  // a name is the whole of it, while a script already exists in this house and a
  // name with no id behind it is a call to something nobody named -- so an empty
  // answer is left out rather than sent as a name to be resolved later.
  const decision_ = (
    name: string,
    castMode: "script" | "nodered",
    script?: string,
  ): InputDecision => ({
    input: input_("entity", { name }),
    how: "entity",
    value: undefined,
    expose: false,
    castMode,
    ...(script ? { script } : {}),
  });
  const decisions: InputDecision[] = [
    decision_("heat_demand", "script", "work_out_the_heat"),
    decision_("other", "script"),
    decision_("third", "nodered"),
  ];
  assert.deepEqual(castScriptAnswers(decisions), {
    heat_demand: "work_out_the_heat",
  });
  // The flows' half of the same answer is a list of names, and the two do not
  // overlap: a row is answered by one of them or by neither.
  assert.deepEqual(castFlowAnswers(decisions), ["third"]);
});

test("the id inside a script picker's answer is read either way", () => {
  // The picker holds `script.turn_it_on` and a `script.` call takes
  // `turn_it_on`, but the value may arrive with the domain already off or as
  // something unreadable -- and a form that threw here would lose a whole screen
  // to one field.
  assert.equal(scriptIdOf("script.turn_it_on"), "turn_it_on");
  assert.equal(scriptIdOf("turn_it_on"), "turn_it_on");
  assert.equal(scriptIdOf("  script.turn_it_on  "), "turn_it_on");
  assert.equal(scriptIdOf(undefined), "");
  assert.equal(scriptIdOf(7), "");
});

test("a cast answers a row, so the card does not call it waiting", () => {
  // **The banner this feeds is the one that says a module is not running.** A
  // condition and a script leave no binding behind -- Open House makes the entity
  // or makes the call, and neither is a value the person sent -- so a row they
  // answer would be named as a setting to go and fill in, about logic that is
  // already built and running.
  const module = hosted_("dim_a_light", []);
  module.settings = [
    input_("number", { name: "condition_row", cast: { condition: "state" } }),
    input_("entity", { name: "script_row", script_id: "work_it_out" }),
    input_("number", { name: "flow_row", flow_id: "flow_1", bound: true }),
    input_("number", { name: "really_waiting" }),
  ];
  assert.deepEqual(
    unsetOptions(module).map((setting) => setting.name),
    ["really_waiting"],
  );
});

