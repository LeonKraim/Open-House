/**
 * The dev harness entry: mount the panel against the mock backend.
 *
 * `npm run dev` loads this instead of `main.ts`. It registers the same elements
 * the bundle registers and then gives the panel a `hass` whose commands the mock
 * answers, so every tab can be opened in a browser with no Home Assistant and no
 * integration. It is not imported by `main.ts` and so is not in the bundle.
 */

import "../main.ts";
import { mockHass } from "./mock-backend.ts";
import type { HassLike } from "../api/connection.ts";

interface DevPanel extends HTMLElement {
  hass: HassLike;
  narrow: boolean;
}

await customElements.whenDefined("open-house-panel");

const panel = document.querySelector("open-house-panel") as DevPanel | null;
if (panel) {
  panel.hass = mockHass();
  panel.narrow = window.innerWidth < 700;
}
