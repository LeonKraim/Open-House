"""The published Store's rules, without a Store.

`ha_adapter.store_api` holds the half of the Store that is Open House's -- what a
publisher's name may be, what a published row is once it comes back, what a
rating may be -- and the half that is the backend's is the backend's. What is
pinned here is only the first, which is why there is no server in this file: a
rule about names that needed a running database to check would be a rule nobody
checks.

Two of these are worth reading rather than skimming. **A name is refused before
it is sent**, because the backend's uniqueness refusal is a round trip and a
person who typed a space should not have to wait for one to be told. And **an
unrated module is not a zero-rated one**: `PublishedModule.rating` is `None`, and
a screen that showed "0.0 (0)" would be reporting a verdict nobody gave.
"""

from __future__ import annotations

from ha_adapter import module_definitions, store_api


def test_a_publisher_name_is_lower_case_and_has_no_spaces() -> None:
    assert store_api.name_refusal("kitchen_lights") is None
    assert store_api.name_refusal("marq-barq") is None
    assert store_api.name_refusal("a1b") is None
    # The refusal names what is wrong, because the person typed it and has to fix
    # it: "invalid" is not something anybody can act on.
    assert "lower case" in str(store_api.name_refusal("MarqBarq"))
    assert "space" in str(store_api.name_refusal(" marqbarq "))
    assert "empty" in str(store_api.name_refusal("   "))


def test_a_name_the_store_keeps_for_itself_cannot_be_claimed() -> None:
    # `official` is a tier and `open_house` is the project. Either published as a
    # person's own name would be a claim that is not true, and the tier chip
    # beside a module would then be a lie the Store itself printed.
    assert "keeps for itself" in str(store_api.name_refusal("official"))
    assert "keeps for itself" in str(store_api.name_refusal("open_house"))
    assert store_api.name_refusal("official_looking") is None


def test_a_name_has_a_length_and_a_shape() -> None:
    assert "at least" in str(store_api.name_refusal("ab"))
    assert "at most" in str(store_api.name_refusal("x" * 40))
    assert "letters, digits" in str(store_api.name_refusal("lights!"))
    # A name that ends on a separator is a name with a typo in it, and it would
    # be a different name from the one without.
    assert "letters, digits" in str(store_api.name_refusal("lights-"))


def test_only_a_uniqueness_refusal_means_the_name_is_taken() -> None:
    # The status code is not enough: a 400 from this collection could be any
    # field failing any rule, and reporting all of them as "taken" would tell a
    # person their name had gone when the request was simply malformed.
    taken = {
        "data": {
            "name": {
                "code": "validation_not_unique",
                "message": "The value must be unique.",
            }
        }
    }
    assert store_api.name_taken(taken) is True
    assert (
        store_api.name_taken({"data": {"name": {"code": "validation_required"}}})
        is False
    )
    assert (
        store_api.name_taken({"data": {"password": {"code": "validation_not_unique"}}})
        is False
    )
    assert store_api.name_taken({"message": "Failed to create record."}) is False
    assert store_api.name_taken(None) is False


def test_a_rating_is_whole_stars_between_one_and_five() -> None:
    for star in store_api.STARS:
        assert store_api.stars_refusal(star) is None
    # Zero is not a rating, it is silence; a half star is arithmetic a person
    # reads as a bug; `True` is an int in Python and is not three stars.
    assert "between" in str(store_api.stars_refusal(0))
    assert "between" in str(store_api.stars_refusal(6))
    assert "whole number" in str(store_api.stars_refusal(4.5))
    assert "whole number" in str(store_api.stars_refusal(True))
    assert "whole number" in str(store_api.stars_refusal("4"))


def test_a_module_row_is_read_with_its_publisher_expanded() -> None:
    row = store_api.module_row(
        {
            "id": "abc123",
            "slug": "evening_lighting",
            "title": "Evening lighting",
            "summary": "Brings the lights down at dusk.",
            "publisher": "pub1",
            "version": "2.1.0",
            "tier": "community",
            "stars_sum": 21,
            "stars_count": 5,
            "comments": 3,
            "installs": 104,
            "updated": "2026-10-01 12:00:00.000Z",
            "document": {"slug": "evening_lighting"},
            "expand": {"publisher": {"id": "pub1", "name": "marqbarq"}},
        }
    )
    assert row.publisher == "marqbarq"
    assert row.publisher_id == "pub1"
    assert row.rating == 4.2
    assert row.document == {"slug": "evening_lighting"}


