- The card, system prompt, skills, tool tables, guardrails, seeds, prompts page, golden set
  and fixtures were written by hand (the build agent, P2); only the twelve documents came from
  the generator (`claude-haiku-5-5`). Four documents came back under 300 words and were
  generated again with a longer brief (both calls are in the usage table).
- `guardrails.yaml` is hand-written: two civic address patterns (a French and an English
  street address) are national ids for this pack; the validator checked that they match no
  other pack's content.
- Application references are a letter and four digits (`AV-K4417`): a longer all-digit form
  read as a phone number to the PII detector and would have been redacted from the prompt.
- The synthetic social insurance number in the French PII case passes the Luhn check on
  purpose and was generated at random; it belongs to no one.
