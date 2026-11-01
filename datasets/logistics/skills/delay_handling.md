---
name: delay_handling
description: Use when a shipper asks why a shipment is late, held or delayed, or when it will arrive.
---
# Explaining a delay

1. Call `track` with the shipment id to get its status, current hub and last event.
2. Call `eta` with the same id to get the ETA window, the confidence and the delay reason.
3. Explain the delay in plain words: what the delay reason is, where the shipment is, and the ETA window with its confidence.
4. If confidence is `low`, say the window may move and that the operations desk will keep watching it.
5. If the delay has passed 24 hours, mention that the delay escalation procedure applies and an account manager is told. Search the knowledge base if the shipper asks for the details.
6. Offer a reroute only when the status is `held` or `delayed` and the shipper asks for options. Then follow the `rerouting` skill.
