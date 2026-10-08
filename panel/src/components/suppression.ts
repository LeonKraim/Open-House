/**
 * The red panel a suppressed module wears: who is holding it off, and that the
 * holding is temporary.
 *
 * A module another module has switched off is the one state on the Modules tab
 * that looks exactly like a fault and is not one. Its own switch reads "on", its
 * settings are intact, and nothing it does happens -- so a person who opens the
 * page to find out why sees a card that appears to be lying to them. The panel
 * exists to say the missing sentence: *this* module is being held off, by
 * *that* one, and nothing has been changed about it.
 *
 * Three facts are drawn and no others, because they are the three the server
 * sends and computes (`ha_adapter/live_modules`): the holder's name, the
 * behaviour of the holder that declared it, and the target's own name. The panel
 * derives nothing -- it does not compare switches, does not look for a
 * suppression in a list, and does not decide whether the module "should" be
 * running -- because a second reading of "who is suppressing whom" would be free
 * to disagree with the engine that produced the `skipped: suppressed` records
 * the person is reading.
 *
 * The release is deliberately *not* offered here. What ends a suppression is the
 * holder's own switch or the suppressing atom's, and a button on the target's
 * card that reached into another module's card would be this panel editing
 * something a person cannot see. The sentence names where to go instead.
 */

import { html, type TemplateResult } from "lit";
import type { InstalledModule } from "../api/models.ts";

/**
 * The red panel for a suppressed module, or `null` when it is not suppressed.
 *
 * `null` rather than an empty template so the caller's `?`-shaped render reads
 * as "there is nothing here" at the call site, which is what a module nobody is
 * holding off is.
 */
export function suppressionBanner(
  module: InstalledModule,
): TemplateResult | null {
  if (module.suppressed_by === null) return null;
  const holder = module.suppressed_by;
  const atom = module.suppressed_behaviour;
  return html`<div class="banner error" role="status">
    <strong>This module is currently suppressed by the ${holder} module.</strong>
    ${atom
      ? html`<p class="help" style="margin:6px 0 0">
          The ${holder} module's
          <code>${atom}</code>
          behaviour is holding it off. This is a temporary override: nothing about
          ${module.name || module.pack} has been changed, and its own switch is
          still where you left it. It comes back the moment ${holder} stops.
        </p>`
      : html`<p class="help" style="margin:6px 0 0">
          Another module is holding it off. This is a temporary override: nothing
          about ${module.name || module.pack} has been changed, and it comes back
          the moment ${holder} stops.
        </p>`}
  </div>`;
}
