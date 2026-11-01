# Try these

Quillfern Outfitters shows one thing on purpose: a destructive action that waits for a human. Cancelling an order or issuing a refund never runs on the model's say-so. Policy answers `needs_approval`, the turn pauses in the approval node, and the audit node writes a record before and after. Try the prompts below and watch the diagram stop and start.

1. Where is my order QF-20417?
   *Read tools only: `lookup_order` then `track_shipment` pass through policy with no pause.*
2. Please cancel order QF-20430, I ordered the wrong size.
   *`cancel_order` lights policy, audit and approval. Approve it and the order flips to cancelled.*
3. Please cancel order QF-20433.
   *Same path. Deny it and the reply says nothing was changed.*
4. I want a refund for order QF-20388, the lantern arrived cracked.
   *`issue_refund` waits in the approval node with an audit record of the order before the action.*
5. Can you cancel order QF-20417?
   *The order has shipped, so the tool returns `not_cancellable` and the reply explains a return instead.*
6. Track shipment QT-58120394 for me.
   *`track_shipment` is a read tool: policy allows it and the loop continues.*
7. Can I return an opened headlamp after three weeks?
   *Knowledge search: the returns policy is cited as a passage in the context.*
8. How long is the warranty on a Quillridge tent?
   *`read_skill` or a knowledge passage; the answer cites the warranty policy.*
9. Is shipping free on a 90 dollar order?
   *Knowledge passage from the shipping policy, no tool call.*
