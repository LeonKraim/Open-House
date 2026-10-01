/**
 * The bundle's entry point: what `panel_custom` loads.
 *
 * Home Assistant's `panel_custom` imports one module URL and expects the import
 * itself to have defined the custom element named by `webcomponent_name`. There
 * is no second call and no bootstrap function it invokes, so registering the
 * element is a side effect of importing this file.
 *
 * The integration registers the panel as:
 *
 *     webcomponent_name="open-house-panel"
 *     module_url="/local/open-house-panel.js"
 *
 * See `README.md` for the exact `panel_custom` registration the integration is
 * asked to make, and `panel/open-house-panel.ts` for the element itself.
 */

import "./panel/open-house-panel.ts";

/**
 * There is exactly one name, and it is the one the integration registered.
 *
 * An earlier version also defined `ha-panel-open-house` against the same class,
 * on the theory that Home Assistant's derived name might be the one it looks
 * for. That cannot work: `customElements.define` rejects a constructor that has
 * already been used in the registry *even under a different name*, so the
 * second call throws, the module fails to import, and Home Assistant falls back
 * to the iframe panel -- a blank tab, from a line meant to prevent one. A
 * `customElements.get` guard does not save it either, because the new name is
 * free while the class is already spent.
 *
 * `webcomponent_name` is passed explicitly, so the name is not derived and there
 * is nothing to hedge against.
 */
