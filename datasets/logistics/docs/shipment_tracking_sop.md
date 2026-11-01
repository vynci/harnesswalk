# Shipment Tracking SOP

## Purpose

This procedure describes how the Tallowgate Freight operations desk tracks a consignment from the moment it is booked until the shipper confirms delivery. It applies to every shipment moved across our six hubs, whichever carrier contract it travels under. The goal is simple: any member of the desk should be able to answer "where is my shipment?" in under a minute, using the same words and the same data as everyone else.

## Shipment ids

Every consignment gets an id when the booking is confirmed. The format is the letters TG, a dash, and six digits, for example TG-604118. The id is printed on the booking confirmation, on every pallet label and on the delivery note. Before looking anything up, check the id matches this pattern exactly. If a shipper gives an id with a missing digit, a different prefix or letters in the number part, ask them to confirm it from their booking confirmation. Never guess a neighbouring id, because a wrong guess can show one shipper another shipper's consignment.

## Status values

A shipment is always in exactly one of four statuses:

- **in_transit**: the shipment is moving between hubs or on the final leg to the consignee. No action is needed.
- **held**: the shipment is stopped at a hub for a reason outside the normal sort, such as a yard closure, a paperwork query or a safety hold. Held shipments are reviewed by the hub shift lead at every shift change.
- **delayed**: the shipment is still moving or queued, but it will miss its planned arrival window. A delay reason is recorded.
- **delivered**: the consignee has signed for the shipment. The last event holds the date, time and signature note.

## Scan refresh

Hub scanners send events to the tracking system, and the desk view is refreshed every 30 minutes at hubs. Linehaul departures are recorded when the trailer leaves the gate, not when it is loaded. Because of the refresh interval, a shipment can look one scan behind. If a shipper says a shipment has already arrived and the system disagrees, check the last event time. If it is older than 90 minutes, raise it with the hub shift lead rather than telling the shipper they are wrong.

## What to tell the shipper

When you report a status, give four things: the status in plain words, the current hub, the origin and destination, and the last event with its date and time. If the status is held or delayed, offer to check the ETA. If the status is delivered, state the delivery time and stop: do not offer a reroute, because a delivered shipment cannot be rerouted under any circumstances.

## Unknown ids

If the tracking system says the id is not on file, tell the shipper plainly and suggest they check their booking confirmation. Bookings made less than 15 minutes ago may not have synchronised yet, so offer to try again shortly. Do not create, edit or guess a record to fill the gap.

## Record keeping

Every status question answered by the desk is logged against the shipment id with the time and the operator. This helps the account manager see how often a shipper is asking, which is often an early sign that a delay needs escalation under the Delay Escalation SOP.
