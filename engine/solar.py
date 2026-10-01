"""The sun's elevation, from a virtual instant and a fixture's location.

Motion lighting's dark test reads a lux sensor when one is bound and the computed
sun position when none is (`first-behaviours`). That second branch is the only
input in the phase that is neither a device nor a mode, so it is computed here
from two things the engine already has -- the virtual clock and a location the
fixture carries -- and from nothing else.

Two decisions are visible in the signature:

- **The location is a parameter, not engine state.** `simulation` is explicit
  about this ("Location is not engine state"): making it engine state would put a
  constant the user never chose into the same snapshot as the enable flags and
  bindings, and would make "the same house at the same instant decides the same"
  true only until someone changed a value no user can see. The fixture supplies
  it; the engine passes it through; nothing snapshots it.
- **The computation is pure and total.** No caching, no "previous elevation", no
  reading of the clock -- `at` is an argument. That is what makes the sun branch
  a function of the virtual clock and the location and of nothing else, which is
  what `first-behaviours`' "the sun branch follows the clock" scenario asserts,
  and it is also what keeps this module on the right side of `simulation`'s scan
  for a wall-clock read.

The algorithm is the standard low-precision solar position formula -- mean
longitude and anomaly, the equation of centre, the obliquity, the right
ascension, Greenwich mean sidereal time -- accurate to a few hundredths of a
degree, which is far finer than any elevation threshold a house would set. The
high-precision alternative (the full VSOP87 reduction) buys precision nothing in
this phase reads, so it is not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from math import asin, atan2, cos, degrees, radians, sin

#: The J2000.0 epoch, which the day count below is measured from. The formula is
#: written in days from this instant, so the constant is the origin the whole
#: computation is expressed in rather than a convenience.
_EPOCH = datetime(2000, 1, 1, 12, 0, tzinfo=UTC)

#: Seconds in a day, named because the day count is a division by it.
_SECONDS_PER_DAY = 86_400.0


class SolarError(Exception):
    """Base for the failures this module defines."""


class InvalidLocationError(SolarError):
    """A latitude or longitude outside the range the formula is defined on."""

    def __init__(self, field: str, value: float) -> None:
        super().__init__(f"{field} {value!r} is outside its valid range")
        self.field = field
        self.value = value


@dataclass(frozen=True, slots=True)
class Location:
    """Where a fixture house is, as its own configuration.

    Latitude and longitude are the two values the elevation formula reads. The
    zone is carried because the fixture's metadata states it and because a
    scenario written in local times needs it to say what "08:00" means, but no
    arithmetic below reads it: an aware `datetime` already names its instant, so
    the elevation is the same for two zones describing the same moment.
    """

    latitude: float
    longitude: float
    time_zone: str

    def __post_init__(self) -> None:
        if not -90.0 <= self.latitude <= 90.0:
            raise InvalidLocationError("latitude", self.latitude)
        if not -180.0 <= self.longitude <= 180.0:
            raise InvalidLocationError("longitude", self.longitude)
        if not self.time_zone:
            raise InvalidLocationError("time_zone", self.time_zone)


def sun_elevation(*, at: datetime, location: Location) -> float:
    """The sun's elevation above the horizon at `at`, in degrees.

    Negative below the horizon. `at` must be timezone-aware: a naive instant has
    no defined relationship to Greenwich, so the sidereal term would be silently
    wrong by however many hours the caller's zone happens to be offset by.
    """
    if at.tzinfo is None:
        raise SolarError("sun_elevation needs an aware instant, not a naive one")

    days = (at - _EPOCH).total_seconds() / _SECONDS_PER_DAY

    # Mean longitude and mean anomaly of the sun.
    mean_longitude = 280.460 + 0.9856474 * days
    mean_anomaly = 357.528 + 0.9856003 * days

    # Ecliptic longitude, corrected by the equation of centre.
    anomaly = radians(mean_anomaly)
    ecliptic_longitude = (
        mean_longitude + 1.915 * sin(anomaly) + 0.020 * sin(2 * anomaly)
    )

    # Obliquity of the ecliptic, then the sun's declination and right ascension.
    obliquity = radians(23.439 - 0.0000004 * days)
    lam = radians(ecliptic_longitude)
    declination = asin(sin(obliquity) * sin(lam))
    right_ascension = degrees(atan2(cos(obliquity) * sin(lam), cos(lam)))

    # Greenwich mean sidereal time, in hours, then the local hour angle.
    greenwich = 18.697374558 + 24.06570982441908 * days
    local_sidereal = greenwich * 15.0 + location.longitude
    hour_angle = radians(local_sidereal - right_ascension)

    latitude = radians(location.latitude)
    elevation = asin(
        sin(latitude) * sin(declination)
        + cos(latitude) * cos(declination) * cos(hour_angle)
    )
    return degrees(elevation)
