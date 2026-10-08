/**
 * Unit tests for the Health tab's one piece of reasoning about data.
 *
 * The screen is checked by looking at it; what is pinned here is the summary it
 * draws from its own list. The count is taken here rather than trusted from a
 * second call, and that is the point: the chips above the cards and the cards
 * themselves are counted from one array, so a wrong tally is a header that
 * disagrees with the list under it -- the one thing a summary must never do.
 */

import { strict as assert } from "node:assert";
import { test } from "node:test";

import type { HealthIssue } from "../api/models.ts";
import { severityCounts } from "./health.ts";

/** One issue, with only the field the count reads. */
function issue_(severity: HealthIssue["severity"]): HealthIssue {
  return { severity } as HealthIssue;
}

test("each severity is counted on its own", () => {
  const counts = severityCounts([
    issue_("error"),
    issue_("warning"),
    issue_("warning"),
    issue_("info"),
    issue_("info"),
    issue_("info"),
  ]);
  assert.deepEqual(counts, { error: 1, warning: 2, info: 3 });
});

test("a severity with no issues is zero, not absent", () => {
  // A `Record` with a missing key would render `NaN` into the chip -- the same
  // class of fault the Overview's counts are defaulted for.
  assert.deepEqual(severityCounts([issue_("warning")]), {
    error: 0,
    warning: 1,
    info: 0,
  });
});

test("an empty report counts nothing, four zeroes", () => {
  assert.deepEqual(severityCounts([]), { error: 0, warning: 0, info: 0 });
});
