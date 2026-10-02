"""The `test` pack verb -- task 10.2.

`test` runs "the scenarios a pack ships", and the first thing the requirement
does not say is where those live: a pack directory holds pinned artefacts beside
its manifest, `scenarios/` is the phase-1 corpus, and `sim.scenario`'s loader
takes any path rather than a convention. So the verb takes the scenario paths a
caller names -- this suite's fixtures are the answer to a question the package
does not answer -- and the properties below are what the verb must hold whoever
answers it.

**"Untested" is not "passed".** A pack no scenario reaches has been demonstrated
by nothing, and the module's stance is that nothing absent is reported as a pass
(the same reason `validate` refuses to call an empty directory fine). That is
`PackTestReport.untested`, and `ok` is false whenever it holds.

**A failure carries its author.** Four classes and not one "failed": a scenario
that will not load is the corpus's to fix, a pack the installation refuses is the
pack's, and a run that disagreed with its own assertions is the scenario's. The
suite asserts the class and not only that something failed, because the class is
what tells a reader who fixes it.

**The verb never reports a run it did not make.** A manifest that cannot be read
and a root whose vocabulary cannot be read are usage errors -- the caller named
them -- rather than an untested verdict, so a mistyped path and a deliberate
empty scenario list do not read alike. That is what
`test_a_root_without_a_vocabulary_is_a_usage_error` pins.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.vocabulary import Vocabulary
from openhouse import pack_verbs
from openhouse import scenarios as openhouse_scenarios
from tools.catalog import paths

from .packfactory import pack as generated_pack

#: The shipped packs' own scenarios, which are the `test` verb's subject and sit
#: beside the phase-1 corpus rather than in it -- see the directory's README.
PACK_SCENARIOS = paths.ROOT / "scenarios" / "packs"

#: The pack this suite installs: the shipped example, which requires
#: `light_group` and `motion_sensor` -- both bound by the `minimal` fixture -- so
#: a passing install is the common case and a refusal is the test that asks for
#: one. Using the shipped pack rather than a fixture manifest is deliberate: its
#: `provides` path is repository-relative, which is where a session's sandbox
#: resolves it, and a temporary manifest could not pin a file the sandbox finds.
EXAMPLE_PACK = paths.PACKS / "official" / "example-pack.yaml"

#: A pack the `minimal` fixture cannot hold *yet*: it requires the `fan` slot,
#: which no room in that house binds. Installation accepts it -- a module lands
#: unwired and disabled -- so this is a pack whose scenarios still run.
UNWIRED_PACK = paths.PACKS / "official" / "bathroom_fan.yaml"

#: The scenario every test but the corpus one runs: the light is set and then
#: asserted, with no time advanced so no behaviour can move it between the step
#: and the read. `{expected}` is the state the assertion claims, and a scenario
#: whose claim is not the state it set is the one that fails.
SCENARIO = """\
name: {name}
given:
  house: minimal
  seed: 7
  started_at: "2026-01-01T23:30:00+00:00"
when:
  - set_state:
      entity_id: light.living_room
      state: "on"
then:
  - state:
      entity_id: light.living_room
      is: "{expected}"
"""


def _scenario(directory: Path, name: str, expected: str = "on") -> Path:
    """Write one scenario into `directory`, asserting the light `expected`."""
    path = directory / f"{name}.yaml"
    path.write_text(
        SCENARIO.format(name=name, expected=expected), encoding="utf-8", newline="\n"
    )
    return path


def _outcomes(report: pack_verbs.PackTestReport) -> list[str]:
    """The classes one report's outcomes were reported under, in order."""
    return [outcome.outcome for outcome in report.outcomes]


# -- a pack nothing was run against ------------------------------------------


def test_a_pack_no_scenario_names_is_untested_and_not_a_pass(tmp_path: Path) -> None:
    """No scenarios is `untested`, and `untested` is not `ok`.

    Falsified by `ok` computed as "no outcome failed", which is vacuously true of
    a pack nothing ran against -- the reading that makes a mistyped scenario path
    and a pack that passed every one of its scenarios the same answer.
    """
    report = pack_verbs.test_pack(EXAMPLE_PACK, scenarios=())
    assert report.untested
    assert not report.ok
    assert report.outcomes == ()
    assert report.name == "example_pack"
    assert report.manifest == str(EXAMPLE_PACK)


