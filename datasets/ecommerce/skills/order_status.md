---
name: order_status
description: Use when a customer asks where an order is or what its status is.
---
# Order status

1. Call `lookup_order` with the order id.
2. Report the status, the items and the total.
3. If the order has a `tracking_id`, call `track_shipment` and report the last scan, the carrier and the eta.
4. If the order is `processing`, say it has not shipped yet and can still be cancelled.
5. If the order id is not found, ask the customer to check it. Ids look like QF- followed by five digits.
