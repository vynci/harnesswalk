Content review (P5 integration pass, 2026-11-01, done by the integration builder, an agent
working for the maintainer; the owner may add a read of their own here): read this card and
`tools/packs/healthcare.review.md`; listed every capitalised name in the system prompt, skills,
documents, PDF sources, seeds, tool tables and prompts and checked them for real brands, clubs,
places and people; searched for well-known brand names, street addresses, postcodes and URLs
(none); read `patient_faq.md`, `after_hours_urgent_care.md`, `lab_tests_guide.md` and
`vaccination_clinic_guide.md` in full and sampled the rest; ran the pack validator's PII scan
(clean) and the golden suite (fake provider) in a data directory holding every other pack's runs
(clean). The clinic, sites (north, riverside, eastgate), the Willowbank, Eastgate Quarter and
Harbour District areas and every person are invented or generic descriptors. Clinical content
stays within front-desk policy: the documents and the `clinical_boundaries` skill decline
advice, diagnosis, dosing and symptom interpretation and send urgent symptoms to emergency
services; the preparation notes (fasting for a blood test, the wait after a vaccination) are
clinic procedure, not treatment advice. Name redaction is on again now that the detector scopes
its case rule to the cue words (`pii_name_hours`); the six cases that expect a typed value
restored in the reply are `modes: [dev]`. Reviewed: yes.
