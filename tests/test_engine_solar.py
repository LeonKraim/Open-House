"""The sun's elevation: a pure function of an instant and a location.

`first-behaviours` gives motion lighting a dark test that reads a lux sensor when
one is bound and the computed sun position when none is. That second branch is
the only input in the phase that is neither a device nor a mode, so it gets its
own module and its own tests.

Three kinds of assertion are here, and the third is the one a plausible
implementation fails. The **bands** pin that the formula is the solar position
formula and not something that merely returns a number in range: the sun is high
at a northern summer noon and low at a northern winter one, and a formula with a
sign error or a swapped sine would land outside them. The **relations** pin
properties that must hold whatever the constants are -- the sun is up at noon and
down at midnight, and a southern-hemisphere noon in June is the mirror of a
northern one. The **purity** assertions pin that nothing here reads a clock or
remembers a previous answer, which is what makes the sun branch a function of the
virtual clock and of nothing else.

Each test says, in its docstring, what a falsifying implementation would look
like.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from engine.solar import InvalidLocationError, Location, SolarError, sun_elevation

#: A place at the prime meridian, so a UTC instant is also a local one and a
#: band can be written without converting anything by hand. The house fixture
#: carries its own location; this one exists to keep the arithmetic legible.
LONDON = Location(latitude=51.5, longitude=-0.1, time_zone="Europe/London")

#: The same latitude in the other hemisphere, for the mirror test.
SYDNEY = Location(latitude=-33.87, longitude=151.21, time_zone="Australia/Sydney")

_SUMMER = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)
_WINTER = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
_SUMMER_MIDNIGHT = datetime(2026, 6, 21, 0, 0, tzinfo=UTC)


# --------------------------------------------------------------------------
# The bands: it is the solar position formula
# --------------------------------------------------------------------------


def test_the_sun_is_high_at_a_northern_summer_noon() -> None:
    """At the June solstice's noon in London the sun stands near 62 degrees.

    A falsifying implementation with a sign error in the declination -- the
    commonest way to get this wrong -- would put the summer sun at the *winter*
    height, around 15 degrees, and a band excludes it.
    """
    elevation = sun_elevation(at=_SUMMER, location=LONDON)
    assert 55.0 < elevation < 68.0


def test_the_sun_is_low_at_a_northern_winter_noon() -> None:
    """At a January noon in London the sun stands near 15 degrees.

    A falsifying implementation that ignored the day of the year and returned the
    latitude's complement would put every noon at 38.5 degrees, inside neither
    band, so the pair of tests is what fixes the seasonal swing rather than the
    level.
    """
    elevation = sun_elevation(at=_WINTER, location=LONDON)
    assert 12.0 < elevation < 19.0


def test_a_southern_hemisphere_june_noon_is_the_mirror_of_a_northern_one() -> None:
    """Sydney's June noon is near 33 degrees -- its winter, and London's summer.

    A falsifying implementation that treated latitude as unsigned, or that
    dropped the hemisphere from the hour angle, would put Sydney's June noon at
    London's June height. This is the one test that fails only for a
    hemisphere-blind formula, which is why it is not left to the bands.
    """
    elevation = sun_elevation(
        at=datetime(2026, 6, 21, 2, 0, tzinfo=UTC), location=SYDNEY
    )
    assert 27.0 < elevation < 38.0


# --------------------------------------------------------------------------
# The relations: up at noon, down at midnight, ordered across the year
# --------------------------------------------------------------------------


def test_the_sun_is_up_at_noon_and_down_at_midnight_on_the_same_day() -> None:
    """One day, two instants: the elevation changes sign between them.

    A falsifying implementation that returned `abs(...)` -- or that dropped the
    arc-sine's sign -- would report a positive elevation at midnight and this is
    the cheapest test that says the below-horizon branch exists at all.
    """
    noon = sun_elevation(at=_SUMMER, location=LONDON)
    midnight = sun_elevation(at=_SUMMER_MIDNIGHT, location=LONDON)
    assert noon > 0.0
    assert midnight < 0.0


def test_the_winter_noon_is_lower_than_the_summer_noon() -> None:
    """The declination's swing is visible in the two noons of one location.

    A falsifying implementation whose seasonal term had the wrong sign would put
    January above June, and both bands above would still be satisfied by a
    formula whose two signs were wrong together.
    """
    assert sun_elevation(at=_WINTER, location=LONDON) < sun_elevation(
        at=_SUMMER, location=LONDON
    )


def test_the_elevation_moves_monotonically_through_the_morning() -> None:
    """Towards noon the sun rises; away from it, it falls.

    A falsifying implementation with a wrong coefficient on the sidereal term
    would make the daily arc run backwards or at the wrong rate, and a single
    band would not notice -- the sun would still be high at some instant near
    noon. Sampling the approach pins the direction.
    """
    elevations = [
        sun_elevation(at=datetime(2026, 6, 21, hour, 0, tzinfo=UTC), location=LONDON)
        for hour in (6, 8, 10, 12)
    ]
    assert elevations == sorted(elevations)


# --------------------------------------------------------------------------
# Purity: no clock, no memory, no second input
# --------------------------------------------------------------------------


def test_the_same_instant_and_location_give_the_same_answer_every_time() -> None:
    """Two identical calls are exactly equal, and an intervening call changes nothing.

    A falsifying implementation that cached "the previous elevation" -- or that
    measured a delta against a stored instant -- would return a different value
    the second time once another instant had been asked for in between. Exact
    equality is the right assertion because the formula is deterministic
    arithmetic and has no tolerance to allow.
    """
    first = sun_elevation(at=_SUMMER, location=LONDON)
    other = sun_elevation(at=_WINTER, location=LONDON)
    assert other != first
    assert sun_elevation(at=_SUMMER, location=LONDON) == first


def test_the_elevation_depends_on_the_instant_and_not_on_the_order_asked() -> None:
    """Asking for a later instant first does not change the earlier answer.

    The same falsification as above, reached from the other side: an
    implementation that carried state between calls would answer the earlier
    instant differently depending on what had been asked before it.
    """
    forwards = [
        sun_elevation(at=instant, location=LONDON)
        for instant in (_SUMMER_MIDNIGHT, _SUMMER, _WINTER)
    ]
    backwards = [
        sun_elevation(at=instant, location=LONDON)
        for instant in (_WINTER, _SUMMER, _SUMMER_MIDNIGHT)
    ]
    assert forwards == list(reversed(backwards))


def test_one_instant_has_one_elevation_however_it_is_spelled() -> None:
    """The same moment written in two zones is the same elevation.

    A falsifying implementation that read the *naive* part of the datetime --
    `at.hour` and friends -- would give two different answers for one instant and
    would make the sun branch depend on the fixture's time zone rather than on
    the clock.
    """
    instant = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    same_moment = datetime(2026, 1, 1, 7, 0, tzinfo=timezone(timedelta(hours=-5)))
    assert same_moment == instant
    assert sun_elevation(at=same_moment, location=LONDON) == sun_elevation(
        at=instant, location=LONDON
    )


def test_the_zone_name_on_a_location_is_not_read() -> None:
    """Two locations with the same coordinates differ only in a name nothing reads.

    A falsifying implementation that applied the zone as an offset would answer
    the same instant differently for the two, and the location's zone is carried
    for a scenario's local times rather than for this arithmetic.
    """
    renamed = Location(
        latitude=LONDON.latitude,
        longitude=LONDON.longitude,
        time_zone="Pacific/Auckland",
    )
    assert sun_elevation(at=_SUMMER, location=renamed) == sun_elevation(
        at=_SUMMER, location=LONDON
    )


# --------------------------------------------------------------------------
# What is refused
# --------------------------------------------------------------------------


def test_a_naive_instant_is_refused() -> None:
    """An instant with no zone has no defined relationship to Greenwich.

    A falsifying implementation that assumed UTC would silently misplace the
    instant by the caller's offset, and the sun branch would then decide the dark
    test from the wrong hour -- a bug that produces a plausible number and no
    error anywhere.
    """
    with pytest.raises(SolarError):
        sun_elevation(at=datetime(2026, 1, 1, 12, 0), location=LONDON)


@pytest.mark.parametrize(
    "field,value",
    [
        ("latitude", 90.1),
        ("latitude", -90.1),
        ("longitude", 180.1),
        ("longitude", -180.1),
    ],
)
def test_a_coordinate_outside_its_range_is_refused_naming_the_field(
    field: str, value: float
) -> None:
    """A latitude past the pole or a longitude past the antimeridian is a mistake.

    A falsifying implementation that accepted them would run the formula on a
    coordinate that does not exist and return a number indistinguishable from a
    real one, so the mistake would surface as a house that lights up at the wrong
    time rather than as a refused configuration.
    """
    coordinates = {"latitude": LONDON.latitude, "longitude": LONDON.longitude}
    coordinates[field] = value
    with pytest.raises(InvalidLocationError) as raised:
        Location(time_zone="Europe/London", **coordinates)  # type: ignore[arg-type]
    assert raised.value.field == field


def test_an_empty_time_zone_is_refused() -> None:
    """A fixture that names no zone cannot say what a local time means.

    A falsifying implementation that let the empty string through would defer the
    failure to whichever scenario first wrote `08:00`, where the traceback would
    name the scenario rather than the fixture.
    """
    with pytest.raises(InvalidLocationError) as raised:
        Location(latitude=51.5, longitude=-0.1, time_zone="")
    assert raised.value.field == "time_zone"


@pytest.mark.parametrize(
    "latitude,longitude", [(90.0, 180.0), (-90.0, -180.0), (0.0, 0.0)]
)
def test_the_boundary_coordinates_are_accepted(
    latitude: float, longitude: float
) -> None:
    """The poles and the antimeridian are places, so the range is closed.

    A falsifying implementation that used a strict comparison would refuse the
    equator and the poles -- the two coordinates most likely to be typed in a
    fixture -- and every other test here uses a mid-range latitude, so nothing
    else would notice.
    """
    location = Location(latitude=latitude, longitude=longitude, time_zone="UTC")
    elevation = sun_elevation(at=_SUMMER, location=location)
    assert -90.0 <= elevation <= 90.0
