"""A minimal MQTT 3.1.1 client: connect, publish, subscribe, and read a loop.

This exists in `tools/` for the same two reasons `tools/netguard.py` does, and
the two are worth stating because the alternative -- `paho-mqtt` -- is the
obvious one.

**`pyproject.toml`'s `dependencies` array is a boundary, not a list.** The
engine-purity invariant reads exactly that array and fails any import under
`engine/` that is not stdlib, first-party, or named there, so adding a
distribution is a change to an enforced rule rather than a convenience. The mock
fleet is a tool: no module under `engine/`, `sim/` or `openhouse/` imports this
one, so a dependency taken here would be taken for a caller the boundary does not
cover -- which is exactly the trade `tools/ha/onboard.py` refused when it
hand-rolled its websocket client for "one JSON message in, one JSON message out".
MQTT is the same trade with more of the same: the fleet needs CONNECT, PUBLISH,
SUBSCRIBE, PINGREQ and the inbound read, and that is a protocol small enough to
write down rather than a reason to widen the array.

**`sim/` may not import `socket`, and a scenario run executes under a guard that
fails the moment one opens.** So the fleet cannot be a `sim/` module and cannot
run inside a scenario; it is a separate process beside the container, and this is
the module that reaches the network on its behalf. `tools/` is where the one
module that must import `socket` already lives, so it is where this one lives
too.

What is deliberately absent is as much a decision as what is present. There is no
QoS 1 or 2 (the fleet's state is republished on every change, so a lost message
is a message the next publish replaces, and a retry queue would be machinery
nothing reads), no retained-message bookkeeping, no will, no authentication
(`docker/mosquitto.conf` is `allow_anonymous true`), and no session persistence
(every connect is a clean session). Each is a feature a general client has and
this one does not need, and adding one would be adding a code path no caller
exercises -- which the project's own rule against unread fields rejects wherever
it appears.

Boundaries: this module speaks to a broker, and a broker that stops answering
must not hang a fleet forever. `connect` and every read take a timeout, so a dead
broker surfaces as `MqttError` at a call site rather than as a process that never
returns.
"""

from __future__ import annotations

import contextlib
import socket
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = ["Client", "Message", "MqttError"]

#: The MQTT control packet types this client sends or understands, by the number
#: that occupies the high nibble of a packet's first byte. Named rather than
#: written as literals in the framing code below, because `0x30` in a comparison
#: is a number and `PUBLISH` is a sentence about the protocol.
_CONNECT = 0x1
_CONNACK = 0x2
_PUBLISH = 0x3
_SUBSCRIBE = 0x8
_SUBACK = 0x9
_PINGREQ = 0xC
_PINGRESP = 0xD
_DISCONNECT = 0xE

#: A connect acknowledgement's return code, by value. Zero is the only one that
#: is not a refusal, and the rest are named so a failure says `not authorised`
#: rather than `3`, which is what a reader of a log would otherwise have to look
#: up. `docs/` is not consulted at runtime; the table is here because a broker's
#: refusal is the one place this client has an answer to give.
_CONNACK_REASONS: dict[int, str] = {
    0: "accepted",
    1: "refused: unsupported protocol version",
    2: "refused: client identifier rejected",
    3: "refused: server unavailable",
    4: "refused: bad username or password",
    5: "refused: not authorised",
}

#: The largest value the remaining-length field can carry: four varint bytes,
#: seven payload bits each, and the fourth byte stops at 127 because the
#: continuation bit is the eighth. A message larger than this cannot be framed,
#: and the framing code says so rather than emitting a truncated length.
_MAX_REMAINING = 268_435_455


class MqttError(Exception):
    """A protocol failure, a broker refusal, or a socket that stopped answering.

    One type for the three because every caller does the same thing with all of
    them -- stop and report -- and a caller forced to catch three would be
    catching one decision in three shapes. The message carries what the broker
    said where the broker said anything.
    """


