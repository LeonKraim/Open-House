"""Make the repository's Python packages importable inside Home Assistant.

Home Assistant loads a custom integration from `/config/custom_components/`, and
nothing else in the repository is on its import path. But the integration is
deliberately thin: `ha_adapter` makes the setup flow's decisions as pure
functions, and `engine` makes the household's. Both live in the repository, not
in the integration, because they are also what the simulator, the CLI and the
tests use -- a copy under `custom_components/` would be a second source of truth
for the same code, which is the failure this project refuses everywhere else.

So the container mounts those packages read-only at `/openhouse-src`, and this
module puts that directory on `sys.path`. It is imported first, before anything
that reaches for them.

The path is not hardcoded as a fact about the machine: `OPEN_HOUSE_SRC` overrides
it, and when the directory is absent the module does nothing rather than
inserting a path that does not exist. That is what makes it safe to import in the
repository's own test runs, where `ha_adapter` and `engine` are importable from
the working directory already and the container's mount point means nothing.
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


def source_root() -> Path | None:
    """The repository directory the packages were mounted from, or `None`.

    The same directory `install` puts on `sys.path`, offered as an answer rather
    than only as a side effect, because it is also where the catalog lives: the
    setup flow resolves its vocabulary relative to the checkout root, and inside
    the container the checkout is at the mount point rather than above the
    integration. `None` means there is no mount and the caller should fall back
    to walking its own ancestors, which is the case in the repository's tests.
    """
    directory = Path(os.environ.get("OPEN_HOUSE_SRC") or DEFAULT_SOURCE)
    return directory if directory.is_dir() else None


def install(source: str | None = None) -> bool:
    """Put the repository's packages on `sys.path`, once. Reports whether it did.

    Returns `False` when the directory is not there, which is the honest answer
    for a repository checkout: the caller can then fall back to whatever path the
    packages are already importable from, rather than failing on a mount point
    that only exists inside the container.
    """
    directory = Path(source or os.environ.get("OPEN_HOUSE_SRC") or DEFAULT_SOURCE)
    if not directory.is_dir():
        return False
    location = str(directory)
    if location not in sys.path:
        sys.path.insert(0, location)
    return True


install()
