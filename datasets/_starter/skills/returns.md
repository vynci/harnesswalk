---
name: returns
description: How to handle a return or refund request.
---
# Returns and refunds

1. Ask for the order id if the customer has not given it.
2. Look the order up with `lookup_order`.
3. An order that has not shipped can be cancelled instead of returned: offer `cancel_order`.
   Cancelling needs a human approval, so tell the customer you have asked for it.
4. A shipped order can be returned within 30 days of delivery. Explain that a prepaid label
   arrives by email within one business day.
5. Refunds go back to the original payment method within 5 business days.

Never promise a refund amount the order lookup does not show.