# -- the four classes --------------------------------------------------------


def test_a_scenario_that_holds_is_reported_passed(tmp_path: Path) -> None:
    """A run that happened and agreed with its assertions is a pass.

    Falsified by a verb that reports a run as failing for want of evidence it
    never gathered: the check is that `ok` comes back true only after a scenario
    actually ran, which is why the pack is the shipped example and not a fixture
    the installation step could refuse for a reason the test is not about.
    """
    report = pack_verbs.test_pack(
        EXAMPLE_PACK, scenarios=[_scenario(tmp_path, "holds", "on")]
    )
    assert not report.untested
    assert report.ok
    assert _outcomes(report) == ["passed"]


def test_a_scenario_that_disagrees_is_failed_and_not_a_pass(tmp_path: Path) -> None:
    """A run whose assertion does not hold is `failed`, and carries why.

    Falsified by an assertion mismatch swallowed into a pass, or reported with no
    reason: the scenario asserts the light is off after setting it on, so the run
    happened and disagreed, which is a different thing from a scenario that never
    ran -- and the report has to say which.
    """
    report = pack_verbs.test_pack(
        EXAMPLE_PACK, scenarios=[_scenario(tmp_path, "disagrees", "off")]
    )
    assert _outcomes(report) == ["failed"]
    assert not report.ok
    assert report.outcomes[0].message
    assert not report.outcomes[0].passed


def test_a_scenario_that_does_not_load_is_a_fixture_error(tmp_path: Path) -> None:
    """A file that will not load is the corpus's failure and not the pack's.

    Falsified by a loader error escaping the verb, or by a bad file reported as a
    pack failure: the pack is the shipped example and is not what is wrong, so
    the class has to name the scenario -- which is what a reader with a corpus of
    one bad file needs before anything else.
    """
    broken = tmp_path / "broken.yaml"
    broken.write_text("when: [\n  - set_state: {\n", encoding="utf-8", newline="\n")
    report = pack_verbs.test_pack(EXAMPLE_PACK, scenarios=[broken])
    assert _outcomes(report) == ["fixture_error"]
    assert not report.ok
    assert "broken.yaml" in report.outcomes[0].message


def test_a_pack_no_house_could_hold_is_a_pack_error_and_not_a_failure(
    tmp_path: Path,
) -> None:
    """A refusal at install is the pack's failure, before any step runs.

    Falsified by an install refusal reported as a failed run, or by the refusal
    escaping: the generated pack names a slot no vocabulary declares, which is a
    name no house could ever supply, and no step of the scenario was ever
    reached -- so crediting the scenario with the failure would send its author to
    the wrong file.

    **A pack this house has not wired yet is not this case.** A module whose
    slots no room binds installs disabled and unsatisfiable and its scenarios run,
    which is the rule below: the room's configurable devices are the modules' own
    slots, so the module has to be in before the slot it wants can be filled.
    """
    refused = generated_pack(tmp_path, "unbindable", requires=("nowhere_defined",))
    report = pack_verbs.test_pack(
        refused, scenarios=[_scenario(tmp_path, "holds", "on")]
    )
    assert _outcomes(report) == ["pack_error"]
    assert not report.ok
    assert "nowhere_defined" in report.outcomes[0].message


def test_a_pack_the_house_has_not_wired_yet_still_runs(tmp_path: Path) -> None:
    """Installing is not activating, and an unwired module is not a refusal.

    Falsified by the older rule that refused a pack whose required slots a room
    binds nowhere: that refusal would make the wiring unreachable, because the
    room's configurable devices are the modules' own slots and the module has to
    be in before the slot it wants can be filled. `bathroom_fan` requires `fan`
    and no room in the `minimal` fixture binds it, so this is exactly that case.
    """
    report = pack_verbs.test_pack(
        UNWIRED_PACK, scenarios=[_scenario(tmp_path, "holds", "on")]
    )
    assert _outcomes(report) == ["passed"]
    assert report.ok


# -- one session per scenario, in the order named ----------------------------


