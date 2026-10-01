# Author contact — the two unlicensed repos

Two of the four reference repositories grant nothing: neither ships a licence
file, so no licence attaches to their code or their prose and both are
`ideas_only` for every artifact kind. This page is the exact ask that would be
put to each author, and the record of where that ask has got to.

**Status: not attempted, for both.** Making contact is an outward-facing action
and requires the user's explicit authorisation, which has not been given. No
message has been sent to either author. Until one is, both records in
[`catalog/licenses.yaml`](../../catalog/licenses.yaml) carry
`outcome: not_attempted`, `attempted_on: null` and a reason, and both repos stay
`ideas_only`.

There is no date on which nothing happened, which is why `attempted_on` is
`null` rather than a plausible-looking timestamp: a filled-in date on an
unattempted ask is a record that lies in the direction of looking finished.

## fwartner/homeassistant-config — Florian Wartner

**The ask.** Confirm that the MIT licence declared in the repository's README is
intended to apply to the repository, and add a `LICENSE` file carrying it.

**Why it would change something.** This is the highest-value contact of the two,
because the author has already stated an intent to grant. The repository's
README declares MIT; only the file is missing. If the author confirms, the
record's `license_code` and `license_prose` become `mit`, the derived statuses
become `reusable` with an `attribution` obligation, and this repo joins the two
that may donate expression.

**Why it is not taken as already true.** A claim is not a grant. Recording `mit`
on the strength of a README sentence would make the corpus depend on a reading
of the author's intent that the author has not confirmed, and the whole point of
recording licence facts separately from licence claims is to not do that.

**Channel.** A GitHub issue on the repository, or the address in its commit
metadata. Either is a public, on-the-record ask, which is what the record needs
to be citable.

**Current record.** `outcome: not_attempted`, `attempted_on: null`,
`readme_licence_claim: MIT`, `licence_claim_discrepancy` populated — see
[`fwartner.md`](fwartner.md).

## johnkoht/hassio-config — John Koht

**The ask.** Ask whether the author is willing to license the repository — MIT
or Apache-2.0 would both work — and if so, to add the corresponding `LICENSE`
file.

**Why this one is harder.** There is no stated intent to point at. The
repository ships no licence file and its README makes no licence claim, so this
is a cold ask rather than a confirmation. There is no `readme_licence_claim` and
no `licence_claim_discrepancy` on the record, because a discrepancy field
records the gap between a claim and a file and here there is no claim.

**Why it is still worth asking.** The repository is one of the four the corpus
is built from, and a grant would move it from concepts-only to a full donor. A
refusal or a silence costs nothing that has not already been assumed: the record
stays `no_licence` and the repo stays `ideas_only`.

**Channel.** As above — a GitHub issue on the repository, or the address in its
commit metadata.

**Current record.** `outcome: not_attempted`, `attempted_on: null` — see
[`johnkoht.md`](johnkoht.md).

## What happens on a reply

The record changes and every derived value follows the table; nothing else in
the corpus needs editing. The reuse gate reads `reuse_status_code`, so the same
row that is rejected today is accepted the moment the status becomes `reusable`,
and an approval that never comes leaves everything as it is.

A reply is not a grant either. A claim is not a grant and neither is a message:
what the record needs is the licence value plus the evidence for it, which in
practice means a licence file in the repository or an explicit statement the
author is content to have cited.
