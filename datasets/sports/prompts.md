# Try these

The Copperline help desk is mostly lookups, and that is the point: when a turn only needs a tool to be read back, the router sends the phrasing step to the cheapest model. Watch the router node show `default` for the call that picks a tool and `lookup` for the call that phrases the row, while rules questions answered from the documents stay on `default`.

1. When does ASH play next, and where?
   *Router node: `default`, then `lookup`; the `fixtures` tool lights up.*
2. Who is top of the premier division?
   *Router: `default`, then `lookup`; `standings` is called with `premier`.*
3. How many goals has PL-114 scored?
   *Router: `default`, then `lookup`; `player_stats` returns the row for PL-114.*
4. What time does Rivelmere Wanderers kick off, and what was their last result?
   *One `fixtures` call for RIV; the second model call is routed to `lookup`.*
5. Compare PL-203 and PL-219 for goals and assists.
   *Two `player_stats` calls in two steps: `default`, `lookup`, `lookup`.*
6. Show me both league tables.
   *Two `standings` calls, one per division, each phrase step on `lookup`.*
7. How long is a match, and how big can a squad be?
   *No tool: knowledge search and a cited answer, router stays on `default`.*
8. How many yellow cards until a ban?
   *Knowledge passage from the discipline code, cited as [1]; router stays on `default`.*
9. What is the pitch like at Quarry Lane?
   *Venue guide passage, cited; no tool call, router on `default`.*
10. Read me the matches skill, then check when LOW plays next.
    *`read_skill` then `fixtures`: `default`, `lookup`, `lookup`.*
