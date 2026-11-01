# Pack brief: logistics

Business: **Tallowgate Freight**, a fictional regional freight forwarder with six hubs
(three-letter hub codes you invent, for example `KRN`, `MAV`). Domain: logistics. Shipment
ids look like `TG-604118`. Carrier contracts are with invented carriers.

Feature it demonstrates: **multi-step tool use.** One question takes several loop steps, one
tool per step: track a shipment, check the alternate hub's capacity, reroute it (a `write`
tool that policy sends to a human for approval), and get the new ETA. The diagram shows the
loop going round model, tools, policy several times. Feature nodes: `loop`, `tools`,
`policy`.

System prompt rules to add: you are the operations desk assistant for shippers; work one tool
at a time and read each result before the next call; reroute only through the alternate hub
on the shipment's contract and only when that hub is not `closed`; a reroute needs a human
approval; never invent an ETA; operations desk hours are 24/7, account managers Monday to
Friday 7:00 to 19:00.

Tools:

1. `track` (`read`): key `shipment_id`, pattern `^TG-[0-9]{6}$`. Rows: shipment_id, status
   (`in_transit`, `held`, `delayed`, `delivered`), current_hub, origin, destination,
   contract (a carrier contract code like `CC-17`), alternate_hub, last_event. 7 rows.
2. `eta` (`read`): key `shipment_id`. Rows: shipment_id, eta (date and time window),
   confidence (`high`, `medium`, `low`), delay_reason (or `none`). Same 7 shipments.
3. `hub_capacity` (`read`): key `hub_code`, pattern `^[A-Z]{3}$`. Rows: hub_code, name,
   utilisation_pct, status (`open`, `congested`, `closed`), accepts_reroutes (`yes` or `no`),
   cutoff (daily cutoff time). 6 rows.
4. `reroute` (`write`, with `audit: { before: track }`): key `shipment_id`. Rows for the
   shipments whose alternate hub is open and accepts reroutes: shipment_id, status `rerouted`,
   via (the alternate hub), new_eta. `missing`: `{ error: reroute_not_possible }`.

Skills (3 to 5): `tracking`, `delay_handling` (track, then eta, then explain), `rerouting`
(track, then hub_capacity on the alternate hub, then reroute only if open, then report),
`hub_questions`, optionally `claims`.

Documents (12 to 15): shipment tracking SOP, delay escalation SOP (escalate after 24 hours
late), reroute SOP, hub directory (all six hubs with codes, districts, cutoffs, capacity), three
carrier contract summaries (synthetic: service levels, liability per kilogram, claim windows),
cargo claims procedure (claims within 21 days), dangerous goods acceptance policy (generic,
which classes are refused), packaging and pallet standards, customs paperwork checklist for
cross-border lanes (invented regions), peak season surcharge notice, holiday operating
schedule. Two of them are PDFs: the reroute SOP and the hub directory.

Golden cases: 24. Tags: `multistep`, `tool`, `approval`, `knowledge`, `skill`. Include:

- 1 case that needs three tools in order: `track`, then `hub_capacity` on the alternate hub,
  then `reroute` (approved), then the final reply. One tool call per model call; each later
  call's `expect.last_message_contains` holds a value from the previous tool row (for example
  the alternate hub code).
- 1 four-tool case: `track`, `hub_capacity`, `reroute` (approved), `eta`.
- 2 reroute cases denied (`"approval": "deny"`) after `track` and `hub_capacity`.
- 1 case where `hub_capacity` says the alternate hub is `closed`, so the reply does not
  reroute (two tools only).
- 5 two-tool cases: `track` then `eta` (3 shipments), `track` then `hub_capacity` (2).
- 4 single-tool cases: track, eta, hub capacity, an unknown shipment (`TG-999999`).
- 2 skill cases: `read_skill` (`rerouting` or `delay_handling`) then two tools.
- 8 knowledge cases with citations: escalation time, claim window, liability per kilogram on
  one contract, a hub's cutoff from the directory, dangerous goods refused, pallet height
  limit, peak surcharge amount, holiday closure date.
