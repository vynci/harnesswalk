# Reroute SOP

## Purpose

A reroute moves a live consignment from its current hub to its contract's alternate hub so that it can continue its journey sooner. Because a reroute changes a shipment that is already in the network, it is controlled: it needs a human approval, it is logged, and it is only available when the alternate hub can really take the freight.

## When a reroute is possible

A reroute may be requested only when all of these are true:

1. The shipment status is in_transit, held or delayed. A delivered shipment cannot be rerouted.
2. The shipment's contract names an alternate hub. Only that hub may be used. The desk may not pick a different hub, even a quieter one.
3. The alternate hub status is not closed.
4. The alternate hub accepts reroutes. Some hubs, for example the Holmquay Port Terminal when it is congested, do not accept reroutes at all.

If any condition fails, do not submit the request. Explain the reason to the shipper and offer to check the ETA at the current hub instead.

## Steps

1. Track the shipment and note its status and alternate hub.
2. Check the alternate hub's capacity record: status, utilisation and the accepts-reroutes flag.
3. If the hub qualifies, submit the reroute request. The system pauses the request and asks a human approver to confirm.
4. Tell the shipper the request is waiting for approval. Do not describe the reroute as done.
5. When the approval is granted and the system returns the status rerouted, report the hub the shipment goes via and the new ETA window.
6. If the approver denies the request, the shipment stays where it is and nothing is changed. Tell the shipper that clearly.

## Approval

The approver is the on-duty hub operations lead for the shipment's current hub, with the desk supervisor as backup. The approval target is 2 hours from submission. Approvers check cargo type, cutoff times at the alternate hub and any special handling notes on the record, such as temperature limits or fragile loads.

## Logging

Every reroute request is written to the audit log with the shipment record as it was before the change and the result after it. This lets the team reconstruct what the shipment looked like at the moment of the decision. Denied requests are logged too.

## Fees

A reroute normally carries an handling fee under the shipper's contract. The fee is waived when the delay is caused by a Tallowgate hub closure, for example a yard closure or a hub shutdown. It is not waived for delays caused by weather, third-party carriers or incorrect paperwork.

## Limits

A shipment may be rerouted once. A second reroute needs sign-off from the operations director. Shipments carrying dangerous goods or temperature-controlled loads may be rerouted only to a hub approved for that cargo type, as listed in the Hub Directory.
