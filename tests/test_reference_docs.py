"""The reference docs, and the repository paths they spell -- task 1.6.

A reference page is read by someone who cannot check it: a path in it is taken as
a fact about the repository, and a path that has moved is a fact that has quietly
become false. Task 1.6's verify clause is that the documented paths exist as
written, and this module is that clause over the whole of `docs/` -- every
backticked repository path and every link target, in every markdown file under
it. The scope is the directory rather than the page the task writes, because a
page whose paths are checked only while it is new is one whose paths rot the
moment it stops being new, and the Phase 0 and Phase 1 verification records were
edited after they were written.

Two forms are read as paths, because they are how a repository path is actually
written in these pages: an inline path in backticks, resolved from the repository
root; and a link's target, resolved from the page's own directory, with any
`#fragment` ignored. A trailing citation of where *inside* the path is not part
of it -- `tools/catalog/schemas.py::check_immutability` and
`catalog/README.md:263` name the file and then a symbol or a line, and the file
is what must exist.

Four shapes are deliberately not read, each for its own reason:

- a token with a space, `*` or `<>` in it: a placeholder such as
  `schemas/<concept>/1.0.0.json` is a shape rather than a location;
- a token whose last segment has no extension: `ressources/` and `.local/` are
  cited as directories a check runs in or refuses to run in, and both are absent
  in CI by design, so a reader cannot open them here or there;
- a token whose first segment is not a top-level entry of the repository: a bare
  capability name like `control-surface/spec.md` is a spec delta inside a change
  package, not a repository path;
- anything on a `fixture:` line, because the verification records describe trees
  their tests *build*: "a fake `engine/leak.py` importing `openhouse`" names a
  module that is hypothetical by construction, and reading it as a path would
  fail the check on a record that is right.

The last section checks the *content* of the page task 1.6 writes, and derives
what it expects from the artifacts rather than typing it out: every published
version of the two concepts this phase publishes, and every licence code with its
SPDX identifier. A page that documented the chain as it stood when it was written
would be the document equivalent of a hard-coded version.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from tools.catalog import paths
from tools.catalog.schemas import load_versions

from .conftest import write

ROOT = Path(__file__).resolve().parents[1]

#: The documentation this check is over. The whole directory rather than a list
#: of pages: a page added later is checked without anyone remembering to add it.
DOCUMENTS = ROOT / "docs"

#: The page task 1.6 writes, whose content is checked as well as its paths.
PAGE = DOCUMENTS / "reference" / "pack-versions-and-licences.md"

#: An inline path and a link's target. Both are read on every line.
_INLINE = re.compile(r"`([^`\n]+)`")
_LINK = re.compile(r"\]\(([^)\s]+)\)")

#: A file name: the last segment carries an extension.
_FILE = re.compile(r"^[^./][^/]*\.[A-Za-z0-9]+$")

#: The top-level entries of this repository, so that a token whose first segment
#: names none of them is not a path from the root. Read from the tree rather than
#: listed, because a second list would be one more thing to keep in step with the
#: checkout.
_TOP_LEVEL = frozenset(entry.name for entry in ROOT.iterdir())


def _location(text: str) -> str:
    """The part of a token that names a file: no fragment, no symbol, no line."""
    return text.split("#", 1)[0].split(":", 1)[0]


def _is_repository_path(text: str) -> bool:
    """Whether a token is a claim about where a file in this repository is."""
    if not text or any(character in text for character in " \t*<>"):
        return False
    if "://" in text or text.startswith(("#", "mailto:")):
        return False
    location = _location(text)
    if "/" not in location:
        return False
    head = location.lstrip("./").split("/")[0]
    return head in _TOP_LEVEL and bool(_FILE.match(location.rsplit("/", 1)[-1]))


def _claims(document: Path) -> list[tuple[str, Path]]:
    """Every path claim `document` makes, each with the directory it is read from.

    The two forms take different bases and that is the whole of why a claim
    carries one: an inline path is a repository path wherever the page sits, and
    a link target is relative to the page that carries it.
    """
    claims: list[tuple[str, Path]] = []
    for line in document.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("fixture:"):
            continue
        for target in _LINK.findall(line):
            claims.append((target, document.parent))
        for token in _INLINE.findall(line):
            claims.append((token, ROOT))
    return claims


def test_every_path_spelled_in_the_documentation_exists() -> None:
    """Task 1.6's verify clause, over every markdown file under `docs/`.

    A falsifying implementation that collected nothing from a document -- or that
    skipped one it could not read -- would pass this test on a documentation tree
    where every path had moved, so the set the scan collected is asserted against
    paths that are known to be in it, from pages written before this task.

    The diagnostic names the page as well as the path, because the fix is an edit
    to a page and the same wrong path can appear in several: "a path somewhere in
    `docs/` does not exist" is not a defect anyone can act on.
    """
    documents = sorted(DOCUMENTS.rglob("*.md"))
    assert documents, "there is no documentation under docs/ to check"

    missing: list[str] = []
    checked: set[str] = set()
    for document in documents:
        where = document.relative_to(ROOT).as_posix()
        for text, base in _claims(document):
            if not _is_repository_path(text):
                continue
            resolved = (base / _location(text)).resolve()
            try:
                # Every claim is named by where it lands inside the repository,
                # which is also the check that a link cannot leave it: a page
                # pointing above the root is a claim about a file outside the
                # thing the page documents.
                checked.add(resolved.relative_to(ROOT).as_posix())
            except ValueError:
                missing.append(f"{where} -> {text} (outside the repository)")
                continue
            if not resolved.exists():
                missing.append(f"{where} -> {text} (absent)")
    assert checked >= {
        "catalog/licenses.yaml",
        "schemas/engine-api/1.0.0.json",
        "tools/catalog/schemas.py",
    }, (
        "the scan collected none of the paths the shipping pages spell, so it is "
        "reading nothing: " + ", ".join(sorted(checked))
    )
    assert not missing, "; ".join(missing)


def test_the_scan_reads_the_two_forms_and_skips_what_is_not_a_path(
    tmp_path: Path,
) -> None:
    """The half of the check that can be wrong without the other half noticing.

    A scan that read every backticked token would fail the documentation on a
    placeholder or on `ressources/`, and a scan that read none would pass it on a
    tree where nothing existed. The subject here is a page written for the test,
    which is the only subject that can be *known* to hold each shape: the four
    shapes the check leaves alone, the two forms it reads, and one path that does
    not exist, so that the collecting half and the resolving half are both seen
    to work.
    """
    write(tmp_path, "catalog/slots.yaml", "slots: []\n")
    # Two levels down, which is where a page that links `../../catalog/...` has
    # to sit for the link to reach the fixture's own catalog: a page one level
    # down would resolve the same link above the fixture root, and the test would
    # be asserting the wrong thing about the base a link is read from.
    document = write(
        tmp_path,
        "docs/reference/page.md",
        "\n".join(
            [
                "[catalog](../../catalog/slots.yaml)",
                "`tools/catalog/schemas.py::check_immutability`",
                "`catalog/README.md:263`",
                "`schemas/pack-manifest/1.2.0.json`",
                "`schemas/pack-manifest/9.9.9.json`",
                "`schemas/<concept>/1.0.0.json`",
                "`ressources/`",
                "`mit`",
                "[the site](https://example.invalid/x)",
                "[this page](#a-page)",
                'fixture: "a fake tree whose `engine/leak.py` imports `openhouse`"',
                "",
            ]
        ),
    )

    claims = _claims(document)
    assert [text for text, _ in claims if _is_repository_path(text)] == [
        "../../catalog/slots.yaml",
        "tools/catalog/schemas.py::check_immutability",
        "catalog/README.md:263",
        "schemas/pack-manifest/1.2.0.json",
        "schemas/pack-manifest/9.9.9.json",
    ]
    assert [
        text
        for text, base in claims
        if _is_repository_path(text) and not (base / _location(text)).exists()
    ] == ["schemas/pack-manifest/9.9.9.json"]


def test_the_pack_versions_page_names_every_published_version() -> None:
    """The page's subject is the chain, so the chain is read off the tree.

    The two concepts this phase publishes are the manifest format and the engine
    API, and what is expected here is every version the tree holds for each
    rather than the two versions the task happens to name: a successor published
    later must be added to the page, and a page listing only the versions that
    existed when it was written is a page that goes stale in silence.
    """
    text = PAGE.read_text(encoding="utf-8")
    for concept in ("pack-manifest", "engine-api"):
        versions = load_versions(concept)
        assert versions, f"`{concept}` publishes no versions to document"
        for version in versions:
            assert version.relative in text, (
                f"`{version.relative}` is published but not named in "
                f"{PAGE.relative_to(ROOT).as_posix()}"
            )


def test_the_pack_versions_page_names_every_licence_code_and_identifier() -> None:
    """A code a manifest may declare, and the SPDX identifier behind it.

    Both are read from `schemas/catalog/licenses.json`, so publishing a code
    without giving it an identifier -- or without documenting either -- fails
    here rather than in a consumer that reported the wrong thing.
    """
    loaded: object = json.loads(
        (paths.SCHEMA_CATALOG / "licenses.json").read_text(encoding="utf-8")
    )
    assert isinstance(loaded, dict)
    definitions: Any = loaded["$defs"]
    text = PAGE.read_text(encoding="utf-8")
    for code in definitions["licenceValue"]["enum"]:
        assert f"`{code}`" in text, f"`{code}` is a published licence code"
        identifier = definitions["licenceSpdx"]["properties"][code]["const"]
        assert f"`{identifier}`" in text, (
            f"`{code}` carries the SPDX identifier `{identifier}`"
        )
