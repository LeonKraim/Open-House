# The shipped packs' scenarios

One scenario per shipped pack that a house can hold, run by
`tests/test_pack_verbs.py` rather than by `scenarios/`'s own corpus checks.

**Why this is a subdirectory.** `scenarios/` is the phase-1 corpus: one file per
`catalog/edge_cases.yaml` seed, named `<seed number>-<slug>.yaml`, and
`tests/test_scenarios_module.py` asserts that the files there name exactly the
seeds the catalog lists. A pack scenario names no seed, so it belongs beside the
corpus rather than in it -- and `sim.scenario`'s loader and the `oh-house
scenario run` command both read one directory without descending, so the two sets
cannot be confused for each other.

**Each scenario installs the pack it is about.** These are the `test` verb's
subject: a pack's own scenario, with the pack installed inside it by an
`install_pack` step, so the whole path -- validate, resolve against the house,
sandbox, register the behaviours disabled -- runs before the first tick. The two
`enable_flags` are the module's gate and the behaviour's own, because a pack
behaviour is disabled on arrival and enabling it is the house's act
(`pack-install`'s "installation is not activation").

**The assertion is a log citation and not a state.** A declared behaviour
proposes the *service* it declares, and the fake port writes that string as the
entity's state -- so a fan that was told to run reads `fan.turn_on` rather than
`on` until the port is widened. That is the recorded gap in
`engine/behaviours/declared.py`, and asserting on it here would pin the gap as
though it were the contract. What each scenario asserts instead is the record the
engine wrote: `rule: <behaviour>` and `outcome: acted`, which is the fact the
pack is responsible for.

**The bathroom fan has no scenario, and cannot have one.** The pack requires the
`fan` slot, whose entities are `fan.*`, and `sim/fixtures/_plans.py`'s `INITIAL`
table -- which fixes the state every domain starts in -- has no `fan` entry, so a
house binding the slot cannot be materialised at all. That is a fact about the
simulator and not about the pack, and it is recorded here rather than worked
around with a slot the pack does not mean.
