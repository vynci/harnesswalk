- Product lines renamed away from words that are also real brand or product names:
  Glowworm to Fernglow, Ridgeway to Quillridge, Trailmate to Fernpath, Nightjar to Mothdown,
  Hearthstone to Emberhollow (file names, doc ids and case ids included). Ridgeway and
  Hearthstone came from the pack prompt, which is kept as it was run.
- Scan times in `track_shipment` rows read "2026-10-30 at 06:40": a date and a time joined by a
  space look like a phone number to the PII detector, which would redact them in a reply.
