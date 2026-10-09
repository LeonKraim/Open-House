"""The published Store, as this house reaches it.

**The second store, and not the first.** `modules.py` keeps *this house's* store:
the definitions it offers, which is where an import lands. This is the other one
-- the server somebody else runs, that a house publishes its modules to and
installs other people's from. The two are separate because they are separate
things: a house's own store exists whether or not any published Store is
configured, and everything here is answered from a server this house has to be
given the address of.

**No address means no Store, and that is the ordinary state of every install.**
A Store is a server somebody runs; this integration cannot find one, and it does
not try. `async_client` answers `None` when no address is set, nothing here opens
a connection in that case, and every command in `websocket_api.py` answers "put
the address here" rather than reaching for a server that was never named. That is
what lets the Store tab behave exactly as it does today for the many houses that
will never set one.

**The shapes are `ha_adapter.store_api`'s, and the network is this module's.**
Which request carries a published module, what a row looks like coming back and
what this house refuses to send are all in `store_api`, pure and tested without a
store in the room, exactly as `pack_authoring` is pure and `dev_authoring` is
not. What is here is the part that has to hold a socket: paths are built into
URLs, a session is borrowed from Home Assistant (`async_get_clientsession`, so
connections are Home Assistant's to pool and close), and a failure becomes a
`StoreError` carrying a sentence a person can act on rather than a status code.

**A publisher is an identity this install claims once.** The name is the account
and a generated password is the secret, and the pair is remembered in the entry's
options (`OPTION_PUBLISHER`) so the install proves it is the one that owns the
name without an email or a second sign-up. The Store's own unique index enforces
one name per publisher, so the refusal a second person gets is the backend's and
the sentence below is built from it. The password is generated with
`secrets.token_urlsafe` and never shown.
"""

from __future__ import annotations

import json
import logging
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ha_adapter import module_definitions, store_api

__all__ = [
    "DEFAULT_URL",
    "OPTION_PUBLISHER",
    "OPTION_URL",
    "Store",
    "StoreError",
    "async_claim",
    "async_client",
    "no_store_sentence",
    "publisher_name",
    "store_url",
    "with_url",
]

_LOGGER = logging.getLogger(__name__)

#: The Store this build of Open House ships pointed at, used when no address is
#: configured. **This is the address, not a question**: it is filled in, so a
#: house talks to a Store without anybody typing one, and the panel never asks.
#: Publishing to somebody else's Store is how the published Store is meant to
#: work -- a person who wants one of their own, a self-hosted one, a test one,
#: sets the option instead and it wins.
#:
#: The value is the development Store that `store/docker-compose.yml` stands up,
#: named from *inside* the Home Assistant container (which is where the
#: integration runs, so `localhost` there is the container). It is a placeholder
#: for a real Store's address and lives in one line for exactly that reason.
#:
#: `store_url` is the only reader, which is what keeps "is an address defined?" a
#: single question.
DEFAULT_URL = "http://host.docker.internal:8090"
#: The key the Store's address lives under in a config entry's options. Empty is
#: the ordinary state, because the shipped address is what a house uses until
#: somebody sets one of their own: see the module docstring.
OPTION_URL = "store_url"
#: The key this install's claimed publisher identity lives under: a mapping of
#: `name` to the password generated for it. Written only by a claim that
#: succeeded, so a name this install does not hold is a name it has not stored.
OPTION_PUBLISHER = "store_publisher"

#: How long any one Store call may take. Short, because every one of these is on
#: the path of a person waiting for a page: a Store that is not answering should
#: be a refusal in a second rather than a screen that hangs.
_TIMEOUT_SECONDS = 10

#: The most rows one listing asks for. The Store has no pagination in this API --
#: the tab draws two lists, not pages of them -- so this is the whole of what a
#: browse returns, and it is generously above the page a person actually reads.
_PAGE = 200

