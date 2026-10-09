/**
 * The Dev tab: a source becomes a module the house runs.
 *
 * **Why it is a tab and not a script.** Importing an automation or a blueprint
 * as a module is a reading before it is a host: a person looks at what a
 * document asks for, says which entity fills which input, and says which of the
 * source's own internals should be published. Every one of those is a judgement
 * about their house, and the only place a judgement about their house can be
 * made is in front of their house.
 *
 * **Home Assistant's own editors do the editing.** The controls the import
 * screen draws are Home Assistant's controls, not lookalikes: `ha-form` renders
 * the inputs through `ha-selector`, which is what the automation and blueprint
 * editors themselves use, and `ha-code-editor` in YAML mode is the same
 * component the YAML view of an automation uses, so pasting a blueprint and
 * reading the module it became are both done in the editor the source came from.
 *
 * The screen itself is `<open-house-host-module>`, which owns the whole of the
 * import; this element is the tab that mounts it, so the sidebar has one entry
 * and the screen has a file of its own.
 */

import { html, type TemplateResult } from "lit";
import { OpenHouseElement } from "../base.ts";
import "./host-module.ts";

export class DevTab extends OpenHouseElement {
  override render(): TemplateResult {
    return html`<div class="layout">
      <h1>Dev</h1>
      <p class="muted">
        Import a blueprint or an automation as a module the house runs, as it
        is: nothing is translated, and Home Assistant goes on running the whole
        document.
      </p>
      <open-house-host-module
        .hass=${this.hass}
        .client=${this.client}
        .admin=${this.admin}
      ></open-house-host-module>
    </div>`;
  }
}

if (!customElements.get("open-house-tab-dev")) {
  customElements.define("open-house-tab-dev", DevTab);
}