@dataclass(frozen=True, slots=True)
class Message:
    """One inbound PUBLISH: the topic a broker relayed, and its payload bytes.

    The payload stays bytes rather than becoming a string here. MQTT payloads are
    binary by definition, and a decoder living in the transport would decide an
    encoding for every caller -- `text` is the accessor for the callers that know
    theirs is UTF-8, which the fleet's command payloads are.
    """

    topic: str
    payload: bytes

    @property
    def text(self) -> str:
        """The payload decoded as UTF-8, replacing anything that is not."""
        return self.payload.decode("utf-8", errors="replace")


def _encode_length(length: int) -> bytes:
    """The remaining-length field: a varint, least significant seven bits first.

    Each byte carries seven payload bits and a continuation bit above them, so
    the encoding is a loop rather than a struct pack -- and the loop is why the
    ceiling exists: a value needing a fifth byte has no valid encoding, and
    emitting one anyway would put a length on the wire that the broker reads as
    something shorter.
    """
    if length < 0 or length > _MAX_REMAINING:
        raise MqttError(f"a message of {length} bytes cannot be framed")
    encoded = bytearray()
    while True:
        byte = length % 128
        length //= 128
        if length:
            byte |= 0x80
        encoded.append(byte)
        if not length:
            return bytes(encoded)


def _decode_publish(flags: int, body: bytes) -> Message:
    """One PUBLISH packet's body, as a `Message`.

    A module function rather than a method because two call sites need it -- the
    ordinary read, and the read that is waiting for a SUBACK and must not lose a
    message that arrived first -- and because it touches no client state: the
    packet's bytes are all it needs.
    """
    topic_length = int.from_bytes(body[:2], "big")
    topic = body[2 : 2 + topic_length].decode("utf-8", errors="replace")
    payload = body[2 + topic_length :]
    # A QoS 1 or 2 publish carries a packet identifier after the topic. This
    # client subscribes at QoS 0, so it is unreachable from its own
    # subscriptions -- but a broker configured differently would send one, and a
    # client that read the identifier as payload would hand its caller two bytes
    # of binary where a state string belongs.
    if flags & 0x06:
        payload = payload[2:]
    return Message(topic=topic, payload=payload)


def _read_exactly(sock: socket.socket, count: int) -> bytes:
    """Read exactly `count` bytes, or raise.

    `recv` returns what has arrived rather than what was asked for, so a single
    call is not a message -- it is however much of one the network had ready. The
    loop is the difference between a client that works on a fast local broker and
    one that mis-frames under load, which is the failure that looks like a
    protocol bug and is a read bug.
    """
    chunks = bytearray()
    while len(chunks) < count:
        chunk = sock.recv(count - len(chunks))
        if not chunk:
            raise MqttError(
                f"the broker closed the connection after {len(chunks)} of {count} bytes"
            )
        chunks.extend(chunk)
    return bytes(chunks)


def _read_length(sock: socket.socket) -> int:
    """The remaining-length field, decoded from the stream."""
    value = 0
    multiplier = 1
    for _ in range(4):
        byte = _read_exactly(sock, 1)[0]
        value += (byte & 0x7F) * multiplier
        if not byte & 0x80:
            return value
        multiplier *= 128
    raise MqttError("the remaining length ran past four bytes, which no packet may")