def test_a_row_the_store_did_not_expand_still_reads() -> None:
    # A listing asks for the expansion and a create reply does not, so both
    # shapes arrive. An id shown where a name belongs is one the Store asked not
    # to have; an id shown where a *name* was expected is a bug in the reading.
    row = store_api.module_row({"id": "a", "slug": "s", "publisher": "pub1"})
    assert row.publisher == "pub1"
    assert row.publisher_id == "pub1"

    named = store_api.module_row({"id": "a", "slug": "s", "publisher_name": "marqbarq"})
    assert named.publisher == "marqbarq"


def test_a_module_nobody_rated_is_not_a_module_rated_zero() -> None:
    row = store_api.module_row(
        {"id": "a", "slug": "s", "stars_sum": 0, "stars_count": 0}
    )
    assert row.rating is None
    # And a count with no sum is a broken row rather than a rating of nothing.
    assert (
        store_api.module_row({"id": "a", "slug": "s", "stars_count": 0}).rating is None
    )


def test_publishing_carries_the_definition_itself() -> None:
    definition = module_definitions.ModuleDefinition(
        slug="evening_lighting",
        title="Evening lighting",
        source="alias: Evening lighting\n",
        description="Brings the lights down at dusk.",
        author="marqbarq",
        version="2.1.0",
    )
    body = store_api.publish_payload(definition, publisher_id="pub1")
    # The document, not a manifest: what a person installs is what they
    # published, with no second format in between to disagree with the first. It
    # is the *exported file*, wrapper and all, so that the Store holds exactly
    # what `module_definitions.from_document` reads back -- a bare definition
    # here would be a second shape to keep in step with the first.
    assert body["document"][module_definitions.KIND] == module_definitions.VERSION
    assert body["document"]["definition"]["slug"] == "evening_lighting"
    assert body["document"]["definition"]["source"] == definition.source
    assert body["slug"] == "evening_lighting"
    # Named from the record rather than from the name, so a publisher who later
    # calls themselves something else keeps the rows they published.
    assert body["publisher"] == "pub1"
    # The counters start at nothing: a version is a thing a person installs, and
    # a rating carried over from the last one would be a verdict on a module
    # nobody had seen.
    assert body[store_api.STARS_SUM] == 0
    assert body[store_api.STARS_COUNT] == 0
    assert body["installs"] == 0


def test_a_summary_is_the_definitions_own_description_when_none_is_given() -> None:
    definition = module_definitions.ModuleDefinition(
        slug="s", title="t", source="x: 1\n", description="What it does."
    )
    assert (
        store_api.publish_payload(definition, publisher_id="p")["summary"]
        == "What it does."
    )
    assert (
        store_api.publish_payload(definition, publisher_id="p", summary="Mine")[
            "summary"
        ]
        == "Mine"
    )


def test_a_publisher_claims_a_name_with_a_secret_and_two_copies_of_it() -> None:
    # PocketBase requires the confirmation field, and the secret is the account:
    # there is no email to give and no second sign-up, which is what makes "one
    # install, one name" the backend's rule rather than this client's memory.
    body = store_api.register_payload("marqbarq", "s3cret")
    assert body == {
        "name": "marqbarq",
        "password": "s3cret",
        "passwordConfirm": "s3cret",
    }


def test_the_installed_tab_is_split_by_slug_and_not_by_record_id() -> None:
    here = store_api.module_row({"id": "store1", "slug": "evening_lighting"})
    also_here = store_api.module_row({"id": "store2", "slug": "bedtime"})
    other = store_api.module_row({"id": "store3", "slug": "morning_routine"})
    installed, elsewhere = store_api.installed_split(
        [here, also_here, other], ["evening_lighting"]
    )
    # What the house holds is the *slug*: a module installed from an older
    # version of the same row is still installed, and matching on the Store's id
    # would move a person's own module to "not installed" because its publisher
    # fixed a typo in the summary.
    assert [module.slug for module in installed] == ["evening_lighting"]
    assert [module.slug for module in elsewhere] == ["bedtime", "morning_routine"]


