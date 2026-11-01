---
name: match_lookups
description: Use when someone asks when, where or against whom a club plays, or how it did last time.
---
# Match lookups

1. Find the club's three-letter code (ASH, MIL, and so on). If the user gave a club name, map it to the code. If you cannot tell which club, ask.
2. Call `fixtures` with the code as `team`.
3. Report the next match date, the kick-off time, the opponent, the venue and whether it is home or away. Quote the kick-off exactly as the tool returned it. Never guess a time.
4. If the user also asked about form, add the `last_result` field as written.
5. If the tool returns not_found, say the code was not recognised and list the eight valid codes if you know them.
6. For a postponement question, do not answer from the fixture row; read the postponement policy with `read_doc`.
