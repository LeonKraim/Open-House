"""The published Store: the shapes Open House sends to it and reads back.

**The Store's backend is not written here, and that is the point.** A store needs
accounts, unique names, files, comments, ratings and a database to keep them in,
and every one of those is a thing people have already built well. What is written
here is the *client half*: which request carries a published module, what a
published row looks like when it comes back, and what Open House refuses to send.
Keeping it in `ha_adapter` -- pure, no Home Assistant, no network -- is what lets
the rules below be read and tested without a store in the room, exactly as
`pack_authoring` is pure and `dev_authoring` is not.

**The backend is PocketBase, and the reason is that it is one binary.** It is a
single Go executable on SQLite with a REST API, an admin UI, record-level auth,
unique indexes and file storage already in it, so standing the Store up is
running one file rather than operating a service. The paths and the shapes below
are PocketBase's, and they are spelled in one place -- `records_path`,
`auth_path`, `COLLECTIONS` -- so that a move to something else is a change here
rather than a change in the integration, the panel and every test.

**What Open House owns is the vocabulary.** A published module is an Open House
*module definition* (`module_definitions.to_document`) rather than a pack
manifest: it is the thing a person made, it carries its own source document so an
install never has to find a blueprint again, and it is the same document an
import already produces. The Store stores it verbatim and reads it back through
`module_definitions.from_document`, so publishing and installing cannot drift.

**A publisher's name belongs to the publisher.** One install claims one name,
once, and the backend enforces uniqueness -- so the refusal a second person gets
is "that name is taken", in words, before anything is published under it. That is
`name_refusal` for what Open House will accept at all, and `name_taken` for what
the backend says about a name somebody already has.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from . import module_definitions

#: The PocketBase collections this client talks to. Spelled once, because the
#: same three strings are a path, a relation target and a test fixture.
COLLECTIONS: Mapping[str, str] = {
    "publishers": "publishers",
    "modules": "modules",
    "ratings": "ratings",
    "comments": "comments",
    "installs": "installs",
}

#: The field each collection holds a display row's rating in, as a sum and a
#: count. Kept on the module row rather than counted per read: a store that
#: counted its own ratings on every listing would answer a page of twenty
#: modules with twenty more queries, and PocketBase's own queries are the reason
#: not to.
STARS_SUM = "stars_sum"
STARS_COUNT = "stars_count"

#: The star band the Store accepts. A rating is one to five whole stars -- no
#: zero (that is not a rating, it is silence) and no halves (a half star is a
#: thing a person reads as a bug in the arithmetic).
STARS = (1, 2, 3, 4, 5)

#: Names the Store keeps for itself, whatever a person asks for. `official` is
#: the tier a curated pack carries and `open_house` is the project, so either
#: published as a person's own name would be a claim that is not true.
RESERVED_NAMES = frozenset({"open_house", "official", "verified", "store", "admin"})

#: A publisher's name is lower case and has no spaces, because it is also the
#: part of a published module's address and of its `author` field, and two
#: spellings that differ only in case would be two names for one person.
_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9_-]{1,30})[a-z0-9]$")

NAME_MIN = 3
NAME_MAX = 32


def name_refusal(name: object) -> str | None:
    """Why this publisher name will not do, or `None` when it will.

    A sentence rather than a boolean, because the screen shows it as it is: the
    person typed a name, and "that name has a space in it" is the answer they can
    act on where "invalid" is not.
    """
    if not isinstance(name, str) or not name.strip():
        return "a publisher name cannot be empty"
    if name != name.strip():
        return "a publisher name cannot start or end with a space"
    if len(name) < NAME_MIN:
        return f"a publisher name has to be at least {NAME_MIN} characters"
    if len(name) > NAME_MAX:
        return f"a publisher name can be at most {NAME_MAX} characters"
    if name != name.lower():
        return f"{name!r} would have to be {name.lower()!r}: names are lower case"
    if name in RESERVED_NAMES:
        return f"{name!r} is a name the Store keeps for itself"
    if not _NAME.match(name):
        return (
            f"{name!r} cannot be a publisher name: use letters, digits, hyphens "
            "and underscores, starting and ending with a letter or a digit"
        )
    return None


def name_taken(body: object) -> bool:
    """Whether the backend's refusal about a name means somebody already has it.

    Read off PocketBase's own validation body rather than off the status code: a
    400 from this collection could be any field failing any rule, and treating
    every one of them as "taken" would tell a person their name was gone when
    what actually happened was that the request was malformed.

    PocketBase answers a failed unique index as
    `{"data": {"name": {"code": "validation_not_unique", ...}}}`.
    """
    if not isinstance(body, Mapping):
        return False
    data = body.get("data")
    if not isinstance(data, Mapping):
        return False
    field = data.get("name")
    if not isinstance(field, Mapping):
        return False
    return str(field.get("code") or "") == "validation_not_unique"


def stars_refusal(value: object) -> str | None:
    """Why this rating is not one, or `None` when it is."""
    if isinstance(value, bool) or not isinstance(value, int):
        return "a rating is a whole number of stars"
    if value not in STARS:
        return f"a rating is between {STARS[0]} and {STARS[-1]} stars"
    return None


# -- Paths -------------------------------------------------------------------


def records_path(base: str, collection: str) -> str:
    """The PocketBase list-and-create address of one collection."""
    return f"{_base(base)}/api/collections/{collection}/records"


def auth_path(base: str, collection: str) -> str:
    """The record-auth address of one auth collection.

    A publisher *is* an auth record: the name is the account and the password is
    the token, which is what makes "one install, one name" something the backend
    enforces rather than something this client remembers to check.
    """
    return f"{_base(base)}/api/collections/{collection}/auth-with-password"


def record_path(base: str, collection: str, record: str) -> str:
    return f"{records_path(base, collection)}/{record}"


def _base(base: str) -> str:
    return base.rstrip("/")


# -- What is published -------------------------------------------------------


@dataclass(frozen=True)
class PublishedModule:
    """One module on the Store, as the Store holds it."""

    id: str
    slug: str
    title: str
    summary: str
    #: The publisher's name, and their record id. Both, because the name is what
    #: a person reads and the id is what a "did I publish this?" check compares.
    publisher: str
    publisher_id: str
    version: str
    tier: str
    #: The review, by name, when the module is one the Store has reviewed.
    review: str
    stars_sum: int
    stars_count: int
    comments: int
    installs: int
    updated: str
    #: The module definition itself, unread here. A list of twenty modules does
    #: not need twenty documents parsed, and the one that does is the one being
    #: installed.
    document: Mapping[str, Any]

    @property
    def rating(self) -> float | None:
        """The mean rating, or `None` for a module nobody has rated.

        `None` rather than `0.0`, because zero stars is a rating and "nobody has
        said" is not -- and a screen that showed an unrated module as nothing
        out of five would be saying something no person said.
        """
        if not self.stars_count:
            return None
        return self.stars_sum / self.stars_count


@dataclass(frozen=True)
class StoreComment:
    id: str
    module: str
    publisher: str
    publisher_id: str
    body: str
    created: str


@dataclass(frozen=True)
class StoreRating:
    module: str
    publisher_id: str
    stars: int


def module_row(record: Mapping[str, Any]) -> PublishedModule:
    """One `modules` record as a row, with its publisher expanded.

    PocketBase expands a relation to its whole record when the request asks for
    it (`expand=publisher`), and this reads either shape: the id when it is not
    expanded, and the name when it is. A listing that showed an id where a name
    belongs is one the Store asked not to have.
    """
    expand = record.get("expand")
    publisher = expand.get("publisher") if isinstance(expand, Mapping) else None
    name = record.get("publisher_name")
    if not name and isinstance(publisher, Mapping):
        name = publisher.get("name")
    return PublishedModule(
        id=str(record.get("id") or ""),
        slug=str(record.get("slug") or ""),
        title=str(record.get("title") or ""),
        summary=str(record.get("summary") or ""),
        publisher=str(name or record.get("publisher") or ""),
        publisher_id=str(
            (publisher.get("id") if isinstance(publisher, Mapping) else "")
            or record.get("publisher")
            or ""
        ),
        version=str(record.get("version") or ""),
        tier=str(record.get("tier") or "community"),
        review=str(record.get("review") or ""),
        stars_sum=_count(record.get(STARS_SUM)),
        stars_count=_count(record.get(STARS_COUNT)),
        comments=_count(record.get("comments")),
        installs=_count(record.get("installs")),
        updated=str(record.get("updated") or ""),
        document=_mapping(record.get("document")),
    )


def comment_row(record: Mapping[str, Any]) -> StoreComment:
    expand = record.get("expand")
    publisher = expand.get("publisher") if isinstance(expand, Mapping) else None
    return StoreComment(
        id=str(record.get("id") or ""),
        module=str(record.get("module") or ""),
        publisher=str(
            (publisher.get("name") if isinstance(publisher, Mapping) else "")
            or record.get("publisher_name")
            or record.get("publisher")
            or ""
        ),
        publisher_id=str(
            (publisher.get("id") if isinstance(publisher, Mapping) else "")
            or record.get("publisher")
            or ""
        ),
        body=str(record.get("body") or ""),
        created=str(record.get("created") or ""),
    )


def rating_row(record: Mapping[str, Any]) -> StoreRating:
    return StoreRating(
        module=str(record.get("module") or ""),
        publisher_id=str(record.get("publisher") or ""),
        stars=_count(record.get("stars")),
    )


# -- What the panel is sent --------------------------------------------------
#
# The dataclasses above are the Store's own shape; the two functions below are
# the *panel's* (`panel/src/api/models.ts`). The two are deliberately different:
# a `PublishedModule` is what the Store holds and every reader of it wants, while
# a `PublishedRow` is one screen's view -- it drops the document (a page of
# twenty rows does not need twenty definitions parsed), and it carries `mine`,
# which is not a fact about the row at all but about who is asking. That answer
# has to be built where the asking install's identity is known, and it is pure
# arithmetic on two ids, so it is built here with the rest of what a row is.


def module_json(module: PublishedModule, *, publisher_id: str = "") -> dict[str, Any]:
    """One published module as the panel's `PublishedRow`.

    **`mine` compares the publisher's record id, not their name.** The name is a
    label that can change and the row keeps the id it was filed under
    (`publish_payload` sets `publisher` from the record for exactly this reason),
    so a person who asks the Store to call them something else keeps the button
    on the modules they actually published. `publisher_id` is the asking
    install's own id, `""` until it has claimed a name -- and the guard on it is
    load-bearing: a row the Store did not expand has an empty publisher id too,
    and `"" == ""` would otherwise mark every unexpanded row as this install's.
    """
    return {
        "id": module.id,
        "slug": module.slug,
        "title": module.title,
        "summary": module.summary,
        "publisher": module.publisher,
        "mine": bool(publisher_id) and module.publisher_id == publisher_id,
        "version": module.version,
        "tier": module.tier,
        "review": module.review,
        # `None` for a module nobody has rated, which the panel draws as "not
        # rated yet" and not as nothing out of five. See `PublishedModule.rating`.
        "rating": module.rating,
        "stars_count": module.stars_count,
        "comments": module.comments,
        "installs": module.installs,
        "updated": module.updated,
    }


def comment_json(comment: StoreComment) -> dict[str, Any]:
    """One comment as the panel's `StoreCommentRow`.

    Three of the dataclass's five fields, because the panel has no use for the
    module or the publisher's id: a comment is drawn under the module it is
    already on, and it is signed with a name.
    """
    return {
        "id": comment.id,
        "publisher": comment.publisher,
        "body": comment.body,
        "created": comment.created,
    }


def _count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


# -- What is sent ------------------------------------------------------------


def publish_payload(
    definition: module_definitions.ModuleDefinition,
    *,
    publisher_id: str,
    summary: str = "",
    review: str = "",
) -> Mapping[str, Any]:
    """The body that publishes one module.

    The definition goes as `document`, verbatim and complete, rather than as a
    manifest the Store would have to understand: what a person installs is what
    they published, and there is no second format in between to disagree with the
    first. `publisher` is set from the *record* rather than from the name so that
    a person who later asks the Store to call them something else keeps the row
    they published -- a name is a label, and the rows are theirs.
    """
    document = module_definitions.to_document(definition)
    return {
        "slug": definition.slug,
        "title": definition.title,
        "summary": summary or definition.description,
        "publisher": publisher_id,
        "version": definition.version,
        "tier": "community",
        "review": review,
        "document": dict(document),
        # Reset with every publish, and never sent by an update that is not one:
        # a version is a thing a person installs, and a counter that carried over
        # would make the new one look like the old one had been rated.
        STARS_SUM: 0,
        STARS_COUNT: 0,
        "installs": 0,
    }


def register_payload(name: str, password: str) -> Mapping[str, Any]:
    """The body that claims a publisher name.

    `password` is the name's own secret, generated by the integration and never
    shown: it is how this install proves it is the one that owns the name, so
    there is no second account to make and no email to give.
    """
    return {"name": name, "password": password, "passwordConfirm": password}


def rating_payload(module_id: str, publisher_id: str, stars: int) -> Mapping[str, Any]:
    return {"module": module_id, "publisher": publisher_id, "stars": stars}


def comment_payload(module_id: str, publisher_id: str, body: str) -> Mapping[str, Any]:
    return {"module": module_id, "publisher": publisher_id, "body": body}


def install_payload(module_id: str, publisher_id: str) -> Mapping[str, Any]:
    """The body that records one house installing one module.

    The count is what the Store shows ("1,204 installs") and the row is what
    makes it honest -- a house that installs twice is one install, and a number
    derived from an idempotent row cannot be inflated by pressing a button.
    """
    return {"module": module_id, "publisher": publisher_id}


def filter_query(*, search: str = "", sort: str = "-updated") -> Mapping[str, str]:
    """The list query a Store screen is built from.

    `sort` defaults to newest first: a Store that opened on alphabetical order
    would bury what was published this week under whatever starts with `a`.
    """
    query = {"sort": sort, "expand": "publisher"}
    if search.strip():
        query["filter"] = f"(title ~ {search!r} || summary ~ {search!r})"
    return query


def installed_split(
    modules: Sequence[PublishedModule], installed: Sequence[str]
) -> tuple[tuple[PublishedModule, ...], tuple[PublishedModule, ...]]:
    """The two lists the Store tab shows: the ones this house has, and the rest.

    Keyed by **slug**, and not by the Store's record id: a module this house
    installed from an older version of the same Store row is still installed --
    the slug is what the house calls it and what the installed set holds -- and
    matching on the id would file a person's own module under "not installed"
    the moment its publisher corrected a typo in the summary.
    """
    have = set(installed)
    here = tuple(module for module in modules if module.slug in have)
    elsewhere = tuple(module for module in modules if module.slug not in have)
    return here, elsewhere