def test_every_scenario_named_gets_its_own_outcome_in_order(tmp_path: Path) -> None:
    """Two scenarios are two outcomes, each classed on its own.

    Falsified by a verb that stops at the first non-pass, or that runs the set
    once and reports one verdict: a corpus is a set of runs and a caller fixing
    one failing scenario needs the others' results to stay, which a run that
    aborted on the first failure would drop.
    """
    report = pack_verbs.test_pack(
        EXAMPLE_PACK,
        scenarios=[
            _scenario(tmp_path, "a-holds", "on"),
            _scenario(tmp_path, "b-disagrees", "off"),
        ],
    )
    assert _outcomes(report) == ["passed", "failed"]
    assert not report.ok


def test_a_directory_of_scenarios_is_run_as_a_corpus(tmp_path: Path) -> None:
    """A directory is expanded to every scenario in it, oldest name first.

    Falsified by a verb that only takes files: the caller who points at a corpus
    directory means its scenarios, and a verb that refused the directory would
    make the one path a scenario runner already accepts the one path this verb
    does not.
    """
    _scenario(tmp_path, "a-holds", "on")
    _scenario(tmp_path, "b-also-holds", "on")
    report = pack_verbs.test_pack(EXAMPLE_PACK, scenarios=[tmp_path])
    assert _outcomes(report) == ["passed", "passed"]
    assert report.ok


# -- the caller's mistakes are not the pack's --------------------------------


def test_a_manifest_that_is_not_a_pack_is_a_usage_error(tmp_path: Path) -> None:
    """A manifest nothing can read is the caller's mistake, not an untested pack.

    Falsified by an unreadable manifest reported `untested`: a mistyped path and
    a deliberate empty scenario list would then read alike, and the caller who
    named a file that is not there has a different thing to fix than the one who
    named no scenarios at all.
    """
    with pytest.raises(pack_verbs.UsageError) as raised:
        pack_verbs.test_pack(tmp_path / "absent.yaml", scenarios=())
    assert "absent.yaml" in raised.value.about
    assert raised.value.reason


def test_a_root_without_a_vocabulary_is_a_usage_error(tmp_path: Path) -> None:
    """A root whose vocabulary cannot be read is a usage error, not a no-pass.

    Falsified by a `Vocabulary.load` failure escaping the verb, or by an absent
    vocabulary folded into an untested verdict: the pack is the shipped example
    and is fine, so the failure is about `root`, and the caller has to be told
    that rather than told the pack was never run.
    """
    with pytest.raises(pack_verbs.UsageError) as raised:
        pack_verbs.test_pack(EXAMPLE_PACK, scenarios=(), root=tmp_path)
    assert raised.value.about == str(tmp_path)
    assert raised.value.reason


# -- the class list itself ---------------------------------------------------


def test_the_four_classes_are_declared_and_untested_is_not_one() -> None:
    """`OUTCOMES` is the four classes a run can end as, and not `untested`.

    Falsified by a fifth class for a pack nothing ran against: "no scenario was
    named" has no outcome to class, and the requirement keeps it as the
    `untested` verdict rather than as a run that ended some way, which is the
    distinction the whole verb exists to make.
    """
    assert pack_verbs.OUTCOMES == ("passed", "failed", "fixture_error", "pack_error")
    assert "untested" not in pack_verbs.OUTCOMES


# -- the shipped packs' own scenarios ----------------------------------------


def test_every_shipped_pack_scenario_runs(real_root: Path) -> None:
    """Each shipped pack's scenario installs its pack and the pack acts.

    Falsified by a scenario that runs green without the pack being installed:
    the actor on each run's records has to name the pack, because a scenario that
    asserted its way to a pass while `install_pack` had refused would prove the
    runner works and nothing about the pack -- which is exactly the confusion the
    `test` verb's separate `pack_error` class exists to prevent.
    """
    files = sorted(PACK_SCENARIOS.glob("*.yaml"))
    assert files, "the shipped packs ship no scenario to test"
    runs = openhouse_scenarios.run_corpus(
        PACK_SCENARIOS, vocabulary=Vocabulary.load(real_root)
    )
    assert len(runs) == len(files)
    assert all(run.log for run in runs)
    acted = {
        str(record["actor"]).split(".", 1)[0]
        for run in runs
        for record in run.log
        if record.get("actor")
    }
    assert {"bedtime", "roomba"} <= acted
    assert {"passed", "failed", "fixture_error", "pack_error"} == set(
        pack_verbs.OUTCOMES
    )
