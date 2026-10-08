/**
 * The cast: the logic a row can be answered with instead of a value.
 *
 * A `ha-form` row asks one question -- what fills this input -- and a cast is the
 * second answer to it: not a value but the thing that works the value out. Which
 * is why the vocabulary lives here rather than inside the card that first needed
 * it. Three screens reach for the same menu and the same rules about it, and a
 * copy in each is three answers to "what does an empty template mean" that agree
 * only until one of them is edited.
 *
 * What is *not* here: the editors themselves. The condition editor and the
 * template box are Home Assistant's own (`ha-form` draws them from the selector
 * these return), and the Node-RED and script editors are embeds of those
 * applications (`node-red-editor.ts`, `script-editor.ts`). This module decides
 * which one a row shows, and what a row holds while it shows it.
 *
 * The four casts and the one thing they do not share:
 *
 *   * **template** -- an expression, rendered wherever the input lands. A value,
 *     so it travels as a literal binding.
 *   * **condition** -- a builder rather than an expression. It is *not* written
 *     into the module: Open House makes an entity out of it and binds the input to
 *     that, because a trigger's `entity_id` is matched against real entities and
 *     never rendered, so a condition written there would match nothing.
 *   * **nodered** -- a flow, built in Node-RED, which writes the value the input
 *     reads. The row's own answer stays, because it is what the flow watches.
 *   * **script** -- a Home Assistant script, built in Home Assistant's own editor,
 *     which *returns* the value. A script is the one of the four that returns
 *     rather than holds: `stop:` with `response_variable` is how Home Assistant
 *     hands a value back to whoever called, so this is the cast to reach for when
 *     the computation is a sequence of its own rather than an expression.
 */

import type { ModuleBinding, ModuleInputRow } from "../api/models.ts";

/**
 * Which of the cast editors a row is showing, or none at all.
 *
 * `none` is not the same as an empty cast: a row with no cast uses the choice
 * above it, and a row showing the template box with nothing typed in it does
 * too -- which is why the mode is asked for separately rather than inferred from
 * emptiness. A person who has opened the condition editor and not built anything
 * yet has not answered, and the screen has to tell that from a row they never
 * touched.
 */
export type CastMode = "none" | "template" | "condition" | "nodered" | "script";

/**
 * A condition as an answer, or `null` for nothing written.
 *
 * "Empty" is asked of the *value* and not of its length. Home Assistant's
 * condition editor reports an untouched answer as `{}` and one opened and
 * abandoned as `[]`, and either is a person who has built nothing -- so neither
 * is an answer, and sending one would replace the choice above with a question
 * that asks nothing.
 */
export function writtenCondition(condition: unknown): unknown | null {
  if (condition === undefined || condition === null) return null;
  if (Array.isArray(condition)) return condition.length > 0 ? condition : null;
  if (typeof condition === "object") {
    return Object.keys(condition).length > 0 ? condition : null;
  }
  return null;
}

/**
 * Whether a stored value is logic rather than a value.
 *
 * Home Assistant's own reading: a field holding `{{ ... }}` or `{% ... %}` is a
 * template and is rendered before the automation uses it. The distinction has to
 * be made here because the two are *shown* differently -- a template in an entity
 * picker reads as an unknown entity, which is the one thing a screen must not
 * say about a value that works.
 */
