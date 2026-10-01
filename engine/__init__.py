"""The Open House engine: pure logic, no Home Assistant.

This package is the whole of the system's behaviour. It is kept free of
`homeassistant` imports -- including guarded ones under `try:` and ones under
`TYPE_CHECKING` -- because the engine has to be testable against a fake house
without a running HA instance, and because the simulator, the CLI and the MCP
server all need to drive the same logic the integration drives.

Phase 0 ships this package empty on purpose: the invariant is *asserted* here,
by a check that reads the source, and only *exercised* from Phase 1 onward.
"""
