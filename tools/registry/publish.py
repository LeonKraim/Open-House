"""Publishing: a pack exported as a template, then a pointer and a pull request.

The publish button is two acts and this module is both of them. `export_template`
turns a manifest into something a third party can hand over -- its `provides`
paths rewritten relative to the repository root, which is the base the sandbox
resolves them against and the form the manifest schema calls a *repo-relative
path* -- and `publish` computes the digest that pins it, writes the pointer file,
and builds the pull request that submits it. The publisher has nothing left to
write by hand; what they open is the prefilled pull request. A pack developed
outside its repository names its pinned files absolutely, which the sandbox
refuses at install; the export is what turns that into the one form that
installs.

The pointer is written into `pointers/<tier>/<name>/<version>.yaml`, which is
the layout `index.pointer_files` reads, so publishing and generating cannot
disagree about where a pointer lives. The generated index is *not* touched:
adding a pointer is committed to the pull request, and the index is regenerated
on merge, which is what keeps the two from drifting in a branch nobody re-ran.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

import yaml

from engine.install import digest as pack_digest
from engine.sandbox import load_pack
from openhouse import packs
from tools.catalog.narrow import as_sequence

from .errors import RegistryError
from .layout import POINTERS_DIRECTORY
from .pointer import Pointer, load_pointer

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "DEFAULT_BASE",
    "Publication",
    "PullRequest",
    "export_template",
    "publish",
]

#: The branch a publishing pull request targets.
DEFAULT_BASE = "main"


@dataclass(frozen=True, slots=True)
class PullRequest:
    """The submission, prefilled, as a publisher opens it."""

    title: str
    body: str
    branch: str
    base: str
    labels: tuple[str, ...] = ()

    def to_document(self) -> dict[str, object]:
        return {
            "title": self.title,
            "body": self.body,
            "branch": self.branch,
            "base": self.base,
            "labels": list(self.labels),
        }


@dataclass(frozen=True, slots=True)
class Publication:
    """What a publish produced: where the pointer landed, and what to open."""

    pointer: Pointer
    pointer_path: Path
    pull_request: PullRequest

    def to_document(self) -> dict[str, object]:
        return {
            "pointer": self.pointer.to_document(),
            "pointer_path": self.pointer_path.as_posix(),
            "pull_request": self.pull_request.to_document(),
        }


def export_template(
    manifest_path: Path,
    *,
    root: Path | None = None,
    name: str | None = None,
    description: str | None = None,
) -> dict[str, object]:
    """The manifest as a shareable template: its pinned paths made repo-relative.

    A published pack's `provides` paths are resolved against the repository root
    at install time, so a pack developed outside the tree -- whose pinned files
    are named absolutely -- has to be rewritten before it is published, and that
    is what this does. `root` is the repository the pack will be installed into;
    every absolute path that lands inside it is rewritten to the repo-relative
    path the schema asks for. A path that escapes `root` is left alone, because
    the sandbox refuses it afterwards and quietly rewriting it would turn a
    refusal into a silent repair.

    `root` is optional because a publisher whose manifest already carries
    repo-relative paths has nothing to rewrite, and because a repository named by
    URL has no local tree to be relative to.

    `name` and `description` are overridden only when they are given, because a
    template that blanked a field the caller did not mention would export a pack
    nobody could identify.
    """
    document = dict(packs.load_manifest(manifest_path))
    if isinstance(document.get("provides"), list):
        base = None if root is None else root.resolve()
        document["provides"] = [
            _relative_entry(base, entry)
            for entry in as_sequence(document.get("provides"))
        ]
    if name is not None:
        document["name"] = name
    if description is not None:
        document["description"] = description
    return document


def publish(
    registry_root: Path,
    manifest_path: Path,
    *,
    repo: str,
    commit: str,
    tier: str,
    path: str | None = None,
    previous: Path | None = None,
    base: str = DEFAULT_BASE,
) -> Publication:
    """Write the pointer for `manifest_path` and build its pull request.

    The digest is computed over the manifest as it is on disk, which is the same
    document `export_template` produces once the publisher has written it out;
    the pointer therefore pins what would actually be installed and not a
    reformatted cousin of it.

    `previous` is the manifest of the version this one supersedes, when there is
    one, and it exists so the pull request can state what the update *gains*. A
    reviewer reading "adds `lock.unlock`" is reading the one fact that decides
    the review, and leaving them to diff two manifests is leaving the important
    line to be found.
    """
    document = packs.load_manifest(manifest_path)
    pointer = _pointer_for(
        document,
        manifest_path=manifest_path,
        repo=repo,
        commit=commit,
        tier=tier,
        path=path,
    )
    destination = (
        registry_root
        / POINTERS_DIRECTORY
        / pointer.tier
        / pointer.name
        / f"{pointer.version}.yaml"
    )
    if destination.exists():
        existing = load_pointer(destination)
        if existing != pointer:
            raise RegistryError(
                destination.as_posix(),
                f"already publishes {pointer.name} {pointer.version}; "
                "a published version is immutable, so publish a new version",
            )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        yaml.safe_dump(pointer.to_document(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
        newline="",
    )
    return Publication(
        pointer=pointer,
        pointer_path=destination,
        pull_request=_pull_request(pointer, manifest_path, previous),
    )


def _pointer_for(
    document: Mapping[str, object],
    *,
    manifest_path: Path,
    repo: str,
    commit: str,
    tier: str,
    path: str | None,
) -> Pointer:
    name = document.get("name")
    version = document.get("version")
    if not isinstance(name, str) or not isinstance(version, str):
        raise RegistryError(
            manifest_path.as_posix(), "carries no usable `name` and `version`"
        )
    return Pointer(
        name=name,
        version=version,
        repo=repo,
        commit=commit,
        path=path if path is not None else _within(repo, manifest_path),
        sha256=pack_digest(document),
        tier=tier,
    )


def _within(repo: str, manifest_path: Path) -> str:
    """The manifest's path inside `repo`, when `repo` is a directory it is under.

    A `repo` that is a URL has no local tree to be relative to, so the manifest's
    own name is the only honest answer and the publisher is expected to have
    passed `path` for anything deeper.
    """
    root = Path(repo)
    if root.is_dir():
        try:
            return manifest_path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            pass
    return manifest_path.name


def _relative_entry(base: Path | None, entry: object) -> object:
    """One `provides` entry with an inside-the-root absolute path made relative."""
    if not isinstance(entry, dict):
        return entry
    rewritten: dict[str, object] = dict(cast("Mapping[str, object]", entry))
    value = rewritten.get("path")
    if base is not None and isinstance(value, str) and Path(value).is_absolute():
        try:
            rewritten["path"] = Path(value).resolve().relative_to(base).as_posix()
        except ValueError:
            return rewritten
    return rewritten


def _pull_request(
    pointer: Pointer, manifest_path: Path, previous: Path | None
) -> PullRequest:
    body = _body(pointer, manifest_path, previous)
    return PullRequest(
        title=f"Publish {pointer.name} {pointer.version} ({pointer.tier})",
        body=body,
        branch=f"registry/{pointer.name}-{pointer.version}",
        base=DEFAULT_BASE,
        labels=(pointer.tier, "registry"),
    )


def _body(pointer: Pointer, manifest_path: Path, previous: Path | None) -> str:
    """The pull request body: what is being published, and what it may do."""
    pack = load_pack(manifest_path)
    permissions = sorted(pack.effective_permissions)
    gained = _gained(previous, manifest_path)
    lines = [
        f"Publishes **{pointer.name} {pointer.version}** to the `{pointer.tier}` tier.",
        "",
        f"- source: `{pointer.repo}` at `{pointer.commit}`",
        f"- manifest: `{pointer.path}`",
        f"- digest: `{pointer.sha256}`",
        f"- slots: {', '.join(f'`{slot}`' for slot in pack.declared_slots) or 'none'}",
        f"- services: {', '.join(f'`{service}`' for service in permissions) or 'none'}",
    ]
    if gained:
        lines.append("")
        lines.append("This version adds permissions over the previous one:")
        lines.append("")
        lines.extend(f"- `{service}`" for service in gained)
    return "\n".join(lines) + "\n"


def _gained(previous: Path | None, current: Path) -> Sequence[str]:
    """The services `current` declares that `previous` did not, or nothing."""
    if previous is None:
        return ()
    before = load_pack(previous).effective_permissions
    after = load_pack(current).effective_permissions
    return sorted(after - before)
