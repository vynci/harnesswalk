# Event Inbox Outcome Glossary

Every driver event the rules engine records is delivered to the fleet backend as a webhook. The event inbox keeps one delivery record per event and classifies it into exactly one outcome class. This glossary explains the six classes, how retries work and why a run can look stuck.

## The six outcome classes

**accepted.** The receiver answered with a 2xx status and stored the event. This is the normal outcome. Example: an overspeed event on City Hauler 12 is delivered, the backend stores it and answers 200.

**duplicate.** The receiver already had this event, either because the event id matches or because the asset, kind and time match an event it stored earlier. The copy is dropped, and it counts as delivered. A duplicate is not an error: it usually means a retry arrived after the first attempt had in fact succeeded.

**ignored.** The delivery was accepted but a rule discarded the event, so it is not counted. The inbox records the reason. Example: an overspeed event whose speed limit was 10 km/h is ignored with reason `implausible_limit`.

**rejected.** The receiver refused the delivery for good, with a 400 (bad payload) or a 410 (gone). Rejected deliveries are not retried, because sending the same payload again would give the same answer.

**failed.** Every one of the 8 attempts errored, for example with 401, a 5xx status or a timeout. After the last attempt the delivery is marked failed and stops retrying. An owner or an admin can redeliver it by hand once the cause is fixed.

**pending.** The delivery is still inside its retry schedule. It has not yet succeeded and has not yet used all its attempts.

## The retry schedule

A delivery is attempted once straight away. If that attempt errors, the platform retries after 60 s, then 5 min, 30 min, 2 h, 6 h, 12 h and 24 h. That is 8 attempts in total, spread over about 44 hours. Only errors that might clear up are retried: a timeout, a 5xx or a 401 caused by a secret that is later fixed. A 400 or a 410 is final at once and goes straight to `rejected`.

## Why a run stays "started"

Each event triggers a workflow run that ends when its delivery ends. While the delivery is `pending`, the run stays in the `started` state: it is waiting for the receiver. The run becomes `succeeded` when a delivery finally lands, and `failed` when the last attempt errors. So a pile of runs sitting in `started` is a sign of many pending deliveries, which in turn points at a receiver problem. Check the inbox counts and the last error before looking anywhere else.

## Deduplication

Two things identify a duplicate: the event id, or the combination of asset, event kind and event time. Deduplication protects the driver score from double counting when a retry crosses with an earlier success. Duplicates count as delivered, so they never show up as failures.

## The ignored reasons

- `implausible_limit`: the speed limit at the event was under 20 km/h. No road the fleet drives has such a limit, so the map data is wrong and the event is dropped.
- `held_limit`: the limit was held (the last known limit kept while the lookup failed) or stale (older than 60 s), and the speed was not over 55 km/h once re-judged against the assumed urban limit of 50 km/h.
- `too_short`: the session was under 2 km, so no score is made for it.

## Reading an inbox summary

Quote the counts by class and the last error exactly as the inbox shows them. Add up nothing yourself: the classes are separate, and a delivery is in one class only. When `failed` and `pending` are both high and the last error is the same on every delivery, suspect one shared cause such as a changed signing secret.