export function isTemplate(value: unknown): boolean {
  return typeof value === "string" && /{{|{%/.test(value);
}

/**
 * The menu a row's cast offers, which is not the same menu on every row.
 *
 * All four editors are offered everywhere -- a cast is something a person may
 * reach for on any row, and a row that offers none is a row they have to leave
 * the screen to answer. The one difference is the row a **trigger names**: Home
 * Assistant matches a trigger's `entity_id` against the entities a house has and
 * never renders it, so a template written there is text that matches nothing and
 * an automation that installs and never fires. The option stays -- it is not
 * this screen's business to take an answer away -- and its own label says
 * plainly what would happen.
 *
 * A **condition**, on the other hand, is *better* there than anywhere else: Open
 * House evaluates it itself and points the trigger at the entity it makes.
 */
export function castModeSelector(inTrigger: boolean): Record<string, unknown> {
  return {
    select: {
      mode: "dropdown",
      options: [
        // The row's own field, and the default: for most settings the answer is
        // a value or a device, and the menu is not what they are here for.
        { value: "none", label: "Input field" },
        // **First, and this is the whole point of the menu.** A condition is
        // what a person reaches for when the answer is not a thing but a
        // question about the house, and it is the only one of the four that
        // works on a row a trigger names -- Open House works the condition out
        // and points the trigger at the entity it makes.
        { value: "condition", label: "A condition (Home Assistant's editor)" },
        // **On every row, and that is deliberate.** A flow is code, and what a
        // person does with it is theirs to decide: the output node -- the half
        // that hands a value back to this input -- is made whichever way the row
        // was answered, and the input node is made when the row's own answer
        // names an entity to trigger on. A row holding a number gets the output
        // node and an empty left-hand side to build into, which is the case this
        // was asked for. Offering it only where Open House could guess the
        // trigger would take the option away from the inputs most worth
        // programming.
        { value: "nodered", label: "A Node-RED flow (nodes, in Node-RED)" },
        // **Home Assistant's own script, which returns the value.** The one cast
        // that is a *sequence*: an if, a loop, a call to something else and a
        // value handed back at the end -- which an expression cannot say. Built
        // in Home Assistant's script editor, embedded in the row.
        { value: "script", label: "HAOS script logic (a script that returns it)" },
        {
          value: "template",
          label: inTrigger
            ? "A template -- will not work here, see below"
            : "A template (an expression)",
        },
      ],
    },
  };
}

/**
 * Which cast editor a stored setting opens on, and which one it is showing now.
 *
 * A setting with a **condition** behind it opens on the condition editor,
 * because the condition is the thing the person authored and the entity id the
 * input is bound to is machinery they never chose. A setting the person has
 * flipped the menu on since is what the menu says. The same rule for a **script**
 * and a **flow**: what the person authored is the script or the flow, and the
 * value it hands back is not theirs to edit here.
 */
export function castModeForSetting(
  setting: ModuleInputRow,
  chosen?: CastMode,
): CastMode {
  if (chosen) return chosen;
  // A setting a flow answers opens on the flow, for the reason a condition does:
  // the flow is the thing the person authored, and the entity it writes is
  // machinery they never chose.
  if (setting.flow_id) return "nodered";
  // A script the same again: the script is the answer, and what it returns is
  // read rather than written here.
  if (setting.script_id) return "script";
  // **Truthiness, not presence.** The server sends `"cast": null` for every
  // setting that has none -- `record.derived.get(name)`, which is `None` for
  // all but the cast ones -- and `null !== undefined`, so a presence test opens
  // *every* setting on the condition editor and, worse, saves each one as a
  // cast rather than as the value the person typed. A condition is a mapping the
  // person built, and only a mapping is one.
  return setting.cast ? "condition" : "none";
}

/**
 * The cast the *module* holds for this row, read off the row the server sent.
 *
 * Read apart from `castModeForSetting`, which answers a different question --
 * *which editor to open* -- and so calls a **template** row `"none"`: a
 * template is already the row's own box, and opening the cast menu on one would
 * put a second template box under the first. The record does hold a cast there,
 * though: the row's answer *is* template text, which is the definition of the
 * template cast, and it is what makes the row one that could be detached.
 *
 * So this is the question "is there logic on this row at all", and the one place
 * that asks it is the detach button -- which is offered exactly where there is
 * something to move out.
 */
export function castHeldBy(setting: ModuleInputRow): CastMode {
  const mode = castModeForSetting(setting);
  if (mode !== "none") return mode;
  return isTemplate(setting.value) ? "template" : "none";
}

/**
 * Whether a flow can be started by *this* row's answer, with no one asked.
 *
 * **This narrows what Open House builds, and no longer what it offers.** Every
 * setting can be answered by a flow; this says only whether the push will arrive
 * with its input node already made. It is about the trigger and not about the
 * row: a state trigger watches exactly one entity, so it can be built when the
 * row's own answer resolves to one -- a device the person picked, a target, a
 * slot the room binds, another module's output. A row holding a number or a
 * piece of text has no "when this changes" to hand Node-RED, so that row's flow
 * comes with the output node alone and an empty left-hand side, and whatever
 * starts it is the person's to build.
 *
 * A **script** cast asks the same question and answers it the same way: a script
 * runs when something calls it, so the input node is built when the row names an
 * entity to watch, and a row holding a number gets the call with nothing to
 * start it.
 *
 * `multiple` is the one case that is neither: a target set to several entities
 * is a list, and a trigger given a list would fire on whichever of them was
 * written first -- which is not a thing anybody can be told about their module.
 * The output node is still made; only the trigger is withheld.
 */
export function canFlowWatch(setting: ModuleInputRow): boolean {
  if (setting.multiple) return false;
  const kind = setting.bound_kind ?? "";
  if (kind === "entity" || kind === "output" || kind === "slot") return true;
  // A row a flow already answers is bound to the flow's own entity, so what it
  // watches is a fact the record holds rather than something to work out here.
  if (setting.flow_id) return true;
  return setting.selector === "entity" || setting.selector === "target";
}

/**
 * Whether this setting is one a cast can be written over at all.
 *
 * Not a rule about *which* cast any more -- all four are offered on every row --
 * but about whether the row's control is itself the editor the cast would be. A
 * setting whose stored value is already a template is showing the template box,
 * so a second one under it would be two ways to write one thing; a literal is
 * text the person types. Everything else -- a device, a target, a number, a
 * text input -- is a choice a cast can be written over.
 */
export function canCastSetting(setting: ModuleInputRow, value: unknown): boolean {
  if (isTemplate(value)) return false;
  return setting.bound_kind !== "condition" && setting.bound_kind !== "flow";
}

/**
 * The binding a **template** cast stands for, or `null` when there is none.
 *
 * A cast is the *whole* answer: the device under it is what the person picked
 * while deciding, and what the automation is built with is the logic they wrote.
 * Empty is how the choice comes back, which is what makes the box on-demand
 * rather than a second, competing answer.
 *
 * A *condition*, *flow* or *script* cast is deliberately not here: none of the
 * three is a binding but a `casts` (or `flows`, or `scripts`) entry, because the
 * server makes an entity or a call out of it and binds the input to that -- and a
 * binding sent beside it would be the answer that lost.
 */
export function castBinding(cast: string | undefined): ModuleBinding | null {
  const text = (cast ?? "").trim();
  return text ? { kind: "literal", value: text } : null;
}

/**
 * The id inside an entity id a script picker handed back, or `""`.
 *
 * A script cast is answered by picking one of the scripts the house already has
 * -- the control is an entity picker over the `script` domain, because a script
 * *is* an entity of Home Assistant's -- and what a `script.` call takes is the id
 * without its domain. So this is the one place the two spellings meet, and it is
 * here rather than in either screen because both screens show this control: the
 * import screen's row and the card's settings form.
 *
 * **Tolerant on purpose.** The picker holds `script.turn_it_on` and the answer is
 * `turn_it_on`, but the same value may arrive with the domain already off, or as
 * something this cannot read at all -- and a screen that threw here would lose a
 * whole form to one unreadable field.
 */
export function scriptIdOf(value: unknown): string {
  if (typeof value !== "string") return "";
  const trimmed = value.trim();
  return trimmed.startsWith("script.") ? trimmed.slice("script.".length) : trimmed;
}