def test_the_paths_are_pocketbases_and_a_trailing_slash_is_not_a_second_one() -> None:
    assert (
        store_api.records_path("https://store.example", "modules")
        == "https://store.example/api/collections/modules/records"
    )
    assert (
        store_api.records_path("https://store.example/", "modules")
        == "https://store.example/api/collections/modules/records"
    )
    assert (
        store_api.auth_path("https://store.example", "publishers")
        == "https://store.example/api/collections/publishers/auth-with-password"
    )
    assert store_api.record_path("https://s", "modules", "abc") == (
        "https://s/api/collections/modules/records/abc"
    )


def test_a_store_opens_on_what_was_published_last() -> None:
    assert store_api.filter_query()["sort"] == "-updated"
    assert store_api.filter_query()["expand"] == "publisher"
    assert "filter" not in store_api.filter_query()
    assert "evening" in store_api.filter_query(search="evening")["filter"]


def test_the_three_published_things_are_one_collection_each() -> None:
    # A rating, a comment and an install are rows rather than fields on the
    # module, because all three are the same shape: one house, one module, once.
    assert len(set(store_api.COLLECTIONS.values())) == len(store_api.COLLECTIONS)
    assert set(store_api.COLLECTIONS) == {
        "publishers",
        "modules",
        "ratings",
        "comments",
        "installs",
    }


def test_the_panel_row_leaves_the_document_out_and_carries_the_rating() -> None:
    row = store_api.module_row(
        {
            "id": "abc123",
            "slug": "evening_lighting",
            "title": "Evening lighting",
            "summary": "Brings the lights down at dusk.",
            "publisher": "pub1",
            "version": "2.1.0",
            "tier": "community",
            "stars_sum": 21,
            "stars_count": 5,
            "comments": 3,
            "installs": 104,
            "updated": "2026-10-01 12:00:00.000Z",
            "document": {"slug": "evening_lighting"},
            "expand": {"publisher": {"id": "pub1", "name": "marqbarq"}},
        }
    )
    sent = store_api.module_json(row)
    # A page of twenty rows does not need twenty documents parsed, and the one
    # that does is the one being installed -- so the file is not in the row.
    assert "document" not in sent
    assert sent["publisher"] == "marqbarq"
    assert sent["rating"] == 4.2
    assert sent["stars_count"] == 5
    assert sent["comments"] == 3
    assert sent["installs"] == 104


def test_a_row_nobody_rated_sends_no_rating_rather_than_zero() -> None:
    # The panel draws `null` as "not rated yet"; a `0.0` would be a verdict
    # nobody gave, which is the same distinction `PublishedModule.rating` makes.
    sent = store_api.module_json(store_api.module_row({"id": "a", "slug": "s"}))
    assert sent["rating"] is None


def test_mine_is_this_install_s_id_and_not_an_empty_string_matching_itself() -> None:
    mine = store_api.module_row(
        {
            "id": "a",
            "slug": "s",
            "publisher": "pub1",
            "expand": {"publisher": {"id": "pub1"}},
        }
    )
    theirs = store_api.module_row(
        {
            "id": "b",
            "slug": "t",
            "publisher": "pub2",
            "expand": {"publisher": {"id": "pub2"}},
        }
    )
    assert store_api.module_json(mine, publisher_id="pub1")["mine"] is True
    assert store_api.module_json(theirs, publisher_id="pub1")["mine"] is False

    # The load-bearing guard: a row the Store did not expand has an empty
    # publisher id, and `"" == ""` would otherwise mark every such row as this
    # install's -- a publish button on a module this house never published.
    unexpanded = store_api.module_row({"id": "c", "slug": "u"})
    assert store_api.module_json(unexpanded)["mine"] is False
    assert store_api.module_json(unexpanded, publisher_id="pub1")["mine"] is False


def test_a_comment_row_is_signed_with_a_name_and_not_an_id() -> None:
    comment = store_api.comment_row(
        {
            "id": "c1",
            "module": "m1",
            "publisher": "pub1",
            "body": "Works well in the hallway.",
            "created": "2026-10-02 08:00:00.000Z",
            "expand": {"publisher": {"id": "pub1", "name": "marqbarq"}},
        }
    )
    sent = store_api.comment_json(comment)
    # The module and the publisher's id are left out: a comment is drawn under
    # the module it is already on and signed with a name.
    assert sent == {
        "id": "c1",
        "publisher": "marqbarq",
        "body": "Works well in the hallway.",
        "created": "2026-10-02 08:00:00.000Z",
    }
