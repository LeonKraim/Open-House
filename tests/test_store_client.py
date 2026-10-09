"""The published Store client, against a Store that answers from a script.

`ha_adapter.store_api` holds the shapes and the rules and is tested without a
socket; `custom_components/open_house/store.py` is the half that holds one, and
what it can be wrong about is different: it opens a connection when it should
not, it writes a name into the options that it never claimed, and it reads a
PocketBase answer as the wrong shape. None of those need a real server to catch
-- a fake session that answers with the bodies the probe saw is enough -- so this
file holds no PocketBase and makes no request anywhere.

**The module is loaded against a stub.** This suite deliberately does not have
Home Assistant installed -- nothing in the repository's checks imports the
integration, because the integration only means anything inside a running home
-- and `store.py` imports two names from it. So the same three points are stubbed
and the module is loaded from its path, exactly as `tests/test_node_red.py` loads
`node_red.py`, and the package's `__init__` (which registers the panel and every
websocket command) never runs. What is stubbed is only what a type annotation and
a session helper would have been; `ha_adapter` is imported for real, because the
shapes under test *are* its.

**The fake session is duck-typed, not a real one.** What is under test is which
request is made, with what body, and what is read back out of the answer -- none
of which depends on a pool, a socket or a timeout. The one thing the fake exists
to make checkable is the answer this file is *given*: the bodies below are the
ones the live probe returned, `expand` and all.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from ha_adapter import store_api

#: The session the stubbed `async_get_clientsession` hands back. A one-slot list
#: rather than a global the loader closes over, because the stub is bound into
#: `store`'s namespace before any test runs and has to read whatever the *test*
#: just put here.
_SESSION: list[Any] = [None]


def _session_for(hass: Any) -> Any:
    """What stands in for `async_get_clientsession` while `store` is loaded."""
    return _SESSION[0]


#: The names `custom_components/open_house/store.py` imports at its own top
#: level, and what stands in for each while it is loaded. Only the two Home
#: Assistant names are stubbed: `ha_adapter` is a real package and the shapes
#: under test are its.
_STANDING_IN: tuple[tuple[str, dict[str, object]], ...] = (
    ("homeassistant", {}),
    ("homeassistant.core", {"HomeAssistant": object}),
    ("homeassistant.helpers", {}),
    ("homeassistant.helpers.aiohttp_client", {"async_get_clientsession": _session_for}),
)


def _load_store() -> types.ModuleType:
    """`custom_components/open_house/store.py`, without the package around it.

    Loaded by path, like `tests/test_node_red.py` loads `node_red.py`, so the
    package's `__init__` never runs -- it registers the websocket commands, and
    importing it would make this file fail on whatever the *next* Home Assistant
    API it reaches for is called.

    **`sys.modules` is put back exactly as it was found.** pytest imports every
    test module before running any test, so these stubs would still be in place
    when a later module asks `importlib.util.find_spec("homeassistant")` over the
    checkout, which raises rather than answering. The module is loaded with the
    stubs in place and the previous state restored in a `finally`, which is
    enough: `store` binds these names into its own namespace while it loads, so
    it does not read `sys.modules` again afterwards.
    """
    before = {name: sys.modules.get(name) for name, _ in _STANDING_IN}
    for name, attributes in _STANDING_IN:
        module = types.ModuleType(name)
        for key, value in attributes.items():
            setattr(module, key, value)
        sys.modules[name] = module

    path = (
        Path(__file__).resolve().parents[1]
        / "custom_components"
        / "open_house"
        / "store.py"
    )
    try:
        spec = importlib.util.spec_from_file_location(
            "custom_components.open_house.store", path
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        # Registered while it loads, because `@dataclass` reads the defining
        # module back out of `sys.modules` -- and taken out again below, so a
        # module this file loaded by hand is not one an import would find.
        before[spec.name] = sys.modules.get(spec.name)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    finally:
        for name, previous in before.items():
            if previous is None:
                del sys.modules[name]
            else:
                sys.modules[name] = previous
    return module


store_module = _load_store()


class _FakeResponse:
    """One canned answer, in the shape `store._send` reads it.

    `store` uses `session.request(...)` as an async context manager and reads
    `.status` and `await .text()`, so this provides exactly those and nothing an
    aiohttp response has that the client never touches.
    """

    def __init__(self, status: int, body: Any) -> None:
        self.status = status
        self._text = json.dumps(body)

    async def text(self) -> str:
        return self._text

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *exception: object) -> bool:
        return False


class _FakeSession:
    """A Store that answers from a queue of bodies and remembers being asked.

    The answers are popped in order, which is enough because the calls under test
    are a short fixed sequence -- an auth, then a read -- and what each one was
    given is asserted on `calls` rather than guessed at from the order.
    """

    def __init__(self, answers: list[tuple[int, Any]]) -> None:
        self.answers = list(answers)
        self.calls: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        json: Any = None,
        headers: Any = None,
        params: Any = None,
        timeout: Any = None,
    ) -> _FakeResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "json": json,
                "headers": headers,
                "params": params,
            }
        )
        status, body = self.answers.pop(0) if self.answers else (200, {})
        return _FakeResponse(status, body)


class _Entry:
    """A config entry, as `store` reads and writes it: options, and nothing else."""

    def __init__(self, options: dict[str, Any]) -> None:
        self.options = dict(options)


class _Hass:
    """A Home Assistant, as `store.async_claim` writes through it.

    Only `config_entries.async_update_entry(entry, options=...)` is reached, and
    it is recorded here so a test can say a refused claim wrote nothing.
    """

    def __init__(self) -> None:
        self.written: list[dict[str, Any]] = []
        self.config_entries = self

    def async_update_entry(self, entry: _Entry, *, options: dict[str, Any]) -> None:
        entry.options = dict(options)
        self.written.append(dict(options))


def _use(session: _FakeSession) -> _FakeSession:
    """Make `store`'s stubbed session helper hand back this fake."""
    _SESSION[0] = session
    # The identity is cached at module level and keyed by `(url, name)`, so a
    # token one test obtained would otherwise be handed to the next.
    store_module._IDENTITIES.clear()
    return session


