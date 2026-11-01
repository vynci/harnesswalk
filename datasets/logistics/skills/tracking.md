---
name: tracking
description: Use when a shipper asks where a shipment is or what its current status is.
---
# Tracking a shipment

1. Check the id looks like TG- followed by six digits. If not, ask the shipper to confirm it.
2. Call `track` with the shipment id.
3. If the result says `not_found`, say the id is not on file and suggest checking the booking confirmation. Stop there.
4. Report the status, the current hub, the origin and destination, and the last event in plain words.
5. If the status is `held` or `delayed`, offer to check the ETA with `eta`. Do not call it unless asked or needed.
6. If the status is `delivered`, say so with the last event and do not offer a reroute.
