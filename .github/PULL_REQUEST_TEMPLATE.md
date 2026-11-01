## What and why

<!-- One or two sentences. Link the issue: Closes #123. -->

## Spec

- [ ] No behaviour change, or the change is in `spec/` first (prose or contract, schema, diagram)
- [ ] Additive under `spec/VERSIONING.md`, or the issue agreed on a breaking change
- [ ] A conformance case shows the new behaviour, and `spec/CHANGELOG.md` has a line

## Checks

- [ ] `make test` and `make lint` pass
- [ ] `make web-test` passes (if `web/` changed)
- [ ] Packs: `uv run harnesswalk packs validate` and the pack's golden set pass (if `datasets/` changed)
- [ ] Lessons, README and inspector text still describe what the code does
- [ ] `CHANGELOG.md` has a line under `Unreleased` for anything a user would notice
- [ ] No keys, `.env` files or personal data; tests use the `fake` provider only

## How I tested it

<!-- The commands you ran, and for UI changes a screenshot. -->