# -- No address, no Store ----------------------------------------------------


def test_no_address_means_no_store_and_no_connection() -> None:
    """The ordinary state of every install, and the reason it costs nothing.

    A house that has never heard of the Store must not have anything opened for
    it: no session is built, no request is made, and every caller is handed
    `None` so it has one place to see that the Store simply is not there.
    """
    session = _use(_FakeSession([]))
    assert asyncio.run(store_module.async_client(None, {})) is None
    # A blank address is no address, whatever whitespace it arrived with.
    assert asyncio.run(store_module.async_client(None, {"store_url": "   "})) is None
    assert session.calls == []


def test_the_refusal_names_where_the_address_goes() -> None:
    """Every command says the same place when nothing is configured.

    The address is the integration's own option, so the sentence has to name the
    integration's own Configure step -- a person who reads "no Store is set" and
    is not told where to set one has been told nothing they can act on.
    """
    assert "Settings > Devices & Services > Open House > Configure" in (
        store_module.no_store_sentence()
    )


def test_claiming_with_no_address_is_refused_rather_than_posted() -> None:
    """A claim needs an address as much as a read does, and says so in words."""
    entry = _Entry({})
    hass = _Hass()
    with pytest.raises(store_module.StoreError) as refused:
        asyncio.run(store_module.async_claim(hass, entry, "marqbarq"))
    assert store_module.no_store_sentence() in str(refused.value)
    assert hass.written == []


# -- Claiming a name ---------------------------------------------------------


def test_a_name_refused_on_its_merits_is_refused_before_any_request() -> None:
    """The backend's uniqueness refusal is a round trip and is not needed here.

    A name with a capital in it is the client's own rule (`store_api.name_refusal`),
    so the round trip that would only echo the rule back is not spent, and the
    sentence the person gets is the one that says what to change.
    """
    session = _use(_FakeSession([]))
    entry = _Entry({"store_url": "https://store.example"})
    hass = _Hass()
    with pytest.raises(store_module.StoreError) as refused:
        asyncio.run(store_module.async_claim(hass, entry, "MarqBarq"))
    assert "lower case" in str(refused.value)
    assert session.calls == []
    assert hass.written == []


