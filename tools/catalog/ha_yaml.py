"""Home Assistant's YAML dialect: a loader that knows its tags.

The four reference repositories are not plain YAML. They are Home Assistant
configuration, and HA adds a set of custom tags -- `!include`, `!secret`,
`!include_dir_merge_named`, `!input` -- whose arguments name *other files* or
*other values* that do not live in the document being read. `yaml.safe_load`
raises on every one of them, so a parser that used it would report most of a real
HA configuration as unparseable, and the inventory would record `unparsed`
against files that are perfectly valid.

The obvious repair -- a bare `yaml.load` with a permissive loader -- is worse
than the problem. `SafeLoader` refuses these tags precisely because resolving
them is an operation on the filesystem, and a loader that resolved them would be
a loader that reads a file the caller never named.

So the tags are *represented* rather than resolved. `!include automations.yaml`
becomes a `Tag` carrying the tag and its argument, and the structure around it
stays intact. Two things follow, and both are wanted: the document parses, and
the places where a value lives in another file stay visible as exactly that. A
`Tag` is emphatically not a string, so nothing downstream can mistake
`!secret mqtt_password` for an identifier, a template reference or authored text
-- which matters because the extraction's whole job is to tell those apart.

Every tag is admitted rather than the seven HA documents, and that is a measured
decision rather than a cautious one. The tags standing at a node position in the
four repositories are `!input` (2225), `!include` (969), `!secret` (301), the
four `!include_dir_*` forms (55) -- and `!lambda` once, which is ESPHome's and is
in no Home Assistant reference. A list drawn from HA's documentation would have
missed the one tag a list was supposed to cover, which is the argument against
drawing the list at all: the tag set belongs to whichever integration wrote the
file, and a parse outcome that depended on our knowing every integration is a
parse outcome that goes stale silently. Admitting every tag is also the safer
direction -- an unadmitted tag becomes a parse *failure*, and a failure is
recorded against a file as a fact about it.
"""

from __future__ import annotations

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode, ScalarNode, SequenceNode


class Tag:
    """A custom-tagged node, kept as a value rather than resolved.

    Deliberately not a subclass of `str`, and deliberately without `__eq__`
    against one: every extraction rule in this project is of the form "a value
    in this position that looks like a `domain.object_id`", and a `Tag` that
    compared equal to its own argument would be indistinguishable from the
    authored string `automations.yaml`. The class boundary *is* the check.
    """

    __slots__ = ("name", "value")

    def __init__(self, name: str, value: object) -> None:
        self.name = name
        self.value = value

    def __repr__(self) -> str:  # pragma: no cover - for debugging only
        return f"Tag({self.name!r}, {self.value!r})"

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, Tag)
            and other.name == self.name
            and other.value == self.value
        )

    def __hash__(self) -> int:
        return hash((self.name, repr(self.value)))


class _Loader(yaml.SafeLoader):
    """`SafeLoader` with the HA tags admitted.

    A subclass rather than `add_multi_constructor` on `SafeLoader` itself,
    because the latter mutates a class the whole process shares: the catalog's
    own files are read with the plain loader elsewhere, and a tag registered here
    would begin resolving there too.
    """


def _construct_tag(loader: _Loader, suffix: str, node: yaml.Node) -> Tag:
    name = f"!{suffix}"
    if isinstance(node, ScalarNode):
        return Tag(name, loader.construct_scalar(node))
    if isinstance(node, SequenceNode):
        return Tag(name, loader.construct_sequence(node, deep=True))
    if isinstance(node, MappingNode):
        return Tag(name, loader.construct_mapping(node, deep=True))
    raise ConstructorError(  # pragma: no cover - every node is one of the three
        None,
        None,
        f"cannot construct a tag from {type(node).__name__}",
        node.start_mark,
    )


# PyYAML's annotations declare this as returning and accepting `Unknown`, so
# strict mode reports the call rather than anything wrong with it. The gap is in
# the library's stubs -- the same shape as the typer option in `cli.py` -- and the
# suppression is scoped to the one call rather than to the module.
_Loader.add_multi_constructor("!", _construct_tag)  # pyright: ignore[reportUnknownMemberType]


def load(text: str) -> object:
    """Parse one Home Assistant YAML document, tags and all.

    Raises whatever `yaml.safe_load` raises for text that is not YAML -- the
    caller records the error rather than swallowing it, since a file that will
    not parse is a fact about the corpus.
    """
    return yaml.load(text, Loader=_Loader)


def parse(text: str) -> tuple[object, str | None]:
    """A document and the error that stopped it, one of which is always None.

    Returned as a pair rather than raising because both outcomes are ordinary
    here: the inventory records the error against the file, and the caller has
    nowhere better to put it.
    """
    try:
        return load(text), None
    except (yaml.YAMLError, RecursionError) as exc:
        return None, _one_line(exc)


def _one_line(exc: Exception) -> str:
    """A YAML error as one line of prose.

    `yaml.safe_load` renders a marked error as a multi-line block naming the
    context, the problem and the position. That block belongs in a terminal and
    not in `catalog/inventory.json`, which is a committed artifact compared byte
    for byte between runs -- and a `str(exc)` carrying a trailing newline would
    put the whole block there. The location is kept because it is the part that
    makes the message actionable.
    """
    mark = getattr(exc, "problem_mark", None)
    problem = getattr(exc, "problem", None) or str(exc).strip().splitlines()[0]
    if mark is None:
        return str(problem).strip()
    return f"{problem} (line {mark.line + 1}, column {mark.column + 1})"
