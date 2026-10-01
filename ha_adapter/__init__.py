"""The Home Assistant adapter: the only place the engine meets HA.

Everything that knows about `hass`, entity states, services and the event bus
lives here, and the engine reaches it through an interface rather than an
import. That is what lets the same engine run against a real house, a recorded
trace, or the simulator.
"""
