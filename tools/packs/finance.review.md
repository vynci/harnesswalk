- The first core call stopped at its 16,000 token limit before the document plan; the stage
  was run again with a higher limit. Both calls are in the usage table.
- Skill and tool descriptions containing ": " were quoted, so their YAML front matter parses.
- The system prompt and the `account_security` skill showed the account number format as a
  literal example, which the pack's own guardrail pattern matches; both now describe it in
  words.
