Content review (P5 integration pass, 2026-11-01, done by the integration builder, an agent
working for the maintainer; the owner may add a read of their own here): read this card and
`tools/packs/logistics.review.md`; listed every capitalised name in the system prompt, skills,
documents, PDF sources, seeds, tool tables and prompts and checked them for real brands, clubs,
places and people; searched for well-known brand names, street addresses, postcodes and URLs
(none); read `dangerous_goods_policy.md`, `carrier_contract_cc22.md` and both PDF sources in
full and sampled the rest; ran the pack validator's PII scan (clean) and the golden suite (fake
provider) in a data directory holding every other pack's runs (clean). Three town names in the
`track_shipment` table matched real small places and were renamed (Dunmere to Dunmarrow,
Larchfield to Larchwhistle, Ashfold to Ashtrave); no fixture or golden case named them. Hubs,
carriers (Bellmoor Linehaul, Greywater Haulage, Ostrander Freightways) and regions are invented.
The dangerous goods policy states what the carrier accepts and refuses; it gives no handling
instructions beyond that. Reviewed: yes.
