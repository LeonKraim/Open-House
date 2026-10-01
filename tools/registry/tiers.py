"""The four trust tiers, as data.

A tier answers one question at publishing time and two at check time: whether
the pack's arrival is backed by a signature or a review, whether a dangerous
permission is acceptable in it, and whether it belongs in the public index at
all. Three of the four fields below are read by a check -- `permits_flagged` by
the permission lint, `publishes` by the generator, and `install_flow` by the
store -- and `banner` is read by the panel.

The tier list is `registry/tiers.yaml` and not a table in this module, because
the difference between tiers is a policy a maintainer edits and the code that
applies it should not be released to change it. Which tiers exist is closed by
`TIER_NAMES` and checked against the document, so a typo in a pointer's `tier`
is a failure rather than a silently-unknown fifth tier.

**A tier cannot permit a banned service.** The ban is about services that act on
the host rather than on a device in the house, and it is `catalog/pack-policy.yaml`'s
statement for every pack everywhere. A tier permits a *flagged* service and
never a banned one, which is why the word in the field is `flagged`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import yaml

from tools.catalog.narrow import as_sequence

from .layout import TIERS_FILENAME

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

__all__ = ["TIER_NAMES", "Tier", "TierError", "load_tiers"]

#: The four tiers, in the order a reader meets them from most to least assured.
#: Closed, because a pointer naming a fifth tier is a pointer no check knows how
#: to judge, and admitting it would be admitting a tier nobody reviewed.
TIER_NAMES: tuple[str, ...] = ("official", "verified", "community", "local")


class TierError(Exception):
    """A tier document that cannot be read, naming the file and the field."""

    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"{path.as_posix()} {reason}")


@dataclass(frozen=True, slots=True)
class Tier:
    """One tier, as the checks read it."""

    name: str
    description: str
    #: Backed by a signature over the release artefact.
    signed: bool
    #: Backed by a human having read the manifest and its permission list.
    reviewed: bool
    #: Whether a flagged -- dangerous but legitimate -- service is acceptable.
    permits_flagged: bool
    #: Whether packs of this tier appear in the public index.
    publishes: bool
    #: `store` for the normal install flow, `config` for a sideload from /config.
    install_flow: str
    #: The warning the store shows for this tier, or `None` when there is none.
    banner: str | None

    @property
    def permits_dangerous(self) -> bool:
        """Whether a flagged permission is acceptable in this tier."""
        return self.permits_flagged


def load_tiers(registry_root: Path) -> Mapping[str, Tier]:
    """Every tier `registry/tiers.yaml` declares, keyed by name.

    The document's tier set is required to be exactly `TIER_NAMES`: a tree that
    dropped a tier would make every pointer of it unjudgeable, and a tree that
    added one would be a policy this code cannot apply. Both are named rather
    than tolerated.
    """
    path = registry_root / TIERS_FILENAME
    document = _document(path)
    if not isinstance(document.get("tiers"), list):
        raise TierError(path, "does not declare `tiers` as a list")
    tiers: dict[str, Tier] = {}
    for row in as_sequence(document.get("tiers")):
        tier = _tier(path, row)
        if tier.name in tiers:
            raise TierError(path, f"declares the tier {tier.name!r} twice")
        tiers[tier.name] = tier
    missing = sorted(set(TIER_NAMES) - set(tiers))
    if missing:
        raise TierError(path, f"does not declare the tier(s) {missing}")
    unknown = sorted(set(tiers) - set(TIER_NAMES))
    if unknown:
        raise TierError(path, f"declares the unknown tier(s) {unknown}")
    return tiers


def _document(path: Path) -> Mapping[str, object]:
    try:
        loaded: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise TierError(path, f"could not be read: {exc}") from exc
    if not isinstance(loaded, dict):
        raise TierError(path, "is not a YAML mapping")
    return cast("Mapping[str, object]", loaded)


def _tier(path: Path, row: object) -> Tier:
    if not isinstance(row, dict):
        raise TierError(path, "carries a tier that is not a mapping")
    mapping = cast("Mapping[str, object]", row)
    name = _text(path, mapping, "name")
    if name not in TIER_NAMES:
        raise TierError(
            path, f"declares a tier named {name!r}, which is not one of {TIER_NAMES}"
        )
    return Tier(
        name=name,
        description=_text(path, mapping, "description"),
        signed=_flag(path, mapping, "signed"),
        reviewed=_flag(path, mapping, "reviewed"),
        permits_flagged=_flag(path, mapping, "permits_flagged"),
        publishes=_flag(path, mapping, "publishes"),
        install_flow=_text(path, mapping, "install_flow"),
        banner=_optional_text(path, mapping, "banner"),
    )


def _text(path: Path, row: Mapping[str, object], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value:
        raise TierError(path, f"carries no usable `{field}`")
    return value


def _optional_text(path: Path, row: Mapping[str, object], field: str) -> str | None:
    value = row.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise TierError(path, f"carries no usable `{field}`")
    return value


def _flag(path: Path, row: Mapping[str, object], field: str) -> bool:
    value = row.get(field)
    if not isinstance(value, bool):
        raise TierError(path, f"carries no usable `{field}`")
    return value
