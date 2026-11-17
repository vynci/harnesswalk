---
name: overspeed_triage
description: Use when someone asks whether an overspeed event is credible, or whether to keep or drop it.
---
# Overspeed triage

1. Call `get_driver_events` for the asset and find the event by its `EV-` id.
2. Read the payload: `speedKph`, `limitKph`, `limitSource` (`osm`, `here`, `assumed_urban`),
   `limitAgeS`, `heldLimitKph`, `band`.
3. Drop it as implausible when `limitKph` is under 20 km/h: no public road the fleet drives has
   such a limit, so the map data is wrong.
4. When the limit was held (`heldLimitKph` set) or is stale (`limitAgeS` over 60 s), re-judge
   it against the assumed urban limit of 50 km/h: keep it only if `speedKph` is over 55.
5. Otherwise keep it: the limit is fresh and plausible and the speed is over limit plus 5 km/h.
6. Say which rule decided, cite the speed limit sources guide, and remind the user that the
   score counts an event only once its review is done.
