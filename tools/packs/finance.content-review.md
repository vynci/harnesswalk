Content review (P5 integration pass, 2026-11-01, done by the integration builder, an agent
working for the maintainer; the owner may add a read of their own here): read this card and
`tools/packs/finance.review.md`; listed every capitalised name in the system prompt, skills,
documents, PDF sources, seeds, tool tables and prompts and checked them for real brands, clubs,
places and people; searched for well-known brand names, street addresses, postcodes and URLs
(none); read `fund_sheet_hcp01.md`, `distribution_waterfall_guide.md`,
`default_and_late_payment_policy.md` and both PDF sources in full and sampled the rest; ran the
pack validator's PII scan (clean) and the golden suite (fake provider) in a data directory
holding every other pack's runs (clean). The manager, its four funds and every investor are
invented; there are no bank details, IBANs or SWIFT codes. Every document that touches money
says the desk gives no investment, tax or legal advice and sends the investor to their own
advisers; nothing recommends buying, selling, paying or delaying. The two cases that expect a
typed account number restored in the reply are `modes: [dev]`. Reviewed: yes.
