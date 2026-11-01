- The system prompt and `docs/new_patient_registration.md` showed the health number format as
  a literal example, which the pack's own guardrail pattern matches (and would redact in a
  reply); both now describe the format in words.
- "Mill Lane" (a common real street name) became "Willowbank Row".
- Cue-based name redaction is off for this pack (the brief asked for it): the detector in
  `harnesswalk/control/pii.py` compiles its cue pattern case-insensitively, so "I am looking"
  or "I'm not sure" are taken as names. The `pii_name_hours` case became `pii_id_phone_hours`
  (a health number and a phone together) until the detector is fixed.
