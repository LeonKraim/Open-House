"""The composition root: the facade, the operation registry, the CLI, the MCP.

This package is the one place `engine/` and `sim/` are wired together -- the
library facade a caller drives, the ten operations it exposes, and the CLI and
MCP server that are thin adapters over them. It sits beside `engine/` rather
than inside it (`design.md` D12) because the engine must not import the
simulator: a facade living in `engine/` would give the engine a second outward
edge beside the `HouseAdapter` port and drag `sim/` into every engine test.

The dependency edge points one way. This package imports `engine/` and `sim/`;
neither imports it, which the composition-root scan in
`tools/catalog/invariants.py` enforces rather than trusts. Phase 1 ships this
package as the skeleton the `control-surface` capability fills in; the facade,
the registry, the CLI and the MCP server arrive with their own tasks.
"""
