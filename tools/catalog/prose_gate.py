"""The prose gate: no shipped artifact reproduces prose from a non-permissive repo.

The licence records separate a repo's code grant from its prose terms, and
renemarc is the reason they are separate: its code is `apache_2_0`, reusable,
while its prose is `cc_by_nc_sa` -- non-commercial and share-alike. The
`attribution` spec closes that gap on the code side with the expression rule and
on the prose side with the rule this check enforces: an idea from such a repo may
be restated in our own words, and a passage from it may not be reproduced.

What a quote would be taken from is exactly the text task 3.5 withholds -- the
aliases, display names and comments a source author wrote -- so the gate reads
the store that holds it, `.local/raw-verbatim.json`, rather than a clone. That
store is gitignored, and that is the point rather than an inconvenience: the
comparison is made against material the corpus is not allowed to ship, so the
material cannot itself be shipped to make the comparison. It is also why this is
local-only and deliberately absent from `validate.py`'s registry -- CI runs from
a checkout where `.local/` is absent, and a check that read it there could only
fail on the missing store or pass having read nothing, neither of which is a
statement about the tree.

## What "quoted" means here, and why the threshold sits where it does

Neither obvious reading works. Flagging every shared n-gram flags ordinary
English: our documents contain phrases like "the house is empty and the", source
comments contain other phrases like it, and coincidence rather than copying is
what links them. Matching a whole passage end to end catches nothing, because a
reproduced sentence is almost never a passage in full.

A quote here is a run of `MIN_QUOTE_TOKENS` consecutive tokens appearing, in the
same order, both in a shipped artifact and inside a single passage from a repo
whose prose is `ideas_only`. Tokens are whitespace-delimited, lowercased, and
stripped only of *surrounding* punctuation, so a path, a URL or a dotted
identifier stays one token. That last choice is load-bearing and was made by
measurement, not taste: splitting on every non-alphanumeric character turns
`/config/dashboards/.../x.yaml` into a dozen words, which then match the same
path written into a shipped data file -- an identifier the spec treats as a
recorded fact and not as expression. At the threshold chosen below, that reading
reported five shipped files, every one of them a shared path, URL or entity slug
and none of them a quote. Keeping such strings whole removes them from the
comparison instead of tuning a threshold around them.

The threshold is measured too. On the committed tree no shipped file shares a run
longer than six tokens with any non-permissive passage, and the only runs that
long are ordinary clauses of our own writing: `the house is empty and the`
coincides in `catalog/edge_cases.yaml`, and this docstring reaches the same
length by spelling the coincidence out. Eight leaves two tokens of margin over
that, and stays far short of a paragraph, so a reproduced sentence still trips it.
"""

from __future__ import annotations

import json
from functools import cache
from typing import TYPE_CHECKING

from . import errors, licenses, paths, vcs
from .errors import CheckError, Report
from .narrow import as_mapping, as_sequence, as_text

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

PROSE_GATE_CHECK = "prose-gate"

#: The gitignored store task 3.5 emits, by filename. Repeated here rather than
#: imported from `normalise` so the gate does not reach through the extraction
#: module for one string; a test asserts the two spellings agree, so the
#: repetition cannot drift into a gate that reads a file nothing writes.
VERBATIM_FILE = "raw-verbatim.json"

#: The licence records, by filename. The gate reads which repos' prose is
#: non-permissive from here rather than carrying a list: a repo whose terms
#: changed, or a fifth repo, would otherwise be checked against a stale set.
LICENCES_FILE = "licenses.yaml"

#: The withheld fields a passage could be quoted out of. These are the text
#: fields `normalise.Extraction` routes away from the committed store and into
#: the verbatim one, which is what makes the verbatim store the source of a quote.
PROSE_FIELDS: tuple[str, ...] = ("aliases", "names", "comments")

#: Consecutive tokens that must match, in order, before a shared run is a quote
#: rather than coincidence. See the module docstring for how it was chosen.
MIN_QUOTE_TOKENS = 8

#: Stripped from the *ends* of a token only, so `x.yaml`, `https://...` and
#: `input_boolean.x` stay single tokens while `"word,"`, `(word` and `word.` do
#: not carry the surrounding markdown or punctuation into the comparison.
_EDGE_PUNCTUATION = ".,;:!?()[]{}\"'`*<>|~\\/@#=_+-"


@cache
def _tokens(text: str) -> tuple[str, ...]:
    """A passage as the sequence of tokens a quote is measured in.

    Cached on the argument alone, which is sound because the function is pure:
    the same string always yields the same tokens, and the same passage is looked
    at once per artifact that might contain it.
    """
    tokens: list[str] = []
    for raw in text.lower().split():
        token = raw.strip(_EDGE_PUNCTUATION)
        if token:
            tokens.append(token)
    return tuple(tokens)


def load_verbatim() -> tuple[dict[str, object], ...]:
    """The withheld records, or `CheckError` if the store cannot be read.

    Raising rather than returning an empty tuple, for the reason
    `exceptions.load_exceptions` gives: a store read as empty would leave every
    artifact reported clean while nothing was compared, which is the weakest
    possible input passed as a result. `CheckError` costs this check its findings
    and names the file, and `validate_all` is written to catch it.

    Missing is raised rather than skipped even though the store is gitignored and
    a clean checkout will not have it. This check is local-only for that reason;
    a caller that reaches it without the store has asked a question the store is
    the only answer to, and an empty answer would be read as a pass.
    """
    path = paths.LOCAL / VERBATIM_FILE
    relative = f".local/{VERBATIM_FILE}"
    if not path.is_file():
        raise CheckError(
            PROSE_GATE_CHECK,
            relative,
            "does not exist; the prose gate compares shipped text against the "
            "withheld text this store carries, which the task 3.5 normaliser "
            "writes locally and git never tracks",
        )
    try:
        text = errors.read_text(path)
    except (OSError, UnicodeDecodeError) as exc:
        raise CheckError(PROSE_GATE_CHECK, relative, f"cannot be read: {exc}") from exc
    try:
        loaded: object = json.loads(text)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise CheckError(
            PROSE_GATE_CHECK, relative, f"is not valid JSON: {exc}"
        ) from exc

    records = as_sequence(as_mapping(loaded).get("records"))
    return tuple(as_mapping(record) for record in records)


