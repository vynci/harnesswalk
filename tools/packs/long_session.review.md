- The card, system prompt, skills, tool tables, seeds, prompts page, golden set and fixtures
  were written by hand (the build agent, P2); the ten documents and the 34 pasted planning
  notes in the long golden cases came from the generator (`claude-haiku-5-5`). Seven documents
  came back under 300 words and were generated again with a longer brief. The notes were
  written by `tools/packs/long_session_notes.py` (calls `notes[...]` in the usage table); each
  note's first sentence was given word for word, so a fact the summary must keep sits in the
  first 160 characters of its turn.
- Long turns paste one or two notes (about 500 to 1,100 tokens each) so the session slice
  crosses its compaction threshold (0.8 of 5,000 tokens) at a known turn on the fake provider.
- Trip legs read "2026-11-14 at 12:30": a date followed directly by a time read as a phone
  number to the PII scan.
