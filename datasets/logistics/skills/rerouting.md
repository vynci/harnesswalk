---
name: rerouting
description: Use when a shipper asks to move a shipment to its alternate hub or reroute a delayed consignment.
---
# Rerouting a shipment

1. Call `track` with the shipment id. Note the status and the `alternate_hub`. A delivered shipment cannot be rerouted.
2. Call `hub_capacity` with the alternate hub code. Read its status and `accepts_reroutes`.
3. If the hub is `closed`, or does not accept reroutes, do not call `reroute`. Explain why and offer to check the ETA at the current hub.
4. If the hub is open and accepts reroutes, call `reroute` with the shipment id. This needs a human approval, so tell the shipper it is waiting for one.
5. If the reroute is approved and the result says `rerouted`, report the hub it goes via and the new ETA. Call `eta` only if the shipper asks for the confidence.
6. If the reroute is denied or returns `reroute_not_possible`, say the shipment stays where it is and nothing was changed.
7. Make one tool call per step and read each result before the next.
