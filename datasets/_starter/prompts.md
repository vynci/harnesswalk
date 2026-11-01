# Try these: Example Store

The starter pack touches every part of the harness once, so each prompt below lights a
different path through the diagram. Pick a fixture in the composer to replay it on the fake
provider, or run it on a real model.

1. Can I return order A-1003, the Trailhead backpack?
   *All five memory badges light; the reply cites the returns policy (fixture `trailhead_return`).*
2. Where is my order A-1001?
   *One read tool: `tools` and `policy` light once (fixture `tool_call`).*
3. Please cancel order A-1002.
   *A destructive tool: policy, audit, the approval pause, audit again (fixture `cancel_order`).*
4. What is the return window for opened electronics?
   *Knowledge retrieval and a cited answer (fixture `cited_answer`).*
5. Please remember that my name is Sam.
   *A `remember` call, then the memory writer after the reply (fixture `remember_fact`).*
6. What is the status of A-1001, A-1002 and A-1003?
   *Three tool calls in one step; try it with a tool-call budget of two (fixture `three_lookups`).*
