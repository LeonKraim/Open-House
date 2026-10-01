"""The scenario corpus's package: what a scenario is, and what running one says.

One package, six modules, and the imports between them are a direction rather
than a graph: `assertions` and `model` are the types, `dsl` compiles a step,
`loader` reads a document into those types, `runner` executes one, and `report`
is what a failure writes. Nothing imports upwards -- `loader` does not know a
runner exists and `assertions` does not know a log is retained anywhere -- which
is what lets a corpus be validated without a single entity being created.

`invariants` is deliberately **not** imported here. It is the module that pulls
in Hypothesis, and a scenario runner is a runtime dependency of the CLI and the
MCP server while a property test is not: importing the package in order to run a
scenario should not load a testing framework. The invariants are reached by name,
from the suite that means to run properties, and their absence from this list is
the statement that nothing else needs them.
"""

from __future__ import annotations

from .assertions import LogExpectation, Mismatch, StateExpectation, matches
from .dsl import STEP_VERBS, InvalidStepError, UnknownDocumentError, UnknownVerbError
from .loader import LoadError, load_directory, load_scenario
from .model import Expectation, Given, Scenario, Step, Then
from .report import FailureReport, ScenarioFailed, plain
from .runner import RunResult, SessionFactory, run_directory, run_scenario

__all__ = [
    "STEP_VERBS",
    "Expectation",
    "FailureReport",
    "Given",
    "InvalidStepError",
    "LoadError",
    "LogExpectation",
    "Mismatch",
    "RunResult",
    "Scenario",
    "ScenarioFailed",
    "SessionFactory",
    "StateExpectation",
    "Step",
    "Then",
    "UnknownDocumentError",
    "UnknownVerbError",
    "load_directory",
    "load_scenario",
    "matches",
    "plain",
    "run_directory",
    "run_scenario",
]
