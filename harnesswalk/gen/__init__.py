"""The generator: `harnesswalk init`, `harnesswalk upgrade` and `harnesswalk gen diff`.

Read a harness, grow one, generate your own. `init` renders a template set
(`templates/<lang>/`, Python only today) for a profile (the node set), a pack (the content) and
a generator manifest (providers, transport, storage, release door), and writes a repository
the user owns: plain Python with no runtime dependency on Harnesswalk. Contract and walkthrough:
docs/generator.md.

Modules: `manifest` (the generator manifest and its checks), `render` (the template
preprocessor), `build` (init: templates, data copies, notes, lock, pristine copies), `upgrade`
(three-way merge of managed files onto a branch, with codemods).
"""
