"""Pack manifests written to a temporary tree, wrong in one way at a time.

The builder two test modules share: `test_pack_install.py` needs a manifest wrong
in each of the ways the four checks refuse, and `test_pack_arbitration.py` needs
two packs that reach for the same light. Both are generated rather than committed,
because what is under test in the first is a *defect* -- a range nothing
satisfies, a banned service -- and a committed fixture per failure mode would be
a directory of files whose defects a reader has to diff to find.

Not a test module: the name has no `test_` prefix, so the runner does not collect
it. It carries no assertions of its own either, except the one that makes a
mutation visible -- `edit_line` fails when its anchor is gone rather than quietly
writing an unchanged manifest, which is the failure mode a fixture-editing helper
is most likely to have.
"""

from __future__ import annotations

from pathlib import Path

#: The file every generated manifest pins. Shaped the way the sandbox reads an
#: automation -- a `trigger` and an `action` -- because a `provides` entry
#: declaring class `automation` has to pin a file of that class, and the class is
#: read off the file rather than declared beside it.
PINNED = """\
alias: Pinned automation
trigger:
  - platform: state
    entity_id: motion_sensor
    to: "on"
action:
  - service: light.turn_on
    target:
      entity_id: light_group
"""

#: The slot every generated pack declares and every generated behaviour reaches
#: through. `light_group` is the one slot the `minimal` fixture binds that a
#: service can sensibly act on, so a pack built from this template is a pack that
#: fixture house satisfies.
SLOT = "light_group"


def behaviours(count: int, services: tuple[str, ...], priority: int | None) -> str:
    """`count` behaviour rows, each declaring the same slot and services.

    Generated rather than written out because one check needs six of them, and
    the property under test -- that six arrive six-disabled -- is about the count
    and not about the rows.
    """
    declared = ", ".join(services)
    rank = "" if priority is None else f"    priority: {priority}\n"
    return "".join(
        f"  - name: b{index}\n"
        "    trigger: state\n"
        "    condition: state\n"
        "    action: service\n"
        f"{rank}"
        f"    services: [{declared}]\n"
        f"    slots: [{SLOT}]\n"
        for index in range(count)
    )


def block(header: str, rows: tuple[tuple[str, str], ...]) -> list[str]:
    """A `dependencies`-shaped clause, or nothing when there are no rows."""
    if not rows:
        return []
    return [f"{header}:"] + [
        f"  - name: {other}\n    range: '{span}'" for other, span in rows
    ]


def pack(
    directory: Path,
    name: str,
    *,
    version: str = "1.0.0",
    count: int = 1,
    priority: int | None = None,
    requires: tuple[str, ...] = (SLOT,),
    dependencies: tuple[tuple[str, str], ...] = (),
    conflicts: tuple[tuple[str, str], ...] = (),
    services: tuple[str, ...] = ("light.turn_on",),
    edits: tuple[tuple[str, str], ...] = (),
) -> Path:
    """Write a manifest and the file it pins under `directory/name`, returning it.

    Each pack gets its own directory, because a name is what an installed set is
    keyed by and two packs written to one path would be one file the second call
    overwrote -- so a check about two packs would be a check about one.

    The `provides` path is absolute because these packs live outside the checkout:
    the sandbox resolves a `provides` path against the repository root and
    requires the answer to stay inside the pack's own directory, which a pack
    outside the tree can only satisfy by saying where it is.
    """
    home = directory / name
    home.mkdir(parents=True, exist_ok=True)
    (home / "thing.yaml").write_text(PINNED, encoding="utf-8")

    lines = [
        f"name: {name}",
        f'version: "{version}"',
        "description: a pack for the test",
        "kind: module",
        'engine_api: ">=1.0.0 <2.0.0"',
        "license: mit",
        f"requires_slots: [{', '.join(requires)}]",
    ]
    lines += block("dependencies", dependencies)
    lines += block("conflicts", conflicts)
    lines += [
        "provides:",
        f"  - path: {(home / 'thing.yaml').as_posix()}",
        "    class: automation",
        "behaviours:",
        behaviours(count, services, priority).rstrip("\n"),
        "i18n:",
        "  default:",
        f"    pack: {name}",
        "    description: a pack for the test",
        *(f"    b{index}: behaviour {index}" for index in range(count)),
    ]

    text = "\n".join(lines) + "\n"
    for prefix, replacement in edits:
        text = edit_line(text, prefix, replacement)
    manifest = home / "manifest.yaml"
    manifest.write_text(text, encoding="utf-8")
    return manifest


def edit_line(text: str, prefix: str, replacement: str) -> str:
    """Replace the first line starting with `prefix`, and fail if there is none.

    Anchored at the start of a line rather than at the string's first occurrence:
    a manifest's own prose may mention a clause, and an unanchored replace would
    rewrite the comment and leave the clause alone.
    """
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            lines[index] = replacement + ("\n" if line.endswith("\n") else "")
            return "".join(lines)
    raise AssertionError(f"no line starts with {prefix!r}")
