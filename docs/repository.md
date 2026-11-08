# Repository layout

What lives where in the Harnesswalk repository. [architecture.md](architecture.md) explains how
the parts fit together.

```
spec/         the language-agnostic contract: harness, diagram, events, contracts, conformance, packs
harnesswalk/  the reference implementation (Python): control, context, execution and ops planes
config/       harness.yaml, policy.yaml, context.yaml, models.yaml, and built-in fixtures
web/          the chat and diagram UI (React, TypeScript)
datasets/     the packs: _starter and nine domain packs (healthcare, ecommerce, logistics, sports, finance,
              device_fleet, gov_services, long_session, helpdesk_lite)
lessons/      the course: one chapter per node in walk order, then one per expert group
deploy/       docker compose (dev, homelab and prod profiles), the api image, the AWS stack, load tests
docs/         these pages, releasing, brand assets and screenshots
tests/        unit tests, spec checks, public-mode tests and the conformance runner
tools/        release prep, the wheel smoke, the fresh-clone check, pack generation
```

Each of these folders has a README with the details:

- [spec/conformance/README.md](../spec/conformance/README.md): how the conformance suite runs,
  and how to add a case.
- [harnesswalk/README.md](../harnesswalk/README.md): the module map, from each node to its
  module.
- [web/README.md](../web/README.md): how to run and test the UI, its components and the colour
  system.
- [datasets/README.md](../datasets/README.md): the pack format and the shipped packs.
- [lessons/README.md](../lessons/README.md): the chapter index and the chapter format.
- [deploy/README.md](../deploy/README.md): the compose profiles and the hosted sandbox.

At the root, [CONTRIBUTING.md](../CONTRIBUTING.md) has the setup and how to add a node, a tool, a
pack, a lesson or a conformance case. [CHANGELOG.md](../CHANGELOG.md) records what changed, and
[spec/CHANGELOG.md](../spec/CHANGELOG.md) does the same for the spec.