#: The token and publisher id of an authenticated identity, by `(url, name)`.
#:
#: Cached at module level rather than on the frozen `Store`, because the token is
#: not a property of one `Store` value -- the option-driven client is rebuilt on
#: every request -- but of the *identity* the whole process is using. A 401 drops
#: the entry and the next call authenticates again, so an expired token costs one
#: extra round trip and nothing a person sees.
_IDENTITIES: dict[tuple[str, str], tuple[str, str]] = {}


class StoreError(Exception):
    """The Store could not be reached, or refused what was asked of it.

    Every failure out of this module is one of these, carrying a sentence about
    the Store and the house rather than about an HTTP status: "put the address in
    the settings" and "that name is taken, pick another" are the two things that
    actually happen, and both are things a person can go and fix.
    """


def store_url(options: Mapping[str, Any]) -> str:
    """The Store's address: the configured one, or the one this build ships with.

    A configured address always wins, so a person running their own Store is not
    overruled by a `DEFAULT_URL` that was chosen for people who are not. `""` out
    of here means the address is defined *nowhere*, which is the only state the
    panel has to ask about.
    """
    return str(options.get(OPTION_URL) or "").strip() or DEFAULT_URL


def publisher_name(options: Mapping[str, Any]) -> str:
    """This install's claimed publisher name, or `""` until it has claimed one."""
    return str(_publisher(options).get("name") or "")


def with_url(options: Mapping[str, Any], url: str) -> dict[str, Any]:
    """An entry's options with the Store's address set to `url`.

    **A changed address forgets the claimed name**, and this is the one place that
    rule is written. A publisher identity belongs to the Store that issued it: a
    claim carried across to a different Store authenticates as a stranger -- or,
    on a Store where somebody else holds the same name, *as that person*, which is
    the one thing the uniqueness of a name exists to prevent. So changing the
    address drops the claim and the tab asks for a name again, which is the screen
    that can settle who this house is on the new Store.

    Left as a plain function rather than a method because both writers need it --
    the options flow (`config_flow.OpenHouseOptionsFlow`) and the panel's own
    `published/configure` command -- and two copies of this rule would be one copy
    per way to get it wrong.
    """
    merged = {**options, OPTION_URL: url.strip()}
    was = str(options.get(OPTION_URL) or "").strip()
    if was != merged[OPTION_URL]:
        merged.pop(OPTION_PUBLISHER, None)
    return merged


def no_store_sentence() -> str:
    """Where to set the address, in the words every refusal uses.

    One phrase, because it is written into every command's refusal and a person
    who reads it twice should read the same place twice. It is reachable only in
    a build whose `DEFAULT_URL` is empty -- a house that has an address anywhere
    never sees it -- so it names the one door that is always there.
    """
    return (
        "no published Store is set for this house: put its address in Settings > "
        "Devices & Services > Open House > Configure, and leave it empty to go on "
        "without a Store"
    )


def _publisher(options: Mapping[str, Any]) -> Mapping[str, Any]:
    """The stored publisher pair, read defensively: an absent or wrong-shaped one
    is "no publisher", which is what an install that has never claimed has."""
    held = options.get(OPTION_PUBLISHER)
    return held if isinstance(held, Mapping) else {}


def _parsed(text: str) -> dict[str, Any]:
    """A response body as a mapping, however badly the Store behaved.

    Never raises: an answer that is not JSON -- a proxy's HTML error page, an
    empty body -- becomes `{"message": ...}`, so a caller that only reads
    `status` and `message` is reading a mapping either way. See `_send`.
    """
    if not text:
        return {}
    try:
        answer = json.loads(text)
    except ValueError:
        return {"message": " ".join(text.split())[:200]}
    return answer if isinstance(answer, dict) else {"data": answer}


def _items(body: object) -> list[Mapping[str, Any]]:
    """The rows out of a PocketBase list answer, and nothing else."""
    items = body.get("items") if isinstance(body, Mapping) else None
    if not isinstance(items, list):
        return []
    return [row for row in items if isinstance(row, Mapping)]


def _said(body: object) -> str:
    """What the Store said, as one line short enough for a panel row."""
    message = body.get("message") if isinstance(body, Mapping) else None
    if not message:
        return "no reason given"
    return " ".join(str(message).split())[:200]


