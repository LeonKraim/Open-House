"""Check a Store answers the way Open House expects, end to end.

    python store/check.py http://127.0.0.1:8090

**Why this is a script and not a test.** It talks to a running server, and the
repository's tests do not: nothing in `tests/` may reach the network, because a
test that needs a store in the room is a test that fails on the machine of
somebody who has not run one. What is *here* is the half that has to be checked
against a real PocketBase -- that the rules refuse what they are meant to refuse
and that the hooks move the counters they are meant to move -- and it is written
so that running it is one command against a Store somebody just stood up.

Every request below is exactly what the integration sends, and every reply is
parsed by `ha_adapter.store_api`, so a shape the client cannot read shows up
here rather than in the panel. It is repeatable: it starts by clearing the module
it is about to publish, and it claims its two publisher names only if it does not
already hold them.

Two publishers are needed because a rating is one person's opinion of somebody
else's module, and the module being checked is therefore published by one and
rated by the other.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ha_adapter import module_definitions, store_api

#: The Store to check: the first argument, or the address the compose file
#: publishes. A Store somewhere else is the ordinary case and is why this is an
#: argument rather than a constant.
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8090"


def call(method, path, body=None, token=None):
    data = None if body is None else json.dumps(body).encode()
    url = path if path.startswith("http") else BASE + path
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", token)
    try:
        with urllib.request.urlopen(request) as reply:
            return reply.status, json.loads(reply.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def auth(name, password):
    status, body = call(
        "POST",
        store_api.auth_path(BASE, "publishers"),
        {"identity": name, "password": password},
    )
    assert status < 400, (status, body)
    return body["token"], body["record"]["id"]


def claim(name, password):
    """Take a publisher name, or find it already taken by this same install.

    Tolerating the refusal is what makes the walk repeatable: the second run
    meets the name it claimed on the first, and the only other way a name is
    refused is a body the client itself would never send.
    """
    status, body = call(
        "POST",
        store_api.records_path(BASE, "publishers"),
        store_api.register_payload(name, password),
    )
    assert status < 400 or store_api.name_taken(body), (status, body)


def main() -> int:
    # Two publishers, because a rating needs somebody who is not the author.
    claim("marqbarq", "onetwothree")
    author_token, author_id = auth("marqbarq", "onetwothree")
    # Start from no module of this slug, so the walk reads the same twice. The
    # delete cascades into the ratings, comments and installs that point at the
    # module, which is also the only way to clear them.
    _, mine = call(
        "GET",
        store_api.records_path(BASE, "modules")
        + "?perPage=200&filter=slug='evening_lighting'",
        token=author_token,
    )
    for record in mine.get("items", []):
        call(
            "DELETE",
            store_api.record_path(BASE, "modules", record["id"]),
            token=author_token,
        )
    claim("raterperson", "fourfivesix")
    rater_token, rater_id = auth("raterperson", "fourfivesix")

    definition = module_definitions.ModuleDefinition(
        slug="evening_lighting",
        title="Evening lighting",
        source="alias: Evening lighting\n",
        description="Brings the lights down at dusk.",
        author="marqbarq",
        version="1.0.0",
    )
    status, published = call(
        "POST",
        store_api.records_path(BASE, "modules"),
        store_api.publish_payload(definition, publisher_id=author_id),
        token=author_token,
    )
    print("publish:", status)
    assert status < 400, published
    module_id = published["id"]

    # The listing, as the Store opens it: sorted, and with the publisher named.
    status, listing = call(
        "GET",
        store_api.records_path(BASE, "modules")
        + "?perPage=20&sort=-updated&expand=publisher",
    )
    rows = [store_api.module_row(record) for record in listing["items"]]
    print("listing:", status, [(row.slug, row.publisher, row.rating) for row in rows])
    assert rows and rows[0].document["definition"]["slug"] == "evening_lighting"
    assert rows[0].publisher == "marqbarq", rows[0]

    # The reading the install path uses: the document back out as a definition.
    back = module_definitions.from_document(rows[0].document)
    print("round trip:", back.slug, back.title, repr(back.source))

    # A rating, which the rater is allowed to make and the author is not.
    status, rated = call(
        "POST",
        store_api.records_path(BASE, "ratings"),
        store_api.rating_payload(module_id, rater_id, 4),
        token=rater_token,
    )
    print("rate as a stranger:", status)
    assert status < 400, rated
    # The author rating their own module: refused by the rule, so that no
    # average on the Store is a number its subject made up.
    status, wrong = call(
        "POST",
        store_api.records_path(BASE, "ratings"),
        store_api.rating_payload(module_id, author_id, 5),
        token=author_token,
    )
    print("rate your own:", status)
    assert status >= 400, wrong

    # A *second* rating is the same rating changed, and the Store's own answer to
    # a second one is the unique index: it refuses the row. That refusal is why
    # the integration updates the rating it left rather than posting another --
    # so what is checked here is the refusal, and the update is the client's half.
    again = call(
        "POST",
        store_api.records_path(BASE, "ratings"),
        store_api.rating_payload(module_id, rater_id, 2),
        token=rater_token,
    )
    print("rate twice:", again[0], json.dumps(again[1].get("data", {}))[:120])
    assert again[0] >= 400, again

    # A comment, and an install.
    status, commented = call(
        "POST",
        store_api.records_path(BASE, "comments"),
        store_api.comment_payload(module_id, rater_id, "Works a treat."),
        token=rater_token,
    )
    print("comment:", status)
    assert status < 400, commented
    status, installed = call(
        "POST",
        store_api.records_path(BASE, "installs"),
        store_api.install_payload(module_id, rater_id),
        token=rater_token,
    )
    print("install:", status)
    assert status < 400, installed
    status, twice = call(
        "POST",
        store_api.records_path(BASE, "installs"),
        store_api.install_payload(module_id, rater_id),
        token=rater_token,
    )
    print("install twice:", status)
    assert status >= 400, twice

    # And the counters, which the hook is what moves.
    status, one = call(
        "GET", store_api.record_path(BASE, "modules", module_id) + "?expand=publisher"
    )
    row = store_api.module_row(one)
    print(
        "counters:",
        row.stars_sum,
        row.stars_count,
        row.comments,
        row.installs,
        "rating",
        row.rating,
    )
    assert (row.stars_sum, row.stars_count, row.comments, row.installs) == (
        4,
        1,
        1,
        1,
    ), row
    assert row.rating == 4.0, row
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
