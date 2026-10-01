/**
 * A form rendered from a JSON Schema, with no pack-supplied JavaScript.
 *
 * spec.txt is specific about this: a room's settings page "shows only
 * high-level options, rendered from pack schemas with no pack-supplied JS". A
 * pack is a declarative document, and the sandbox that keeps it declarative
 * everywhere else would be pointless if the panel turned around and evaluated a
 * string a pack had authored. So the pack declares a JSON Schema, the server
 * hands it over, and this element turns it into inputs. Nothing here is
 * `eval`'d, `new Function`'d, or assigned as `innerHTML`; every value that
 * reaches the DOM reaches it as text or as an attribute Lit escaped.
 *
 * The supported subset is the subset the pack schemas use, and it is stated
 * rather than discovered:
 *
 *   * object with `properties` and `required` -- a fieldset, recursed
 *   * `enum` (any type) -- a select, or a checkbox group for arrays
 *   * boolean -- a switch
 *   * integer / number -- a number input
 *   * string -- a text input, a textarea for `format: "multiline"`, a time
 *     input for `format: "time"`, and a colour input for `format: "color"`
 *   * array of a primitive -- a list with add and remove
 *   * `default` -- used to seed a control with no value yet
 *
 * Anything outside the subset renders as a labelled note saying so. Dropping an
 * unknown field silently would let a pack believe it had configured something
 * the panel never showed, which is the failure a schema-driven form exists to
 * prevent.
 */

import { LitElement, html, nothing, type TemplateResult } from "lit";
import {
  defaultValue,
  fieldKind,
  fieldLabel,
  fieldOrder,
  humanizeKey,
  isMissing,
  type JsonSchema,
  type OptionsValues,
} from "./schema-spec.ts";

export type { FieldKind, JsonSchema, OptionsValues } from "./schema-spec.ts";

/**
 * The element: a fieldset per object, a control per leaf.
 *
 * It is uncontrolled in the Lit sense -- `values` is the source of truth and
 * every edit re-emits the whole object on `value-changed`, because a nested
 * object edited in place would make the caller reason about which subtree
 * changed.
 */
export class SchemaForm extends LitElement {
  static override properties = {
    schema: { attribute: false },
    values: { attribute: false },
    readonly: { type: Boolean },
    disabled: { type: Boolean },
  };

  declare schema: JsonSchema | null;
  declare values: OptionsValues;
  declare readonly: boolean;
  declare disabled: boolean;

  constructor() {
    super();
    this.schema = null;
    this.values = {};
    this.readonly = false;
    this.disabled = false;
  }

  /** Lit renders into a shadow root; the parent's styles do not reach in. */
  protected override createRenderRoot(): HTMLElement | DocumentFragment {
    return this;
  }

  private emit(next: OptionsValues): void {
    this.values = next;
    this.dispatchEvent(
      new CustomEvent<OptionsValues>("value-changed", {
        detail: next,
        bubbles: true,
        composed: true,
      }),
    );
  }

  private setChild(parentKey: string | null, key: string, value: unknown): void {
    if (parentKey === null) {
      this.emit({ ...this.values, [key]: value });
      return;
    }
    const parent = (this.values[parentKey] ?? {}) as OptionsValues;
    this.emit({ ...this.values, [parentKey]: { ...parent, [key]: value } });
  }

  private fieldValue(parentKey: string | null, key: string, schema: JsonSchema): unknown {
    const parent = parentKey === null ? this.values : (this.values[parentKey] as OptionsValues | undefined);
    const value = parent?.[key];
    return value === undefined ? defaultValue(schema) : value;
  }

  protected override render(): TemplateResult {
    if (!this.schema || !this.schema.properties) {
      return html`<p class="muted">This pack declares no options.</p>`;
    }
    return html`${this.renderObject(this.schema, null)}`;
  }

  private renderObject(schema: JsonSchema, parentKey: string | null): TemplateResult {
    const order = fieldOrder(schema);
    if (order.length === 0) {
      return html`<p class="muted">This pack declares no options here.</p>`;
    }
    return html`<div class="fields">
      ${order.map((key) => this.renderField(key, schema, parentKey))}
    </div>`;
  }

  private renderField(
    key: string,
    parent: JsonSchema,
    parentKey: string | null,
  ): TemplateResult {
    const schema = parent.properties?.[key] ?? {};
    const required = (parent.required ?? []).includes(key);
    const value = this.fieldValue(parentKey, key, schema);
    const label = fieldLabel(key, schema);
    const id = `${parentKey ?? "root"}-${key}`;
    const help = schema.description
      ? html`<p class="help" id="${id}-help">${schema.description}</p>`
      : nothing;
    const missing = required && isMissing(schema, value)
      ? html`<p class="help warn">Required.</p>`
      : nothing;
    const control = this.renderControl(key, schema, value, parentKey, id, label);
    return html`<div class="field" data-required=${required ? "true" : "false"}>
      <div class="label-row">
        <span class="label">${label}</span>
        ${required ? html`<span class="required-pill" title="Required">required</span>` : nothing}
      </div>
      ${help}${control}${missing}
    </div>`;
  }

