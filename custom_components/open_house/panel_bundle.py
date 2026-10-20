"""Where the built sidebar panel is, in either of the two ways it is installed.

The panel is one file -- `panel/open-house-panel.js`, the ES module
`panel_custom` loads -- and it is a *build artefact*, so it is not committed and
it is not under `custom_components/` in the repository. That leaves the same two
deployments `_bootstrap` answers for the packages, and this module answers the
same question for the file the sidebar tab loads:

* The release asset, which `tools/package_integration.py` builds with the bundle
  *inside* the integration at `panel/open-house-panel.js`, because a HACS install
  copies `custom_components/open_house/` and nothing beside it. This is the
  shipped copy, and it is the one that wins.
* A checkout, where `panel/dist/` is what `npm run build` emits and
  `docker/docker-compose.yml` mounts it into the instance's `www/` directory --
  the same place a person who installs by hand is told to put it.

**The shipped copy wins, and the order is the opposite of `_bootstrap`'s on
purpose.** There, the mount wins because `OPEN_HOUSE_SRC` is an explicit
statement about one deployment. Here neither location is explicit, and the two
differ in a way that matters: the copy beside this module was built with *this*
integration and speaks the commands it registers, whereas a file in `www/` is
whatever was last copied there. A stale bundle is the exact failure
`PanelBundleView` refuses to cache -- a screen running last month's controls
against this month's server -- so the bundle that cannot drift is preferred to
the one that can.

It is deliberately stdlib-only, like `_bootstrap`: `tests/test_packaging.py`
loads it by path and exercises both branches without Home Assistant, because a
resolution rule that is only ever run one way is a rule that has never been seen
to answer the other.
"""

from __future__ import annotations

from pathlib import Path

#: The directory the bundle is shipped in, beside this module. The same name in
#: the archive and in a checkout that has one, so there is one name to know.
SHIPPED_DIRECTORY = "panel"

#: The bundle's filename, fixed by `panel/vite.config.ts` and by the element the
#: file registers; it is named here rather than at each call site because the
#: packager, the view and the tests all have to agree about it.
BUNDLE_NAME = "open-house-panel.js"

#: Where a checkout's build is mounted, relative to Home Assistant's config
#: directory: `panel/dist/open-house-panel.js` is mounted into `www/`, which Home
#: Assistant serves at `/local/`. The panel is *not* registered against that URL
#: -- `PanelBundleView` reads the same file per request and answers `no-store` --
#: so this is a location the file is read *from* and not one it is served at.
MOUNTED_DIRECTORY = "www"


def bundle_path(config_dir: Path, integration_dir: Path | None = None) -> Path | None:
    """The bundle to serve, or `None` when this instance has none.

    `None` is the honest answer for a checkout whose panel has not been built
    and for an install that shipped without one, and it is the caller's to
    report: the view answers a sentence naming the file rather than a blank
    screen, because "the panel has not been built" and "the panel is broken" are
    different problems with different fixes.

    `integration_dir` is where this module's own directory is assumed to be. It
    is a parameter only so the rule can be exercised against a directory that is
    not this one; every caller in the integration leaves it out.
    """
    here = integration_dir if integration_dir is not None else Path(__file__).parent
    shipped = here / SHIPPED_DIRECTORY / BUNDLE_NAME
    if shipped.is_file():
        return shipped
    mounted = config_dir / MOUNTED_DIRECTORY / BUNDLE_NAME
    return mounted if mounted.is_file() else None