def _repo_of(record_id: str, repos: frozenset[str]) -> str | None:
    """The repo a record came from, read off the id the extraction built it with.

    The verbatim store carries no `repo` field: it is a projection of the same
    records the committed store holds, and the committed store names a repo only
    through the id, which is `slug(f"{repo} {path}")`. No handle in
    `catalog/licenses.yaml` contains an underscore, so the id begins with the
    handle and an underscore, and the longest handle that fits is the one -- the
    longest-match form so that a future handle which is a prefix of another's
    cannot claim the other's records.
    """
    for repo in sorted(repos, key=len, reverse=True):
        if record_id.startswith(f"{repo}_"):
            return repo
    return None


def _non_permissive_prose_repos() -> frozenset[str]:
    """The repos whose prose terms are `ideas_only`, read from the records.

    Read through `licenses.load_licences` rather than stated here, because a
    status is derived from a licence value and a second copy of that derivation
    is the one thing this project refuses to keep.
    """
    path = paths.CATALOG / LICENCES_FILE
    relative = f"catalog/{LICENCES_FILE}"
    if not path.is_file():
        raise CheckError(
            PROSE_GATE_CHECK,
            relative,
            "does not exist; the gate reads which repos' prose is non-permissive "
            "from the licence records, and a tree without them has no answer",
        )
    return frozenset(
        record.repo
        for record in licenses.load_licences()
        if record.repo and record.reuse_status_prose == "ideas_only"
    )


def _passages(repos: frozenset[str]) -> Iterator[tuple[str, str]]:
    """Every withheld text field of every record belonging to a named repo."""
    for record in load_verbatim():
        repo = _repo_of(as_text(record.get("id")) or "", repos)
        if repo is None:
            continue
        for field in PROSE_FIELDS:
            for value in as_sequence(record.get(field)):
                text = as_text(value)
                if text:
                    yield repo, text


def _quote_index(
    repos: frozenset[str],
) -> dict[tuple[str, ...], frozenset[str]]:
    """Every `MIN_QUOTE_TOKENS`-long run the non-permissive passages contain.

    Keyed by the run and carrying the repos that wrote it, so a shared run is
    looked up once and every repo it implicates is named. A run two repos both
    wrote is one finding per repo, which is what the reader needs: the same words
    from two sources are two licences to check.
    """
    built: dict[tuple[str, ...], set[str]] = {}
    for repo, passage in _passages(repos):
        tokens = _tokens(passage)
        for start in range(len(tokens) - MIN_QUOTE_TOKENS + 1):
            run = tokens[start : start + MIN_QUOTE_TOKENS]
            built.setdefault(run, set()).add(repo)
    return {run: frozenset(owners) for run, owners in built.items()}


def _shipped_text(path: Path) -> str | None:
    """A shipped file's text, or None when it cannot carry a comparable passage.

    A file that does not decode as UTF-8, or that carries a NUL byte, is not text
    a quote of prose could be written into in a form this compares token by
    token. Skipped rather than reported, matching the file-level scope of the
    check: an unreadable *data* file is `catalog-schema`'s subject, not this
    one's, and inventing a second diagnostic for it here would give one defect
    two owners.
    """
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return None if "\x00" in text else text


def _shipped_files() -> tuple[Path, ...]:
    """The files that would be committed, as absolute paths.

    Enumerated through git -- tracked, plus untracked and not ignored -- so the
    boundary is "what ships" rather than "what is in the working tree now". The
    clones under `ressources/` and the withheld store under `.local/` are outside
    it by the ignore rules, which is what stops the gate matching the store
    against itself.
    """
    root = paths.ROOT
    out: list[Path] = []
    for relative in vcs.listed_files():
        path = root / relative
        if path.is_file():
            out.append(path)
    return tuple(out)


def check_prose_gate(report: Report) -> None:
    """Every shipped artifact, against the non-permissive prose that wrote it.

    One diagnostic per artifact and repo, naming both and quoting the shared run.
    Per repo rather than per occurrence because the reader's next move is to
    decide whether *this* repo's terms were breached, and the same passage quoted
    twice is one such decision; the run is quoted so the finding can be located
    without re-running the search by hand.
    """
    repos = _non_permissive_prose_repos()
    index = _quote_index(repos)
    if not index:
        return

    for path in _shipped_files():
        text = _shipped_text(path)
        if text is None:
            continue
        tokens = _tokens(text)
        first: dict[str, int] = {}
        for start in range(len(tokens) - MIN_QUOTE_TOKENS + 1):
            owners = index.get(tokens[start : start + MIN_QUOTE_TOKENS])
            if owners:
                for repo in owners:
                    first.setdefault(repo, start)

        artifact = path.relative_to(paths.ROOT).as_posix()
        for repo in sorted(first):
            start = first[repo]
            run = " ".join(tokens[start : start + MIN_QUOTE_TOKENS])
            report.add(
                PROSE_GATE_CHECK,
                artifact,
                f"reproduces a passage from `{repo}`, whose prose terms are "
                f"`ideas_only`; ideas from it are restated in our own words and "
                f"never quoted. The shared run begins {run!r}",
            )