class Client:
    """One connection to one broker, good for one publish and subscribe loop.

    Not thread-safe and not reconnecting: the fleet owns one client, drives it
    from one loop, and a broker that goes away surfaces as `MqttError` to a
    caller that decides whether to retry. A client that reconnected behind its
    caller's back would reconnect into a subscription set the caller believes it
    installed and the broker no longer holds.
    """

    def __init__(
        self,
        host: str,
        port: int = 1883,
        *,
        client_id: str = "open-house-fleet",
        keepalive: int = 60,
        timeout: float = 10.0,
    ) -> None:
        self.host = host
        self.port = port
        self.client_id = client_id
        self.keepalive = keepalive
        self.timeout = timeout
        self._socket: socket.socket | None = None
        self._packet_id = 0
        #: Publishes read while waiting for something else -- a retained message
        #: that arrived ahead of its SUBACK. Held in arrival order so `poll`
        #: returns them in the order the broker sent them, which is the order a
        #: caller applying commands needs.
        self._pending: deque[Message] = deque()

    # -- Lifecycle ----------------------------------------------------------

    def connect(self) -> None:
        """Open the socket and complete the MQTT handshake.

        A clean session every time, so a broker that outlives a fleet run does
        not replay a previous run's subscriptions into this one.
        """
        if self._socket is not None:
            raise MqttError("this client is already connected")
        sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        # Every subsequent read uses the same timeout, so a broker that accepts
        # the connection and then goes quiet fails a read rather than blocking
        # forever -- the half-open case a connect timeout alone does not cover.
        sock.settimeout(self.timeout)
        self._socket = sock
        try:
            identifier = self.client_id.encode("utf-8")
            body = (
                b"\x00\x04MQTT"  # protocol name, length-prefixed
                + bytes([4])  # protocol level: 3.1.1
                + bytes([0x02])  # connect flags: clean session, no will, no auth
                + self.keepalive.to_bytes(2, "big")
                + len(identifier).to_bytes(2, "big")
                + identifier
            )
            self._send(_CONNECT, 0, body)
            self._expect_connack()
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        """Send DISCONNECT where the socket still writes, then close it.

        Best-effort on the send, because a close is called on the failure path as
        often as the success one and a DISCONNECT that cannot be written must not
        replace the error that brought the caller here.
        """
        sock, self._socket = self._socket, None
        if sock is None:
            return
        with contextlib.suppress(OSError):
            sock.sendall(bytes([_DISCONNECT << 4, 0]))
        sock.close()

    def __enter__(self) -> Client:
        self.connect()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # -- Outbound -----------------------------------------------------------

    def publish(
        self, topic: str, payload: str | bytes, *, retain: bool = False
    ) -> None:
        """Publish one message at QoS 0, optionally retained.

        Retained is how the fleet's discovery configs and current states are
        published: a subscriber that arrives after the fleet has announced -- a
        restarted Home Assistant, most of all -- needs the last announcement, not
        the next one.
        """
        encoded_topic = topic.encode("utf-8")
        body = (
            len(encoded_topic).to_bytes(2, "big")
            + encoded_topic
            + (payload.encode("utf-8") if isinstance(payload, str) else payload)
        )
        self._send(_PUBLISH, 0x01 if retain else 0x00, body)

    def subscribe(self, topic_filter: str) -> None:
        """Subscribe to one filter at QoS 0 and wait for the acknowledgement.

        Waiting is the point: a caller that subscribed and started publishing
        before the SUBACK arrived could have a command topic miss the first
        command HA sent. Returning after the broker has answered makes "subscribed"
        a fact rather than an intention.
        """
        packet_id = self._next_packet_id()
        encoded = topic_filter.encode("utf-8")
        body = (
            packet_id.to_bytes(2, "big")
            + len(encoded).to_bytes(2, "big")
            + encoded
            + bytes([0])  # requested QoS
        )
        self._send(_SUBSCRIBE, 0x02, body)
        self._expect_suback(packet_id)

    def ping(self) -> None:
        """Send PINGREQ. The caller's loop decides when; this only writes it."""
        self._send(_PINGREQ, 0, b"")

    # -- Inbound ------------------------------------------------------------

    def poll(self, *, timeout: float | None = None) -> Message | None:
        """Read packets until a PUBLISH arrives, or the timeout expires.

        Non-PUBLISH packets -- a PINGRESP, a SUBACK for a subscription this
        client made -- are consumed rather than returned, because a caller asking
        for the next message is asking about messages and not about the protocol's
        bookkeeping. `None` on timeout is distinct from an exception: a quiet
        broker is the normal case for a loop that polls, and a broker that went
        away is not.
        """
        if self._pending:
            return self._pending.popleft()
        sock = self._require_socket()
        if timeout is not None:
            sock.settimeout(timeout)
        try:
            return self._read_one()
        except TimeoutError:
            return None
        finally:
            if timeout is not None:
                sock.settimeout(self.timeout)

    def messages(self, *, timeout: float | None = None) -> Iterator[Message]:
        """Every message that arrives, one poll at a time, until the broker stops."""
        while True:
            message = self.poll(timeout=timeout)
            if message is not None:
                yield message

    # -- Internals ----------------------------------------------------------

    def _read_one(self) -> Message | None:
        packet_type, flags, body = self._read_packet()
        if packet_type == _PUBLISH:
            return _decode_publish(flags, body)
        if packet_type == _PINGRESP:
            return None
        raise MqttError(f"the broker sent an unexpected packet type {packet_type}")

    def _read_packet(self) -> tuple[int, int, bytes]:
        sock = self._require_socket()
        header = _read_exactly(sock, 1)[0]
        length = _read_length(sock)
        # A zero remaining length has no body to read, and `_read_exactly(sock, 0)`
        # would be a call that returns nothing while looking like it did something.
        body = _read_exactly(sock, length) if length else b""
        return header >> 4, header & 0x0F, body

    def _expect_connack(self) -> None:
        packet_type, _, body = self._read_packet()
        if packet_type != _CONNACK:
            raise MqttError(
                f"expected a connect acknowledgement, got packet type {packet_type}"
            )
        if len(body) < 2:
            raise MqttError("the connect acknowledgement carried no return code")
        code = body[1]
        if code != 0:
            reason = _CONNACK_REASONS.get(code, "refused with an unknown code")
            raise MqttError(f"the broker {reason}")

    def _expect_suback(self, packet_id: int) -> None:
        # A PUBLISH is not an error here, and this loop is a defect the first
        # live run found. MQTT gives no ordering between a SUBACK and the
        # retained messages a subscription releases, so a broker that sends the
        # retained message first -- mosquitto does -- delivers a PUBLISH while
        # this client is still waiting to be told the subscription exists. A
        # client that raised on it would work against a broker that happened to
        # answer first and fail against the one in `docker/`, which is the worst
        # shape a bug can have. The message is buffered instead, and `poll`
        # hands it to the caller in the order it arrived.
        while True:
            packet_type, flags, body = self._read_packet()
            if packet_type == _PUBLISH:
                self._pending.append(_decode_publish(flags, body))
                continue
            if packet_type != _SUBACK:
                raise MqttError(
                    f"expected a subscription acknowledgement, got packet type "
                    f"{packet_type}"
                )
            break
        if len(body) < 2 or int.from_bytes(body[:2], "big") != packet_id:
            raise MqttError(
                f"the subscription acknowledgement did not answer packet {packet_id}"
            )
        # A broker may grant a lower QoS than requested, and may refuse a filter
        # outright with 0x80. Refusal is the one worth failing on: a subscription
        # the broker did not grant is a command topic the fleet will never hear,
        # and a fleet that believed it was listening is worse than one that says
        # it is not.
        if len(body) >= 3 and body[2] == 0x80:
            raise MqttError(
                f"the broker refused the subscription for packet {packet_id}"
            )

    def _send(self, packet_type: int, flags: int, body: bytes) -> None:
        sock = self._require_socket()
        packet = bytes([(packet_type << 4) | flags]) + _encode_length(len(body)) + body
        try:
            sock.sendall(packet)
        except OSError as error:
            raise MqttError(
                f"the broker connection failed on write: {error}"
            ) from error

    def _require_socket(self) -> socket.socket:
        if self._socket is None:
            raise MqttError("this client is not connected")
        return self._socket

    def _next_packet_id(self) -> int:
        """A packet identifier in 1..65535, wrapping rather than reaching zero.

        Zero is not a legal identifier, so the wrap skips it -- a client that
        wrapped to zero would send a packet a broker is entitled to reject.
        """
        self._packet_id = self._packet_id % 65535 + 1
        return self._packet_id
