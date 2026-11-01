---
name: shipping_questions
description: Use when a customer asks about delivery times, costs or a tracking id.
---
# Shipping questions

1. If the customer gives a tracking id (QT- and eight digits), call `track_shipment` and report the last scan and eta.
2. If they give an order id, call `lookup_order` first and use its `tracking_id`.
3. For delivery times and costs, search the knowledge base for the shipping policy: standard 3 to 6 business days, express 1 to 2, free standard shipping over 75 dollars.
4. If there is no tracking id yet, the order has not shipped. Say so.
