import { css } from "lit";

/**
 * The panel's shared stylesheet.
 *
 * It is written against Home Assistant's own CSS custom properties
 * (`--primary-color`, `--card-background-color`, `--ha-card-border-radius`, and
 * the rest) rather than against literal colours, so the panel follows the
 * user's theme, their light/dark choice and their density setting without a
 * theme of its own to keep in sync. A literal colour here would look wrong in
 * someone's dark theme the day they set one.
 *
 * Every element in this bundle renders into the light DOM (see
 * `OpenHouseElement.createRenderRoot`), so this stylesheet is injected once by
 * the root panel element rather than repeated per shadow root, and the layout
 * primitives below are available to every tab without each importing them.
 */
export const sharedStyles = css`
  :host {
    display: block;
    color: var(--primary-text-color);
    background: var(--primary-background-color);
    font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif);
    font-size: var(--ha-font-size-m, 14px);
  }

  * {
    box-sizing: border-box;
  }

  .layout {
    max-width: 1200px;
    margin: 0 auto;
    padding: 16px;
  }

  h1,
  h2,
  h3 {
    font-weight: var(--ha-font-weight-normal, 400);
    margin: 0;
  }

  h1 {
    font-size: var(--ha-font-size-xl, 24px);
  }

  h2 {
    font-size: var(--ha-font-size-l, 20px);
  }

  h3 {
    font-size: var(--ha-font-size-m, 16px);
    font-weight: 500;
  }

  a {
    color: var(--primary-color);
  }

  .card {
    background: var(--card-background-color);
    border-radius: var(--ha-card-border-radius, 12px);
    border: 1px solid var(--ha-card-border-color, var(--divider-color));
    padding: 16px;
    margin-bottom: 16px;
  }

  .row {
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .row.wrap {
    flex-wrap: wrap;
  }

  .spread {
    justify-content: space-between;
  }

  /*
   * A screen's header row: its title and controls, spaced apart.
   *
   * A class rather than the inline margin-bottom each screen used to carry, so
   * the header sits the same distance above its content everywhere -- and so the
   * overview's redesign has no layout of its own to keep in step.
   */
  .tab-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
    margin-bottom: 12px;
  }

  /* A row of people chips under the summary line, above the room table. */
  .people {
    margin-bottom: 12px;
  }

  .grow {
    flex: 1;
    min-width: 0;
  }

  .stack {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .muted {
    color: var(--secondary-text-color);
  }

  .small {
    font-size: var(--ha-font-size-s, 12px);
  }

  button {
    font: inherit;
    border-radius: 6px;
    border: 1px solid var(--divider-color);
    background: var(--card-background-color);
    color: var(--primary-text-color);
    padding: 6px 14px;
    cursor: pointer;
  }

  button:hover {
    background: var(--secondary-background-color, rgba(0, 0, 0, 0.04));
  }

  button:disabled {
    opacity: 0.5;
    cursor: default;
  }

  /*
   * A block this house cannot reach yet -- Node-RED that is not set up.
   *
   * Drawn rather than hidden, because the sentence inside it is how a person
   * finds out what to install, and greyed rather than ordinary so it does not
   * read as a control that did not work. The same class on the editor's block
   * and on the cast menu's disabled entry, so "not available" is one look in
   * this panel rather than two that agree only until one is edited.
   */
  .unavailable {
    opacity: 0.6;
  }

  /*
   * A disabled entry in a menu: one a person can read and cannot pick. Engines
   * grey it by default; restated so the cast a house has no Node-RED for cannot
   * read as an ordinary choice in any of them.
   */
  option:disabled {
    color: var(--disabled-text-color, var(--secondary-text-color));
  }

  button.primary {
    background: var(--primary-color);
    border-color: var(--primary-color);
    color: var(--text-primary-color, #fff);
  }

  button.danger {
    background: var(--error-color);
    border-color: var(--error-color);
    color: #fff;
  }

  button.icon {
    padding: 4px 8px;
    font-size: var(--ha-font-size-s, 12px);
  }

  /*
   * A behaviour chip that is a switch.
   *
   * The atom toggles on the modules screens are chips because a pack is read as
   * a row of its parts, and they are *buttons* rather than spans because a
   * person turns one part off by clicking it. Only a few properties are
   * restated -- the pill geometry the button rule would otherwise square off --
   * and the ok/warn colours are repeated at this specificity because a single
   * class like .chip.ok would lose to the button.chip rule below.
   */
  button.chip {
    border: 1px solid transparent;
    border-radius: 999px;
    padding: 1px 10px;
    font-size: var(--ha-font-size-s, 12px);
    line-height: 1.6;
    background: var(--secondary-background-color, rgba(0, 0, 0, 0.06));
    color: var(--secondary-text-color);
  }

  button.chip:hover {
    border-color: var(--divider-color);
  }

  button.chip.ok {
    background: color-mix(in srgb, var(--success-color, #0f9d58) 18%, transparent);
    color: var(--success-color, #0f9d58);
  }

  button.chip.ok:hover {
    border-color: var(--success-color, #0f9d58);
  }

  input,
  select,
  textarea {
    font: inherit;
    color: var(--primary-text-color);
    background: var(--card-background-color);
    border: 1px solid var(--divider-color);
    border-radius: 6px;
    padding: 6px 8px;
    min-width: 0;
  }

  input:focus-visible,
  select:focus-visible,
  textarea:focus-visible,
  button:focus-visible {
    outline: 2px solid var(--primary-color);
    outline-offset: 1px;
  }

  table {
    width: 100%;
    border-collapse: collapse;
  }

  th {
    text-align: left;
    font-weight: 500;
    color: var(--secondary-text-color);
    border-bottom: 1px solid var(--divider-color);
    padding: 6px 8px;
  }

  td {
    padding: 8px;
    border-bottom: 1px solid var(--divider-color);
    vertical-align: middle;
  }

  .chip {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    border-radius: 999px;
    padding: 1px 10px;
    font-size: var(--ha-font-size-s, 12px);
    background: var(--secondary-background-color, rgba(0, 0, 0, 0.06));
    color: var(--secondary-text-color);
    white-space: nowrap;
  }

  .chip.ok {
    background: color-mix(in srgb, var(--success-color, #0f9d58) 18%, transparent);
    color: var(--success-color, #0f9d58);
  }

  .chip.warn {
    background: color-mix(in srgb, var(--warning-color, #ff9800) 18%, transparent);
    color: var(--warning-color, #ff9800);
  }

  .chip.error {
    background: color-mix(in srgb, var(--error-color, #db4437) 18%, transparent);
    color: var(--error-color, #db4437);
  }

  /*
   * A chip that stands for neither good nor bad -- a decision the engine
   * *skipped*, a person who is away -- so it stays grey and is deliberately a
   * class rather than nothing at all.
   *
   * It exists because a mapping that leaves a state's appearance as the empty
   * string reads as "no chip", and a state that is neither an error nor a
   * success still has to look like the chip it is rather than like a word that
   * happened to land beside the coloured ones.
   */
  .chip.neutral {
    background: var(--secondary-background-color, rgba(127, 127, 127, 0.12));
    color: var(--secondary-text-color);
    border: 1px solid var(--divider-color);
  }

  .banner {
    border-radius: 8px;
    padding: 10px 14px;
    margin-bottom: 12px;
    border-left: 4px solid var(--divider-color);
    background: var(--secondary-background-color, rgba(0, 0, 0, 0.04));
  }

  .banner.error {
    border-left-color: var(--error-color);
  }

  .banner.warn {
    border-left-color: var(--warning-color);
  }

  .banner.info {
    border-left-color: var(--primary-color);
  }

  .empty {
    text-align: center;
    color: var(--secondary-text-color);
    padding: 40px 16px;
  }

  .help {
    color: var(--secondary-text-color);
    font-size: var(--ha-font-size-s, 12px);
    margin: 2px 0 0;
  }

  .help.warn {
    color: var(--warning-color);
  }

  /*
   * A room's unbound required slots, kept folded away under its name.
   *
   * An expander rather than a sentence in the cell: the list is worth reading
   * when a room is being fixed and is noise on a table a person is scanning, and
   * only the rooms that have one draw it.
   */
  .missing {
    margin-top: 2px;
    font-size: var(--ha-font-size-s, 12px);
    color: var(--secondary-text-color);
  }

  .missing > summary {
    cursor: pointer;
    color: var(--warning-color);
  }

  /*
   * Node-RED, embedded. The editor is a whole application in an iframe and the
   * only thing this panel decides about it is how much room it gets: a fixed
   * height rather than an intrinsic one, because an iframe has no intrinsic
   * height and would otherwise collapse to nothing; and a border, because an
   * editor floating on the card reads as a picture of one rather than as the
   * thing you type in.
   */
  .embed {
    border: 1px solid var(--divider-color);
    border-radius: var(--ha-border-radius-md, 8px);
    overflow: hidden;
    margin-top: 6px;
  }

  .embed-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    flex-wrap: wrap;
    padding: 4px 4px 4px 10px;
    background: var(--secondary-background-color, rgba(0, 0, 0, 0.04));
    border-bottom: 1px solid var(--divider-color);
    font-size: var(--ha-font-size-s, 12px);
  }

  .embed-actions {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .embed-actions button {
    font-size: var(--ha-font-size-s, 12px);
    padding: 2px 10px;
  }

  .embed iframe {
    display: block;
    width: 100%;
    height: 68vh;
    min-height: 420px;
    border: 0;
    background: var(--card-background-color, #fff);
  }

  /*
   * An embed that has to be a whole page rather than a box in a row.
   *
   * Home Assistant's script editor is a frontend page -- its own header, its own
   * sidebar, its own save button -- and 68vh of one inside a settings row is a
   * page nobody can work in. So it opens over the screen: still in the panel,
   * still beside the row it answers, and given the room a page needs. The rules
   * below come after the embed's on purpose, because the iframe selector in both
   * weighs the same and the last one written is the one that wins.
   */
  .embed-modal {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.55);
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 16px;
    z-index: 20;
  }

  .embed-modal .sheet {
    display: flex;
    flex-direction: column;
    width: min(1200px, 100%);
    height: min(900px, 100%);
    background: var(--card-background-color);
    border-radius: var(--ha-card-border-radius, 12px);
    overflow: hidden;
  }

  .embed-modal iframe {
    display: block;
    flex: 1;
    width: 100%;
    min-height: 0;
    border: 0;
    background: var(--card-background-color, #fff);
  }

  .tabs {
    display: flex;
    gap: 4px;
    border-bottom: 1px solid var(--divider-color);
    overflow-x: auto;
    padding: 0 8px;
    /* Kept under the app bar while a long screen -- a house with twenty health
       issues -- scrolls past it, so the way to another tab is always on screen.
       Its own background is what stops the tabs from being read over the content
       sliding beneath them. */
    position: sticky;
    top: 0;
    z-index: 4;
    background: var(--primary-background-color);
  }

  .tab {
    border: none;
    background: none;
    border-bottom: 3px solid transparent;
    border-radius: 0;
    padding: 10px 14px;
    white-space: nowrap;
    color: var(--secondary-text-color);
  }

  .tab[aria-selected="true"] {
    color: var(--primary-color);
    border-bottom-color: var(--primary-color);
    font-weight: 500;
  }

  /* A tabs row inside a card is a choice *within a step* -- the source a module
     is read from -- and not a way to move around the app. Drawn exactly as the
     app's tab bar it read as a second navigation strip stacked in the page, and
     because the tabs row is sticky it stuck at the same offset as the real bar
     and overlapped it on a screen long enough to scroll. It keeps the selected
     underline, which is the part that says what is chosen, and loses the bar and
     the stickiness. */
  .card .tabs {
    position: static;
    padding: 0;
    margin-bottom: 12px;
    border-bottom: none;
  }

  .field {
    margin-bottom: 14px;
  }

  .field .label-row {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .field .label {
    font-weight: 500;
  }

  .required-pill {
    font-size: var(--ha-font-size-xs, 10px);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--warning-color);
  }

  .field input[type="text"],
  .field input[type="number"],
  .field input[type="time"],
  .field select,
  .field textarea {
    width: 100%;
    margin-top: 4px;
  }

  .nested {
    border: 1px solid var(--divider-color);
    border-radius: 8px;
    padding: 12px;
    margin-top: 6px;
  }

  .nested legend {
    font-weight: 500;
    padding: 0 4px;
  }

  .checks {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    margin-top: 4px;
  }

  .check,
  .toggle {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }

  /*
   * The reach control: a checkbox dropdown, so the places an atom applies to
   * are named rather than translated into a boolean, and more than one can be
   * chosen at once. The summary is the answer ("Kitchen, Hall +1"); the menu
   * is one tick per place.
   */
  .reach {
    position: relative;
    display: inline-block;
  }

  .reach > summary {
    cursor: pointer;
    list-style: none;
    padding: 2px 10px;
    border: 1px solid var(--divider-color);
    border-radius: 999px;
    font-size: 0.85em;
    white-space: nowrap;
    color: var(--secondary-text-color);
  }

  .reach > summary::-webkit-details-marker {
    display: none;
  }

  .reach > summary::after {
    content: " ▾";
  }

  .reach[open] > summary {
    border-color: var(--primary-color);
    color: var(--primary-color);
  }

  .reach-menu {
    position: absolute;
    z-index: 8;
    margin-top: 4px;
    min-width: 180px;
    max-height: 260px;
    overflow-y: auto;
    padding: 8px;
    display: flex;
    flex-direction: column;
    gap: 4px;
    border: 1px solid var(--divider-color);
    border-radius: 8px;
    background: var(--card-background-color, var(--primary-background-color));
    box-shadow: 0 4px 14px rgba(0, 0, 0, 0.25);
  }

  .list {
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin-top: 4px;
  }

  .list-row {
    display: flex;
    gap: 6px;
  }

  .list-row input {
    flex: 1;
  }

  /*
   * One thing that could be published, as a block of its own.
   *
   * It is not a list-row: a candidate's expression is a whole Jinja template and
   * a horizontal flex gave it a share of one line, so a long one ran off the
   * card and over whatever was beside it. Stacked, it wraps inside the card --
   * breaking anywhere, because a template's longest word may have no space in it.
   */
  .candidate {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 8px 10px;
    border: 1px solid var(--divider-color);
    border-radius: 8px;
    background: var(--secondary-background-color, rgba(127, 127, 127, 0.06));
  }

  .candidate .field {
    margin-bottom: 0;
  }

  .expression {
    margin: 0;
    padding: 6px 8px;
    border-radius: 6px;
    background: var(--card-background-color, var(--primary-background-color));
    overflow-wrap: anywhere;
    white-space: pre-wrap;
    font-size: var(--ha-font-size-s, 12px);
    max-height: 12em;
    overflow-y: auto;
  }

  .readonly-value {
    margin: 4px 0 0;
  }

  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 12px;
  }

  .spinner {
    display: inline-block;
    width: 18px;
    height: 18px;
    border: 2px solid var(--divider-color);
    border-top-color: var(--primary-color);
    border-radius: 50%;
    animation: oh-spin 0.8s linear infinite;
  }

  @keyframes oh-spin {
    to {
      transform: rotate(360deg);
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .spinner {
      animation-duration: 2.4s;
    }
  }

  .visually-hidden {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
    white-space: nowrap;
  }

  /*
   * The stale page. Above everything, and deliberately not dismissible: no
   * backdrop-click rule and no close affordance, because a page that can be
   * dismissed is a page that goes on refusing every write with nothing on
   * screen saying why.
   */
  .stale-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.55);
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px;
    z-index: 20;
  }

  .stale-sheet {
    max-width: 560px;
    background: var(--card-background-color);
    border-radius: var(--ha-card-border-radius, 12px);
    border: 1px solid var(--ha-card-border-color, var(--divider-color));
    box-shadow: var(--ha-card-box-shadow, 0 8px 24px rgba(0, 0, 0, 0.3));
    padding: 20px;
  }

  .stale-sheet h2 {
    margin: 0 0 8px;
    font-size: var(--ha-font-size-l, 20px);
    font-weight: var(--ha-font-weight-normal, 400);
  }

  /*
   * Additions from the UI polish pass. Appended rather than woven in so the
   * handful of rules that were edited above keep their place and their meaning
   * in the diff.
   */

  /*
   * The app bar: the panel's name, above the tab strip.
   *
   * A brand mark rather than a page title -- it holds no heading, so the
   * screen's own h1 is the loudest thing on the page rather than the
   * second-loudest (see renderHeader). The bottom rule is what makes it read
   * as a bar over the strip and not as a first paragraph.
   */
  .app-bar {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
    padding: 0 8px 8px;
    margin-bottom: 4px;
    border-bottom: 1px solid var(--divider-color);
  }

  .app-bar .brand {
    font-weight: 500;
    letter-spacing: 0.01em;
    color: var(--primary-text-color);
  }

  .app-bar p {
    margin: 0;
  }

  /* The body under the tab strip, set off from it by the same gap everywhere. */
  .tab-body {
    margin-top: 16px;
  }

  /*
   * A row of filters, each with the word for what it narrows.
   *
   * align-items: flex-end so a filter whose label sits above a control lines
   * up with the next one whatever the control's height; the gap is wider than
   * the general .row gap because a labelled control is a wider unit than a
   * bare one.
   */
  .filters {
    display: flex;
    align-items: flex-end;
    flex-wrap: wrap;
    gap: 16px;
    margin-bottom: 12px;
  }

  .filter {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .filter .label {
    font-size: var(--ha-font-size-s, 12px);
    color: var(--secondary-text-color);
  }

  /*
   * A table that may be wider than the panel, made scrollable rather than cut.
   *
   * The Activity log has six columns and a narrow sidebar panel has room for
   * about three, so without this the last columns -- the outcome, and the "Why"
   * button that opens the reason -- ran off the edge with no way back. The
   * element also carries tabindex="0", which is what lets a keyboard reach the
   * scroll: a scroll container is otherwise unreachable without a pointer.
   */
  .table-scroll {
    overflow-x: auto;
    max-width: 100%;
  }

  /* A cell that must not wrap -- "just now" broke onto two lines at 480px. */
  .nowrap {
    white-space: nowrap;
  }

  /*
   * A health issue's title, at a card heading's size rather than a page's.
   *
   * It is an h2 because it is the top level of the page -- the page's own h1
   * is "Health", so an h3 skipped a level -- and the class keeps it from
   * growing to a section heading's size.
   */
  .issue-title {
    font-size: var(--ha-font-size-m, 16px);
    font-weight: 500;
    margin: 0;
  }

  /*
   * The engine's names for a fault, kept but folded away.
   *
   * Collapsed by default, and neutral rather than the warning colour the
   * "missing" expander uses: an entity id and a code are things to quote, not a
   * fault in themselves, and a card should read as the sentence it is before it
   * reads as a log line.
   */
  .tech {
    margin-top: 6px;
    font-size: var(--ha-font-size-s, 12px);
    color: var(--secondary-text-color);
  }

  .tech > summary {
    cursor: pointer;
  }

  .tech > .help {
    margin: 4px 0 0;
    overflow-wrap: anywhere;
  }
`;
