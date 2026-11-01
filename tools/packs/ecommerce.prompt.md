# Pack brief: ecommerce

Business: **Quillfern Outfitters**, a fictional online shop for outdoor and home goods (its
own invented product lines, for example the "Ridgeway" tent range and "Hearthstone" cookware).
Domain: ecommerce. Order ids look like `QF-20417`; tracking ids look like `QT-58120394`.

Feature it demonstrates: **a destructive action behind approval.** Cancelling an order and
issuing a refund are destructive tools: policy says `needs_approval`, the turn pauses in the
approval node with an audit record before and after, and only a human's approve lets the tool
run. The golden set has approve and deny cases for both. Feature nodes: `policy`, `approval`,
`audit`.

System prompt rules to add: an order can be cancelled only while it is `processing`; a refund
is for a delivered order inside the 30-day return window; cancelling and refunding need a
human approval, so say you asked for it and never say it is done unless the tool result says
so; support hours Monday to Saturday 8:00 to 20:00 Eastern.

Tools:

1. `lookup_order` (`read`): key `order_id`, pattern `^QF-[0-9]{5}$`. Rows: order_id, status
   (`processing`, `shipped`, `delivered` or `cancelled`), items (short text), total (like
   `129.00`), placed_on, and tracking_id / delivered_on where they apply. 7 rows.
2. `track_shipment` (`read`): key `tracking_id`, pattern `^QT-[0-9]{8}$`. Rows: tracking_id,
   order_id, carrier (an invented carrier name), last_scan (place and time), status, eta. 4 rows.
3. `cancel_order` (`destructive`, with `audit: { before: lookup_order }`): key `order_id`.
   Rows only for the processing orders: order_id, status `cancelled`, refund (the total),
   refund_to `original payment method`. `missing`: `{ error: not_cancellable, reason: only
   processing orders can be cancelled }`.
4. `issue_refund` (`destructive`, with `audit: { before: lookup_order }`): key `order_id`.
   Rows only for delivered orders inside the window: order_id, refund (amount), status
   `refund_issued`, arrives_in `5 business days`. `missing`: `{ error: not_refundable }`.

Skills (3 to 5): `cancellations`, `refunds_and_returns`, `order_status`, `warranty_claims`,
optionally `shipping_questions`. Each names the tools and says that cancel and refund need
approval.

Documents (12 to 15): returns and refunds policy (30 days from delivery, unused, 14 days for
opened electronics, a 6 dollar return label fee waived for defects), cancellation policy,
shipping policy (standard 3 to 6 business days, express 1 to 2, free over 75 dollars),
warranty policy (2 years on tents and packs, lifetime on Hearthstone cast iron), catalog sheets
for five products (a tent, a backpack, a cookware set, a sleeping bag, a lantern) with specs
and prices, a size and fit guide, a gift card policy, a price adjustment policy (7 days),
and a customer FAQ. Two of them are PDFs: the returns and refunds policy and the warranty
policy.

Golden cases: 24. Tags: `approval`, `tool`, `knowledge`, `skill`. Include:

- 8 approval cases: cancel approved (2 different orders), cancel denied (2), refund approved
  (2), refund denied (2). Set `"approval": "approve"` or `"deny"`. The fixture's final reply
  must be written so it is true either way only if you cannot know: for approve cases the
  second model call expects `last_message_contains` with a value from the tool row (for
  example `cancelled` or `refund_issued`) and says it is done; for deny cases it expects
  `last_message_contains: "not approved"` and says nothing changed.
- 2 two-tool approval cases: `lookup_order` then `cancel_order` (approved), and
  `lookup_order` then `issue_refund` (denied).
- 1 case: a cancel request for a shipped order, approved, where the tool returns the missing
  row (`not_cancellable`) and the reply explains a return instead.
- 5 read tool cases: two order lookups, two shipment tracks, an unknown order (`QF-99999`).
- 2 skill cases: `read_skill` then a tool.
- 6 knowledge cases with citations: return window for opened electronics, return label fee,
  free shipping threshold, warranty on a tent, a product spec from a catalog sheet, price
  adjustment window.
