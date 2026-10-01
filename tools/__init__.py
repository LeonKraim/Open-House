"""Open House project tooling.

The `tools` package holds build- and validation-time code. It is a package in
its own right so that `tools.catalog` cannot be confused with the `catalog/`
data directory at the repository root -- the two are different things with the
same word in them, and importing one when you meant the other is the kind of
error that resolves silently.
"""
