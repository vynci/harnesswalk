---
name: cancellations
description: Use when a customer wants to cancel an order before it ships.
---
# Cancelling an order

1. Call `lookup_order` with the order id to read its status.
2. If the status is `processing`, call `cancel_order` with the order id. This needs a human approval, so tell the customer you have asked for it.
3. If the tool result says `cancelled`, confirm the cancellation and the refund amount to the original payment method.
4. If the approval was denied, say plainly that nothing was changed.
5. If the status is `shipped` or `delivered`, do not call `cancel_order`. Explain that the order cannot be cancelled and offer a return once it arrives.
6. If the order is already `cancelled`, say so and do not repeat the action.
