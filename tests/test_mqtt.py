"""The name this client gives the broker, and why it may not be a shared one.

There is no broker and no socket here, because the defect this covers was never
in the bytes on the wire. It was in the client id: one fixed name, so that every
connection claimed the same session. A broker treats a second connection under a
held id as the first one leaving -- it disconnects the older -- which is the
right rule for a device reconnecting and the wrong one for a bench where the
fleet, the stimulus and a `--clear` run are three clients on one machine.

The fleet is the one that serves for a whole run, so the fleet was the one
dropped. It saw the eviction as the broker closing on it mid-loop, raised, and
died -- and the walk that was watching the house went on driving a broker that
nothing was reflecting any more, which is a failure that reads as the *panel*
not answering.

What can be checked without a broker is the property the drop came from: no two
clients share a name, and a caller that names its own still gets it.
"""

from __future__ import annotations

from tools.ha.mqtt import Client


def test_two_clients_do_not_share_a_name() -> None:
    first = Client("localhost")
    second = Client("localhost")
    assert first.client_id != second.client_id, (
        "two clients found one name, so connecting the second would evict the "
        "first -- which is the whole defect: the fleet is evicted by whichever "
        "tool touches the broker next"
    )


def test_a_client_that_is_named_keeps_its_name() -> None:
    assert Client("localhost", client_id="my-fleet").client_id == "my-fleet"


def test_the_name_is_one_the_broker_can_hold() -> None:
    name = Client("localhost").client_id
    assert name, "a client with no name has no session of its own"
    assert name.isascii(), f"{name!r} would not survive the CONNECT encoding"
    # The id is a length-prefixed field, so the ceiling is the two bytes that
    # prefix it rather than anything the generated name could reach.
    assert len(name.encode("utf-8")) < 65536
