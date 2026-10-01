# The pack registry

This tree is the registry: the pointer files a publisher adds, the index the
store reads, and the revocation list. It is a repository of *pointers* rather
than of packs — a pointer names where a pack's source lives and pins the exact
revision with a digest, so the registry never has to hold a copy of anyone's
code.

## Layout

```
registry/
  pointer.schema.json     the shape of a pointer file
  tiers.yaml              the four trust tiers and what each permits
  revocations.yaml        the revocation list (the source of truth)
  pointers/<tier>/<name>/<version>.yaml
                          one pointer per published pack version
  index.json              generated from the pointers
  revoked.json            generated from revocations.yaml
```

`index.json` and `revoked.json` are **generated** and are never edited by hand.
Both are produced by:

```
python -m tools.registry.cli generate
```

and CI re-runs the generator and fails if the committed files differ from its
output, so a pointer added without regenerating the index is a red build rather
than a store that silently cannot see the pack.

## A pointer

```yaml
name: hallway-motion
version: 1.0.0
repo: https://github.com/example/hallway-motion
commit: 4f9c1e2b8a7d6c5f4e3d2c1b0a9f8e7d6c5b4a39
path: hallway-motion.yaml
sha256: sha256:2b7e...c41a
tier: community
```

The `sha256` is the digest of the manifest *document*, computed by
`engine.install.digest`, so two manifests that say the same thing have the same
digest whatever their file's whitespace. The store verifies it before it
installs: a pointer whose digest does not match the manifest it points at is
refused, which is what makes the digest a pin rather than a note.

`repo: .` is reserved and names the repository that hosts this registry, which
is how the project's own packs are published without a second checkout.

## The four tiers

| tier | signed | reviewed | dangerous permissions | published |
| --- | --- | --- | --- | --- |
| `official` | yes | yes | permitted | yes |
| `verified` | no | yes | permitted | yes |
| `community` | no | no | **refused** | yes |
| `local` | no | no | permitted | no |

A *banned* service is refused in every tier; the ban is about what acts on the
host rather than on a device in the house, and no tier un-bans it. A tier can
permit a *flagged* service and cannot permit a banned one.

## Publishing

A publisher runs `python -m tools.registry.cli publish <manifest> --repo ...
--commit ... [--tier community]`, which writes the pointer and prints a
prefilled pull-request title and body. Opening that pull request is the act of
submission; the checks in `tools/registry/checks.py` are what CI runs against
it.