def _refuse(status: int, body: object, doing: str) -> None:
    """Raise the refusal a failed call deserves, or return when it did not fail.

    The verb is the caller's ("publishing a module"), so the sentence says what
    was being done rather than which path was called -- the path is what the
    Store's own `message` is for.
    """
    if status >= 400:
        raise StoreError(f"the Store refused {doing} ({status}): {_said(body)}")


@dataclass(frozen=True)
class Store:
    """One published Store, as this house reaches it."""

    url: str
    hass: HomeAssistant
    #: This install's claimed name and its secret, or both empty until it has
    #: claimed one. The password is generated and never shown; see the module
    #: docstring.
    name: str = ""
    password: str = ""

    # -- The wire -----------------------------------------------------------

    async def _send(
        self,
        method: str,
        url: str,
        payload: Any = None,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        """One HTTP call, answered with its status and parsed body.

        **The address arrives whole, and is not assembled here.** Every caller
        builds it with `ha_adapter.store_api` -- which is the one place that
        knows a Store's URL shape -- so joining it to `self.url` again would be
        exactly the mistake of prefixing a URL that already has its prefix, and
        every call would go to `https://store.examplehttps://store.example/...`.

        **A status the Store chose is not an error here.** A 400 carries the
        body that says *why* -- a name already taken, a row that would clash --
        and two of the callers below read that body to decide what to do instead
        of giving up, so the status is returned rather than raised. What *is*
        raised is a call that never arrived: a refused connection, a timeout, a
        host that does not resolve. Those are the same storey to a person
        ("could not be reached"), and none of them has a body worth reading.
        """
        session = async_get_clientsession(self.hass)
        all_headers = {"Accept": "application/json"}
        if payload is not None:
            all_headers["Content-Type"] = "application/json"
        all_headers.update(headers or {})
        try:
            async with session.request(
                method,
                url,
                json=payload,
                headers=all_headers,
                params=params,
                timeout=_TIMEOUT_SECONDS,
            ) as response:
                text = await response.text()
                status = response.status
        except Exception as failure:
            raise StoreError(
                f"the Store at {self.url} could not be reached: {failure}"
            ) from failure
        return status, _parsed(text)

    async def _identity(self, *, refresh: bool = False) -> tuple[str, str]:
        """This install's token and publisher id, authenticating when needed.

        `("", "")` for a house that has claimed no name: there is nothing to
        authenticate as, and the public reads below (a listing, a module's
        comments) are answered without one. A name that *was* claimed but the
        Store no longer accepts is a `StoreError`, because the difference between
        "not claimed" and "claimed and gone" decides whether a person claims a
        name or finds out what happened to the one they had.
        """
        if not self.name or not self.password:
            return ("", "")
        key = (self.url, self.name)
        if not refresh:
            held = _IDENTITIES.get(key)
            if held is not None:
                return held
        path = store_api.auth_path(self.url, store_api.COLLECTIONS["publishers"])
        status, body = await self._send(
            "POST",
            path,
            {"identity": self.name, "password": self.password},
        )
        if status >= 400:
            raise StoreError(
                f"the Store no longer recognises this house's publisher name "
                f"{self.name!r} ({status}): claim it again on the Store tab"
            )
        token = str(body.get("token") or "")
        record = body.get("record")
        publisher_id = (
            str(record.get("id") or "") if isinstance(record, Mapping) else ""
        )
        _IDENTITIES[key] = (token, publisher_id)
        return (token, publisher_id)

    async def request(
        self,
        method: str,
        path: str,
        payload: Any = None,
        *,
        params: Mapping[str, Any] | None = None,
        auth: bool = True,
    ) -> tuple[int, dict[str, Any]]:
        """One call, with this install's token on it, sent again once on a 401.

        **A 401 is a stale token, not a refusal.** The Store's token has a
        lifetime and this install may hold it across one; re-authenticating once
        and repeating the call is what makes an expired token invisible, where
        handing the 401 back would make a person re-configure something that was
        never wrong. A second 401 is passed back, because then it is not the
        token and retrying it again would only be a loop.
        """
        headers: dict[str, str] = {}
        if auth:
            token = (await self._identity())[0]
            if token:
                headers["Authorization"] = token
        status, body = await self._send(
            method, path, payload, params=params, headers=headers
        )
        if status == 401 and auth and self.name:
            token = (await self._identity(refresh=True))[0]
            headers = {"Authorization": token} if token else {}
            status, body = await self._send(
                method, path, payload, params=params, headers=headers
            )
        return status, body

    async def _required_publisher(self) -> str:
        """The publisher id, or a refusal saying a name has to be claimed first.

        Every write below this line is made *as somebody* -- the Store records
        who published, rated and commented -- and a house that has not claimed a
        name cannot be that somebody. The sentence says which screen to go to,
        because that is the one act that makes the call possible.
        """
        publisher_id = (await self._identity())[1]
        if not publisher_id:
            raise StoreError(
                "this house has not claimed a publisher name on the Store, so "
                "there is nothing to publish or rate as: claim a name on the "
                "Store tab first"
            )
        return publisher_id

    async def publisher_id(self) -> str:
        """This install's publisher record id on the Store, or `""` when none.

        What `mine` is read from (`store_api.module_json`), exposed rather than
        baked into `browse`, because the split into installed and not belongs to
        the caller and both halves need the same id to draw.
        """
        return (await self._identity())[1]

    # -- What is read and written -------------------------------------------

    async def browse(self, search: str = "") -> tuple[store_api.PublishedModule, ...]:
        """The Store's modules, newest first, optionally narrowed by a search.

        The listing is public, so it is read with whatever token this install
        happens to hold and works with none. The rows are the Store's own
        (`store_api.module_row`); which of them this house already has is the
        caller's question, answered against the house's definitions.
        """
        # `dict[str, Any]` and not the mapping's own `str`-valued type: `perPage`
        # is a number here and a query string on the wire, and typing the copy
        # from `filter_query` would make the one non-string parameter a type
        # error for saying what it is.
        params: dict[str, Any] = dict(store_api.filter_query(search=search))
        params["perPage"] = _PAGE
        path = store_api.records_path(self.url, store_api.COLLECTIONS["modules"])
        status, body = await self.request("GET", path, params=params)
        _refuse(status, body, "reading the Store")
        return tuple(store_api.module_row(record) for record in _items(body))

    async def publish(
        self, definition: module_definitions.ModuleDefinition, summary: str = ""
    ) -> Mapping[str, Any]:
        """Publish one of this house's definitions, and answer with the row.

        **The Store allows one module per publisher per slug, so this is a
        publish *or* an update, and it decides which by looking.** The row this
        install published before is found by `(publisher, slug)`, and an existing
        one is *patched* rather than posted again -- the button appears on a row
        already marked `mine`, so a second press is a person updating what they
        published, and a create-only write would answer every such press with the
        backend's unique-index refusal.

        **An update keeps the counters.** The rating, the comment count and the
        install count are facts about the module as people have met it, and a
        publisher correcting a summary has not unrated their module; so the
        rating fields `publish_payload` resets for a *new* publish are left out of
        an update. A first publish resets them, which is right: a new module has
        been met by nobody.
        """
        publisher_id = await self._required_publisher()
        path = store_api.records_path(self.url, store_api.COLLECTIONS["modules"])
        held = await self.request(
            "GET",
            path,
            params={
                "perPage": 1,
                "filter": f"(publisher='{publisher_id}' && slug='{definition.slug}')",
            },
        )
        _refuse(held[0], held[1], "reading your published modules")
        rows = _items(held[1])
        payload = dict(
            store_api.publish_payload(
                definition, publisher_id=publisher_id, summary=summary
            )
        )
        expand = {"expand": "publisher"}
        if rows:
            record = store_api.record_path(
                self.url, store_api.COLLECTIONS["modules"], str(rows[0].get("id") or "")
            )
            for counter in (store_api.STARS_SUM, store_api.STARS_COUNT, "installs"):
                payload.pop(counter, None)
            status, body = await self.request("PATCH", record, payload, params=expand)
            _refuse(status, body, "updating a published module")
        else:
            status, body = await self.request("POST", path, payload, params=expand)
            _refuse(status, body, "publishing a module")
        return store_api.module_json(
            store_api.module_row(body), publisher_id=publisher_id
        )

    async def install(self, record_id: str) -> store_api.PublishedModule:
        """One published module, with its document, ready to import.

        The document travels whole (`modules.async_import_definition` reads it
        back as a definition), which is why the row here is the Store's own
        rather than the panel's -- the panel's row has no document in it, and it
        is the one thing installing needs.
        """
        path = store_api.record_path(
            self.url, store_api.COLLECTIONS["modules"], record_id
        )
        status, body = await self.request("GET", path, params={"expand": "publisher"})
        _refuse(status, body, "reading a published module")
        return store_api.module_row(body)

    async def record_install(self, record_id: str) -> None:
        """Tell the Store this house now has this module, and say nothing else.

        The one call here that is best-effort, and deliberately so: the module is
        *already* in this house by the time this runs, so the worst a failure can
        mean is a count that did not move -- and passing that on would tell
        somebody their install failed when it did not. The two ways it quietly
        does nothing are ordinary: a house that has claimed no name has no
        identity to record as, and one that has installed this module before meets
        the Store's `(module, publisher)` index, which is the same row said twice.
        """
        publisher_id = await self.publisher_id()
        if not publisher_id:
            return
        path = store_api.records_path(self.url, store_api.COLLECTIONS["installs"])
        await self.request(
            "POST", path, store_api.install_payload(record_id, publisher_id)
        )

    async def rate(self, record_id: str, stars: int) -> Mapping[str, Any]:
        """Rate a published module, and answer with the module's new average.

        **One person, one rating, and the Store keeps it that way.** A second
        rating of the same module is the same rating changed, so the row this
        install already has on `(module, publisher)` is looked for first and
        *patched* if it is there -- rather than posting and reading the backend's
        unique-index refusal, which would show a person pressing a star a second
        time an error for doing the thing the screen invited. The rating this
        install gave its own module is refused by the Store itself, because an
        average that included its subject's own vote is a number nobody said.
        """
        publisher_id = await self._required_publisher()
        ratings = store_api.records_path(self.url, store_api.COLLECTIONS["ratings"])
        held = await self.request(
            "GET",
            ratings,
            params={
                "perPage": 1,
                "filter": f"(module='{record_id}' && publisher='{publisher_id}')",
            },
        )
        _refuse(held[0], held[1], "reading your rating")
        rows = _items(held[1])
        if rows:
            path = store_api.record_path(
                self.url, store_api.COLLECTIONS["ratings"], str(rows[0].get("id") or "")
            )
            status, body = await self.request("PATCH", path, {"stars": stars})
            _refuse(status, body, "changing your rating")
        else:
            status, body = await self.request(
                "POST",
                ratings,
                store_api.rating_payload(record_id, publisher_id, stars),
            )
            _refuse(status, body, "rating a module")
        return await self._rating_of(record_id)

    async def _rating_of(self, record_id: str) -> Mapping[str, Any]:
        """A module's rating and how many people gave it, as the Store now has it.

        Read back from the module row rather than computed here, because the sum
        and the count are the backend's (`store/pb_hooks` moves them) and a client
        that added a delta would be a second arithmetic free to drift from the
        one the next listing is drawn from.
        """
        path = store_api.record_path(
            self.url, store_api.COLLECTIONS["modules"], record_id
        )
        status, body = await self.request("GET", path)
        _refuse(status, body, "reading a module's rating")
        row = store_api.module_row(body)
        return {"rating": row.rating, "stars_count": row.stars_count}

    async def comments(self, record_id: str) -> tuple[store_api.StoreComment, ...]:
        """What people have said about one module, oldest first.

        Oldest first because a comment list is a conversation and reads from the
        top: a new comment lands at the end of it, which is where the person who
        just wrote it looks.
        """
        path = store_api.records_path(self.url, store_api.COLLECTIONS["comments"])
        status, body = await self.request(
            "GET",
            path,
            params={
                "perPage": _PAGE,
                "filter": f"module='{record_id}'",
                "expand": "publisher",
                "sort": "created",
            },
        )
        _refuse(status, body, "reading the comments")
        return tuple(store_api.comment_row(record) for record in _items(body))

    async def comment(
        self, record_id: str, text: str
    ) -> tuple[store_api.StoreComment, ...]:
        """Say something about one module, and answer with the whole list.

        The list rather than the one row, because the screen it was posted from
        draws the list and would otherwise ask for it again the moment the write
        landed.
        """
        publisher_id = await self._required_publisher()
        path = store_api.records_path(self.url, store_api.COLLECTIONS["comments"])
        status, body = await self.request(
            "POST", path, store_api.comment_payload(record_id, publisher_id, text)
        )
        _refuse(status, body, "leaving a comment")
        return await self.comments(record_id)

    async def claim(self, name: str) -> str:
        """Claim a publisher name, and answer with the name that was claimed.

        The name's own merit is the caller's check (`store_api.name_refusal`),
        done before a request is spent; what is left is the one thing only the
        backend can say, which is that somebody already has it. The password
        comes from `self`, because a claim is the act of *becoming* an identity
        and the caller built this `Store` around the fresh pair for exactly that.
        """
        path = store_api.records_path(self.url, store_api.COLLECTIONS["publishers"])
        status, body = await self.request(
            "POST",
            path,
            store_api.register_payload(name, self.password),
            auth=False,
        )
        if status >= 400:
            if store_api.name_taken(body):
                raise StoreError(
                    f"that name is taken: somebody has already claimed {name!r} on "
                    "the Store, so pick another name"
                )
            raise StoreError(
                f"the Store refused the name {name!r} ({status}): {_said(body)}"
            )
        return str(body.get("name") or name)


async def async_client(hass: HomeAssistant, options: Mapping[str, Any]) -> Store | None:
    """The Store this house talks to, or `None` when no address is set.

    **`None` and not an error, and nothing outward happens for it.** A house that
    has never heard of the Store is the ordinary state of every install, and the
    command that asked is answered with where to set an address rather than a
    process that reaches for a server nobody named. That is the whole reason this
    is a separate function from the `Store` itself: the *absence* of a Store is a
    value here, so every caller has one place to see it.
    """
    url = store_url(options)
    if not url:
        return None
    publisher = _publisher(options)
    return Store(
        url=url,
        hass=hass,
        name=str(publisher.get("name") or ""),
        password=str(publisher.get("password") or ""),
    )


async def async_claim(hass: HomeAssistant, entry: Any, name: str) -> str:
    """Claim a publisher name for this install, once, and remember the pair.

    **The name is tried before it is stored.** The options are written only after
    the Store has accepted the name, so a claim that was refused -- somebody else
    has it, the Store is down -- leaves this install holding nothing, and a name
    it does not in fact own is exactly what must never be written into the
    options as though it were. The password the Store is given is generated here
    with `secrets.token_urlsafe` and never shown: a person claims a *name*, and
    the secret is the machinery that lets this install prove it is the one that
    claimed it, which is what makes one install one name the backend's rule
    rather than this client's memory.
    """
    refusal = store_api.name_refusal(name)
    if refusal is not None:
        raise StoreError(refusal)
    client = await async_client(hass, entry.options)
    if client is None:
        raise StoreError(no_store_sentence())
    password = secrets.token_urlsafe(32)
    # A `Store` built around the pair being claimed, so `claim` sends the very
    # secret that will be stored -- there is no second place a password is made.
    claimant = Store(url=client.url, hass=hass, name=name, password=password)
    await claimant.claim(name)
    options = dict(entry.options)
    options[OPTION_PUBLISHER] = {"name": name, "password": password}
    hass.config_entries.async_update_entry(entry, options=options)
    # The identity authenticates fine, but this process has not authenticated it
    # yet: drop any stale cache for the name so the next call does.
    _IDENTITIES.pop((client.url, name), None)
    return name