  private renderControl(
    key: string,
    schema: JsonSchema,
    value: unknown,
    parentKey: string | null,
    id: string,
    label: string,
  ): TemplateResult {
    if (this.readonly || this.disabled) {
      return this.renderReadonly(schema, value);
    }
    switch (fieldKind(schema)) {
      case "object":
        return html`<fieldset class="nested">
          <legend>${label}</legend>
          ${this.renderObject(schema, key)}
        </fieldset>`;
      case "enum":
        return html`<select
          id=${id}
          aria-label=${label}
          .value=${String(value ?? "")}
          @change=${(event: Event) =>
            this.setChild(parentKey, key, (event.target as HTMLSelectElement).value)}
        >
          ${(schema.enum ?? []).map(
            (option) => html`<option
              value=${String(option)}
              ?selected=${String(option) === String(value ?? "")}
            >${humanizeKey(String(option))}</option>`,
          )}
        </select>`;
      case "multi-enum": {
        const selected = new Set(
          Array.isArray(value) ? value.map(String) : [],
        );
        return html`<div class="checks" role="group" aria-label=${label}>
          ${(schema.enum ?? []).map(
            (option) => html`<label class="check">
              <input
                type="checkbox"
                .checked=${selected.has(String(option))}
                @change=${(event: Event) => {
                  const next = new Set(selected);
                  if ((event.target as HTMLInputElement).checked) {
                    next.add(String(option));
                  } else {
                    next.delete(String(option));
                  }
                  this.setChild(parentKey, key, [...next]);
                }}
              />
              <span>${humanizeKey(String(option))}</span>
            </label>`,
          )}
        </div>`;
      }
      case "boolean":
        return html`<label class="toggle">
          <input
            type="checkbox"
            .checked=${value === true}
            aria-label=${label}
            @change=${(event: Event) =>
              this.setChild(parentKey, key, (event.target as HTMLInputElement).checked)}
          />
          <span>${value === true ? "On" : "Off"}</span>
        </label>`;
      case "number":
        return html`<input
          id=${id}
          type="number"
          aria-label=${label}
          .value=${String(value ?? "")}
          min=${schema.minimum ?? nothing}
          max=${schema.maximum ?? nothing}
          @change=${(event: Event) => {
            const raw = (event.target as HTMLInputElement).value;
            this.setChild(parentKey, key, raw === "" ? null : Number(raw));
          }}
        />`;
      case "multiline":
        return html`<textarea
          id=${id}
          aria-label=${label}
          .value=${String(value ?? "")}
          rows="3"
          @change=${(event: Event) =>
            this.setChild(parentKey, key, (event.target as HTMLTextAreaElement).value)}
        ></textarea>`;
      case "time":
        return html`<input
          id=${id}
          type="time"
          aria-label=${label}
          .value=${String(value ?? "")}
          @change=${(event: Event) =>
            this.setChild(parentKey, key, (event.target as HTMLInputElement).value)}
        />`;
      case "color":
        return html`<input
          id=${id}
          type="color"
          aria-label=${label}
          .value=${String(value || "#000000")}
          @change=${(event: Event) =>
            this.setChild(parentKey, key, (event.target as HTMLInputElement).value)}
        />`;
      case "list": {
        const items = Array.isArray(value) ? value : [];
        const numeric = fieldKind(schema.items ?? {}) === "number";
        return html`<div class="list">
          ${items.map(
            (item, index) => html`<div class="list-row">
              <input
                type=${numeric ? "number" : "text"}
                aria-label="${label} ${index + 1}"
                .value=${String(item ?? "")}
                @change=${(event: Event) => {
                  const raw = (event.target as HTMLInputElement).value;
                  const next = [...items];
                  next[index] = numeric ? Number(raw) : raw;
                  this.setChild(parentKey, key, next);
                }}
              />
              <button
                type="button"
                class="icon"
                title="Remove"
                @click=${() => {
                  const next = [...items];
                  next.splice(index, 1);
                  this.setChild(parentKey, key, next);
                }}
              >Remove</button>
            </div>`,
          )}
          <button
            type="button"
            class="secondary"
            @click=${() => this.setChild(parentKey, key, [...items, numeric ? 0 : ""])}
          >Add</button>
        </div>`;
      }
      default:
        return html`<p class="help warn">
          This option's schema uses a shape the panel cannot render, so it is
          not editable here.
        </p>`;
    }
  }

  private renderReadonly(schema: JsonSchema, value: unknown): TemplateResult {
    if (value === undefined || value === null || value === "") {
      return html`<p class="readonly-value muted">Not set</p>`;
    }
    if (typeof value === "boolean") {
      return html`<p class="readonly-value">${value ? "On" : "Off"}</p>`;
    }
    if (Array.isArray(value)) {
      return html`<p class="readonly-value">${value.map((v) => String(v)).join(", ")}</p>`;
    }
    if (typeof value === "object") {
      return html`<p class="readonly-value muted">Not shown here.</p>`;
    }
    void schema;
    return html`<p class="readonly-value">${String(value)}</p>`;
  }
}

if (!customElements.get("open-house-schema-form")) {
  customElements.define("open-house-schema-form", SchemaForm);
}
