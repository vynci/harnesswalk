# Try these

Tallowgate Freight shows multi-step tool use. A single question here can take three or four trips round the loop: track the shipment, check a hub, reroute it, and get the new ETA. Each trip makes one tool call, and the reroute stops at the approval gate for a human.

1. Where is shipment TG-604118 right now?
   *One loop step: the `track` tool lights up, then the answer.*
2. Why is TG-604677 delayed, and when will it arrive?
   *Two steps: `track` then `eta`, with policy allowing each read.*
3. Is the Wyncliff hub open, and does it take reroutes?
   *One `hub_capacity` call. The status comes back as closed.*
4. TG-604227 is stuck. Check its alternate hub and reroute it if you can.
   *Three steps: `track`, `hub_capacity` on TRB, then `reroute`, which stops at policy, audit and approval.*
5. Track TG-604118, check whether its alternate hub has room, reroute it, and tell me the new ETA.
   *Four steps: `track`, `hub_capacity`, `reroute` (approval), then `eta`. The loop goes round four times.*
6. Can you reroute TG-604677 through its alternate hub?
   *`track`, then `hub_capacity` on WYN shows closed, so no `reroute` call is made.*
7. Move TG-604305 to its alternate hub, but I will not approve it.
   *Deny the approval request: the audit node records the denial and the shipment stays put.*
8. How long do I have to claim for damaged cargo?
   *The knowledge store answers with a citation, with no tool call.*
9. Track TG-999999.
   *`track` returns not_found and the assistant says so without guessing.*