def test_a_name_somebody_already_has_is_refused_with_the_stores_own_word() -> None:
    """A taken name and a malformed one are the same shape of answer, in words.

    "that name is taken" is what happened and "pick another name" is what to do
    about it, and the screen renders the sentence as it is -- so both halves have
    to be in it. The name is *not* stored: a claim that was refused leaves this
    install holding nothing, because a name it does not own must never be written
    into the options as though it were.
    """
    taken = {
        "data": {"name": {"code": "validation_not_unique", "message": "must be unique"}}
    }
    _use(_FakeSession([(400, taken)]))
    entry = _Entry({"store_url": "https://store.example"})
    hass = _Hass()
    with pytest.raises(store_module.StoreError) as refused:
        asyncio.run(store_module.async_claim(hass, entry, "marqbarq"))
    message = str(refused.value)
    assert "that name is taken" in message
    assert "pick another name" in message
    assert store_module.OPTION_PUBLISHER not in entry.options
    assert hass.written == []


def test_a_claim_that_succeeds_stores_a_generated_secret_and_keeps_the_rest() -> None:
    """The pair that makes one install one name, written only once it is owned.

    The password is generated here and never shown, and the Store is sent the
    very one that gets stored -- there is no second place a password is made, and
    a claim that stored a different secret from the one it registered would lock
    the install out of its own name. The address already in the options is left
    where it was: a claim is about the name, not the address.
    """
    session = _use(_FakeSession([(200, {"name": "marqbarq", "id": "pub1"})]))
    entry = _Entry({"store_url": "https://store.example"})
    hass = _Hass()
    claimed = asyncio.run(store_module.async_claim(hass, entry, "marqbarq"))

    assert claimed == "marqbarq"
    assert len(hass.written) == 1
    held = entry.options[store_module.OPTION_PUBLISHER]
    assert held["name"] == "marqbarq"
    assert held["password"]
    assert entry.options["store_url"] == "https://store.example"
    # The registered name is the claimed one, sent to the publisher collection.
    posted = [call for call in session.calls if call["method"] == "POST"]
    assert posted and posted[0]["json"]["name"] == "marqbarq"
    assert posted[0]["json"]["passwordConfirm"] == held["password"]


# -- Rows, from the Store's own shape ---------------------------------------


def _module_record(
    *, record_id: str, slug: str, publisher_id: str, name: str
) -> dict[str, Any]:
    """One `modules` record as PocketBase returns it with the publisher expanded."""
    return {
        "id": record_id,
        "slug": slug,
        "title": slug.replace("_", " ").title(),
        "summary": "Brings the lights down at dusk.",
        "publisher": publisher_id,
        "version": "2.1.0",
        "tier": "community",
        "stars_sum": 21,
        "stars_count": 5,
        "comments": 3,
        "installs": 104,
        "updated": "2026-10-01 12:00:00.000Z",
        "document": {"slug": slug},
        "expand": {"publisher": {"id": publisher_id, "name": name}},
    }


def test_a_reading_becomes_the_panel_s_row_with_mine_from_this_install_s_id() -> None:
    """The two facts the screen cannot work out for itself, from a real record.

    `rating` comes off the record's own sum and count, because the backend keeps
    them (`store/pb_hooks` moves them) and a client that added a delta would be a
    second arithmetic free to drift. `mine` compares the row's publisher *id* to
    this install's, which is why the auth answer's `record.id` is read and kept:
    the id is the one fact that does not change when a person renames themselves.
    """
    mine = _module_record(
        record_id="m1", slug="evening_lighting", publisher_id="pub1", name="marqbarq"
    )
    theirs = _module_record(
        record_id="m2", slug="bedtime", publisher_id="pub2", name="somebodyelse"
    )
    _use(
        _FakeSession(
            [
                (200, {"token": "tok", "record": {"id": "pub1", "name": "marqbarq"}}),
                (200, {"items": [mine, theirs], "totalItems": 2}),
            ]
        )
    )
    client = asyncio.run(
        store_module.async_client(
            None,
            {
                "store_url": "https://store.example",
                "store_publisher": {"name": "marqbarq", "password": "s"},
            },
        )
    )
    assert client is not None

    rows = asyncio.run(client.browse())
    publisher_id = asyncio.run(client.publisher_id())
    assert publisher_id == "pub1"

    sent = [store_api.module_json(row, publisher_id=publisher_id) for row in rows]
    assert [row["mine"] for row in sent] == [True, False]
    assert sent[0]["rating"] == 4.2
    assert sent[0]["stars_count"] == 5
    assert sent[0]["installs"] == 104
    assert sent[0]["publisher"] == "marqbarq"
    # The document is the Store's to hold and is not in a listing row.
    assert "document" not in sent[0]


