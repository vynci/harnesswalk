---
name: league_table
description: Use when someone asks who is top, how a club is placed, or for a division table.
---
# League table

1. Work out which division is meant: `premier` or `championship`. If the user names a club and not a division, check the club's division in the team sheets or ask.
2. Call `standings` with the division as `division`.
3. Give the leader first, then the table string from the tool. Mention `matches_played` and the `updated` date so the user knows how fresh it is.
4. Do not calculate points yourself or predict results. If asked who will win, say you only report the current table.
5. If the user wants two divisions, call `standings` once per division, one after the other.
