---
name: refunds_and_returns
description: Use when a customer asks for a refund or wants to return a delivered item.
---
# Refunds and returns

1. Call `lookup_order` and check the status is `delivered` and the delivery date is within 30 days.
2. Search the knowledge base for the returns and refunds policy if the customer asks about opened items, electronics or label fees.
3. If the order qualifies, call `issue_refund`. This needs a human approval, so say you have asked for it.
4. If the tool result says `refund_issued`, confirm the amount and that it arrives in 5 business days.
5. If approval was denied, say nothing was changed and offer to try again later.
6. If the order is outside the window or not delivered, explain why and do not call the tool.