def test_a_row_the_store_did_not_expand_is_not_marked_mine() -> None:
    """`"" == ""` is the trap: an unexpanded row has no publisher id at all.

    A listing that asked for no expansion would answer every row with an empty
    publisher id, and a `mine` that compared them without a guard would put a
    publish button on every module in the Store.
    """
    unexpanded = {
        "id": "m1",
        "slug": "evening_lighting",
        "title": "Evening lighting",
        "stars_sum": 0,
        "stars_count": 0,
    }
    _use(_FakeSession([(200, {"items": [unexpanded]})]))
    client = asyncio.run(store_module.async_client(None, {"store_url": "https://s"}))
    assert client is not None

    rows = asyncio.run(client.browse())
    sent = store_api.module_json(rows[0])
    assert sent["mine"] is False
    assert sent["rating"] is None


# -- The count an install moves ---------------------------------------------


def test_an_install_is_counted_as_the_house_that_claimed_the_name() -> None:
    """The number beside a published module is houses that have it, so it is written.

    The row is posted as the publisher the Store knows this install to be -- the
    Store's own rule refuses a nameless row, and `mine` on every listing is the
    same id -- so the count a person reads is the count of houses that took the
    module up rather than of houses that asked.
    """
    session = _use(
        _FakeSession(
            [
                (200, {"token": "tok", "record": {"id": "pub1", "name": "marqbarq"}}),
                (200, {"id": "i1", "module": "m1", "publisher": "pub1"}),
            ]
        )
    )
    client = asyncio.run(
        store_module.async_client(
            None,
            {
                "store_url": "https://store.example",
                "store_publisher": {"name": "marqbarq", "password": "s"},
            },
        )
    )
    assert client is not None
    asyncio.run(client.record_install("m1"))

    posted = [call for call in session.calls if "/installs" in str(call["url"])]
    assert len(posted) == 1
    assert posted[0]["method"] == "POST"
    assert posted[0]["json"] == {"module": "m1", "publisher": "pub1"}


def test_a_call_goes_to_one_address_rather_than_two_joined_together() -> None:
    """The Store's address arrives whole from `store_api` and is sent as it is.

    Every path helper takes the base and returns the whole address, so the
    transport must send that rather than join the base to it again: a doubled
    `https://store.examplehttps://store.example/api/...` is a host that does not
    exist, and a fake session that answers whatever it is asked would never have
    said so. The trailing slash is here because the base is a person's typing and
    `https://store.example/` is the same Store as without it.
    """
    session = _use(
        _FakeSession(
            [
                (200, {"token": "tok", "record": {"id": "pub1", "name": "marqbarq"}}),
                (200, {"items": [], "totalItems": 0}),
            ]
        )
    )
    client = asyncio.run(
        store_module.async_client(
            None,
            {
                "store_url": "https://store.example/",
                "store_publisher": {"name": "marqbarq", "password": "s"},
            },
        )
    )
    assert client is not None
    asyncio.run(client.browse())

    assert [call["url"] for call in session.calls] == [
        "https://store.example/api/collections/publishers/auth-with-password",
        "https://store.example/api/collections/modules/records",
    ]


def test_a_house_with_no_name_counts_nothing_and_asks_the_store_nothing() -> None:
    """Nothing to record as, so nothing is said -- and no call is made to say it.

    A house that browses without claiming a name can still install, and the
    counter is not worth a request that the Store's own rules would refuse.
    """
    session = _use(_FakeSession([]))
    client = asyncio.run(
        store_module.async_client(None, {"store_url": "https://store.example"})
    )
    assert client is not None
    asyncio.run(client.record_install("m1"))
    assert session.calls == []
