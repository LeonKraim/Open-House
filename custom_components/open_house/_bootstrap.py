"""Make the packages the integration imports importable inside Home Assistant.

Home Assistant loads a custom integration from `/config/custom_components/`, and
nothing else is put on the import path for it. But the integration is
deliberately thin: `ha_adapter` makes the setup flow's decisions as pure
functions, and `engine` makes the household's. Both live in the repository, not
in the integration, because they are also what the simulator, the CLI and the
tests use -- a copy under `custom_components/` would be a second source of truth
for the same code, which is the failure this project refuses everywhere else.

That leaves two deployments, and this module answers the first before the second:

* An install that has the repository beside it -- the bundled container, which
  mounts the packages read-only at `/openhouse-src`, or a machine where the
  checkout is elsewhere and `OPEN_HOUSE_SRC` says where. Either names a directory
  this module puts on `sys.path`.
* The release asset, which is built by `tools/package_integration.py` with the
  packages *inside* the integration so a HACS install carries them, since HACS
  copies `custom_components/open_house/` and nothing beside it. There the
  directory to put on `sys.path` is this module's own, and it is recognised by
  carrying `engine/` -- which the repository's copy of this directory never does,
  because there the packages are its siblings. That is what keeps this fallback
  from firing in a checkout, where the packages are importable already.

It is imported first, before anything that reaches for them, and it is idempotent.
When neither directory is there the module does nothing rather than inserting a
path that does not exist, which is the honest answer for a bare checkout.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: Where `docker/docker-compose.yml` mounts the repository's packages. A default
#: rather than a requirement, because the mount point is a property of one
#: deployment and the override is how a second deployment -- a real HA install
#: under `/config` -- says where its copy lives.
DEFAULT_SOURCE = "/openhouse-src"

#: The package an install directory is recognised by. `engine` is the one every
#: deployment has and the one whose absence would fail loudest, so it is the
#: marker rather than a list that could pass on a half-copied tree.
_SENTINEL = "engine"


def shipped_root() -> Path | None:
    """The packages this module's own directory carries, or `None`.

    The release asset ships `engine/`, `ha_adapter/` and the rest inside the
    integration, because HACS copies `custom_components/open_house/` and nothing
    beside it. The repository does not: there the packages are siblings of
    `custom_components/`, this directory carries no `engine/`, and this answers
    `None` -- so the fallback that exists for an install cannot change what a
    checkout resolves to.
    """
    here = Path(__file__).resolve().parent
    return here if (here / _SENTINEL).is_dir() else None


def source_root() -> Path | None:
    """The directory the packages are to be imported from, or `None`.

    The same directory `install` puts on `sys.path`, offered as an answer rather
    than only as a side effect, because it is also where the catalog lives: the
    setup flow resolves its vocabulary relative to that root, and in a container
    the checkout is at the mount point rather than above the integration. The
    mount is tried before the shipped copy so an explicit `OPEN_HOUSE_SRC` always
    wins. `None` means neither is present and the caller should fall back to
    walking its own ancestors, which is the case in the repository's tests.
    """
    directory = Path(os.environ.get("OPEN_HOUSE_SRC") or DEFAULT_SOURCE)
    if directory.is_dir():
        return directory
    return shipped_root()


def install(source: str | None = None) -> bool:
    """Put the packages on `sys.path`, once. Reports whether it did.

    With no argument the directory is `source_root()`: the mount, else the copy
    the integration was shipped with. Returns `False` when there is neither,
    which is the honest answer for a repository checkout -- the caller can then
    fall back to whatever path the packages are already importable from, rather
    than failing on a mount point that exists only inside the container.
    """
    directory = Path(source) if source else source_root()
    if directory is None or not directory.is_dir():
        return False
    location = str(directory)
    if location not in sys.path:
        sys.path.insert(0, location)
    return True


install()
