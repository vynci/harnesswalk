---
name: player_questions
description: Use when someone asks about a player's goals, assists, appearances or cards.
---
# Player questions

1. You need the player id, which looks like PL-114. If the user gave only a name, check the team sheets with `read_doc` for the id, or ask for it.
2. Call `player_stats` with the id as `player_id`.
3. Report only the fields asked about, using the numbers exactly as returned: appearances, goals, assists, yellow_cards.
4. For a card question, compare `yellow_cards` with the ban threshold in the discipline code (read it with `read_doc`) and say how many yellows remain before a ban.
5. If you are asked about two players, call `player_stats` once for each and compare the rows.
6. If the id is not found, say so and ask the user to check it against the team sheet.
