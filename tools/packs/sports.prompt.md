# Pack brief: sports

Business: **The Copperline Community League**, a fictional amateur football (soccer) league
with two divisions (`premier` and `championship`), eight invented clubs with three-letter
codes (for example `ASH` for Ashgrove Rovers), invented venues and players. Domain: sports.
Player ids look like `PL-114`. Season 2026 autumn.

Feature it demonstrates: **cheap model routing for lookups.** Fixture, standings and
player-stat questions are lookups: the first model call picks the tool (router rule
`default`), and the call after a read-only tool result only has to phrase the row, so the
router's `lookup` rule sends it to the cheapest configured model. Rules questions answered
from knowledge stay on `default`. The diagram's router node shows the rule per call. Feature
nodes: `model_layer`, `router`.

System prompt rules to add: you are the league's volunteer help desk for players, captains
and fans; use `fixtures`, `standings` and `player_stats` for anything about matches, the table
or a player; answer rules questions from the league documents with citations; kick-off times
are local; the league office answers captains Monday to Thursday 18:00 to 21:00.

Tools (all `read`):

1. `fixtures`: key `team`, pattern `^[A-Z]{3}$`. Rows: team (club name), next_match (date),
   kickoff (HH:MM), opponent, venue, home_or_away, last_result (like `W 3-1 v MIL`). 8 rows.
2. `standings`: key `division`, pattern `^(premier|championship)$`. Rows: division, leader,
   table (one string like `1. Ashgrove Rovers 22 pts, 2. ...` for the four clubs of that
   division), matches_played, updated (date). 2 rows.
3. `player_stats`: key `player_id`, pattern `^PL-[0-9]{3}$`. Rows: player_id, name, club,
   position, appearances, goals, assists, yellow_cards. 8 rows.

Skills (3 to 5): `match_lookups` (use fixtures; never guess kickoff times), `league_table`,
`player_questions`, `rules_questions` (answer from the rulebook with citations),
optionally `captains_admin`.

Documents (12 to 15): league rulebook (match length 2 x 40 minutes, rolling substitutions,
squad size 16), discipline code (yellow card accumulation: 5 yellows is a one-match ban, a
red card is at least two matches), registration and eligibility rules, fixtures and
postponement policy (weather calls by 10:00 on match day), venue guide (four invented venues
with pitch surface and parking), team sheets for four clubs (squad lists with invented names
and player ids that match the `player_stats` table where they overlap), referee and assessor
guide, captains handbook, season calendar (start, mid-season break, final day, cup final),
code of conduct, cup competition rules. Two of them are PDFs: the league rulebook and the
discipline code.

Golden cases: 22. Tags: `routing`, `tool`, `knowledge`, `skill`. Every case also has a
`"checks": {"router_rules": [...]}` key listing the router rule of each model call in order:

- a one-tool lookup (tool call, then reply): `["default", "lookup"]`;
- two tools in two steps: `["default", "lookup", "lookup"]`;
- `read_skill` then one tool: `["default", "lookup", "lookup"]`;
- no tool (knowledge or plain): `["default"]`.

Include: 12 one-tool lookups (fixtures for four clubs, both standings, six player stats), 3
two-tool cases (fixtures then standings, player_stats for two players, fixtures for two
clubs), 1 `read_skill` then `fixtures`, and 6 knowledge cases with citations (match length,
yellow card ban, postponement deadline, squad size, red card ban, a venue's pitch surface).
