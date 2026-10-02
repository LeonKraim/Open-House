"""`python -m tools.registry.cli`: generate the index, check a pack, publish one.

Three commands, one per act a maintainer or a publisher performs. `generate`
writes `index.json` and `revoked.json` from the pointer tree and the revocation
list, and `--check` runs the same generation without writing and fails when the
committed files differ -- which is the command CI runs. `check` runs the
publishing checks against a pack on this machine without publishing it, which is
what an author wants before opening a pull request. `publish` writes the pointer
and prints the prefilled pull request.

The exit codes are the pack CLI's three, for the same reasons: `0` when
everything passed, `1` when the thing under test failed its checks, and `2` when
the caller named something that cannot be read. "This pack is broken" and "I
called this wrong" have different authors, and a single non-zero code would make
the pipeline unable to tell them apart.

`argparse` rather than `typer`, because this module sits under the strict
type-checked `tools/` tree and the option library's helpers arrive untyped.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from engine.install import digest as pack_digest
from openhouse import packs

from .checks import Submission, run_checks
from .errors import RegistryError
from .index import generate, load_index, verify_current
from .layout import default_root
from .pointer import Pointer, PointerError
from .publish import publish

if TYPE_CHECKING:
    from collections.abc import Sequence

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2


def main(argv: Sequence[str] | None = None) -> int:
    """Run the registry CLI, returning the process exit code."""
    parser = _parser()
    args = parser.parse_args(argv)
    command = args.command
    if command == "generate":
        return _generate(args)
    if command == "check":
        return _check(args)
    if command == "publish":
        return _publish(args)
    parser.error(f"unknown command {command!r}")
    return EXIT_USAGE


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.registry.cli")
    parser.add_argument(
        "--registry",
        type=Path,
        default=None,
        help="the registry tree; default ./registry",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    generate = commands.add_parser("generate", help="write the generated files")
    generate.add_argument(
        "--check", action="store_true", help="fail if the committed files are stale"
    )

    check = commands.add_parser("check", help="check a pack without publishing it")
    check.add_argument("manifest", type=Path, help="the pack manifest to check")
    check.add_argument("--repo", default=".", help="the pack's source repository")
    check.add_argument(
        "--commit", default="0000000", help="the revision being published"
    )
    check.add_argument("--tier", default="community", help="the tier to publish under")
    check.add_argument(
        "--previous", type=Path, default=None, help="the superseded manifest"
    )

    publish_cmd = commands.add_parser(
        "publish", help="write a pointer and print its PR"
    )
    publish_cmd.add_argument("manifest", type=Path, help="the pack manifest to publish")
    publish_cmd.add_argument(
        "--repo", required=True, help="the pack's source repository"
    )
    publish_cmd.add_argument(
        "--commit", required=True, help="the revision being published"
    )
    publish_cmd.add_argument(
        "--tier", default="community", help="the tier to publish under"
    )
    publish_cmd.add_argument(
        "--previous", type=Path, default=None, help="the superseded manifest"
    )
    return parser


def _generate(args: argparse.Namespace) -> int:
    root = _root(args.registry)
    if args.check:
        findings = verify_current(root)
        for finding in findings:
            print(finding, file=sys.stderr)
        return EXIT_FAILURE if findings else EXIT_OK
    generated = generate(root)
    print(
        f"wrote {generated.index_path.name} with {len(generated.index.entries)} entries "
        f"and {generated.revoked_path.name} with {len(generated.revocations)} revocations"
    )
    return EXIT_OK


def _check(args: argparse.Namespace) -> int:
    root = _root(args.registry)
    source = Path(args.manifest).resolve()
    try:
        pointer = _pointer_for(source, args.repo, args.commit, args.tier)
    except (RegistryError, PointerError) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    submission = Submission(
        pointer=pointer,
        manifest=source,
        root=_submission_root(source, args.repo),
        previous=args.previous,
    )
    report = run_checks(
        submission, existing_names=_published_names(root), registry_root=root
    )
    for finding in report.findings:
        print(f"[{finding.check}] {finding.where}: {finding.message}", file=sys.stderr)
    if not report.ok:
        return EXIT_FAILURE
    print(f"{source.name} passed the publishing checks")
    return EXIT_OK


def _publish(args: argparse.Namespace) -> int:
    root = _root(args.registry)
    try:
        publication = publish(
            root,
            Path(args.manifest).resolve(),
            repo=args.repo,
            commit=args.commit,
            tier=args.tier,
            previous=args.previous,
        )
    except (RegistryError, PointerError) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    print(f"wrote {publication.pointer_path.as_posix()}")
    print()
    print(publication.pull_request.title)
    print(publication.pull_request.body)
    return EXIT_OK


def _root(registry: Path | None) -> Path:
    return default_root() if registry is None else registry


def _submission_root(source: Path, repo: str) -> Path:
    """The repository a pack's `provides` paths are written against.

    `Submission.root` is defined as exactly that -- "the same base the engine
    resolves them against at install" -- and the `provides` paths in this
    repository are repo-relative (`packs/official/example_pack/motion_light.yaml`).
    Passing the manifest's own directory instead made every check resolve one
    directory too deep: `packs/official/` + `packs/official/...`, which does not
    exist, so `permissions:provides` reported "names no file" for *every* pack
    -- the already-published `example_pack` and `kitchen` among them. The
    maintainer's one pre-pull-request command could not pass for any pack, which
    is why the check is worth reaching for a repository that is not this one.

    `--repo` is what names the repository, so when it is a directory it is the
    answer. A `repo` that is a URL has no local tree -- the same case `publish`
    handles -- and there the manifest's directory is the only thing left.
    """
    candidate = Path(repo)
    return candidate.resolve() if candidate.is_dir() else source.parent


def _pointer_for(source: Path, repo: str, commit: str, tier: str) -> Pointer:
    """A pointer for a manifest on this machine, pinned by its own digest."""
    document = packs.load_manifest(source)
    name = document.get("name")
    version = document.get("version")
    if not isinstance(name, str) or not isinstance(version, str):
        raise RegistryError(source.as_posix(), "carries no usable `name` and `version`")
    return Pointer(
        name=name,
        version=version,
        repo=repo,
        commit=commit,
        path=source.name,
        sha256=pack_digest(document),
        tier=tier,
    )


def _published_names(registry_root: Path) -> Sequence[str]:
    """The names already in the index, or nothing when it has not been generated."""
    try:
        return load_index(registry_root).names()
    except RegistryError:
        return ()


if __name__ == "__main__":
    sys.exit(main())
