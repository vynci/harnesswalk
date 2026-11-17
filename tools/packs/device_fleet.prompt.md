# Pack brief: device_fleet (Kestrelway Field Services)

## The business

Kestrelway Field Services is a small fictional field-services company with five vehicles
(assets) fitted with telematics devices: Shop Pickup 2 (KW-0002, the workshop's test vehicle,
samples at 1 Hz), Route Van 8 (KW-0008), City Hauler 12 (KW-0012, no OBD, GPS only),
Linehaul Tractor 27 (KW-0027) and Reefer 41 (KW-0041, offline at the Quellby depot). Places
are invented: the Brindleford yard, the Carrowmoor cross-dock, Ostley Cross, the Quellby depot.
The fleet desk is staffed 07:00 to 19:00, Monday to Saturday. The pack's "now" is
2026-10-23 09:00 UTC. The core files (system prompt, skills, tools, facts, episodes) are
already written and are given to you; the documents must agree with them exactly.

## The feature

Governed device commands: policy decides every tool call from rules in code, three
irreversible commands wait for a human approval, and every decision is audited. The
documents are the knowledge the assistant cites: how the rules engine detects events, how the
score is computed, what commands need, what the inbox outcomes mean.

## Facts the documents must state (use these numbers exactly)

Driving rules and event kinds (the rules engine runs on the platform, deterministically):
- `overspeed` (rules `overspeed-obd` with obd.speed, `overspeed-gps` with gps.speed): fires
  when speed is over the limit plus 5 km/h, held for 10 s (debounce); cooldown 60 s. Band from
  how far over the limit: under 10 km/h band 1, under 20 band 2, under 30 band 3, otherwise
  band 4; band 3 and up is severe. Confidence high.
- `wot` (rule `throttle`, obd.pedal): pedal at 85 percent or more for 2 s (hysteresis 5);
  cooldown 10 s. `over_rev` (rule `throttle`, obd.rpm): 4500 rpm or more for 2 s
  (hysteresis 200); cooldown 10 s. Both moderate, confidence high.
- `check_engine` (rule `check-engine`): the check-engine lamp on and speed over 5 km/h for
  30 s; once per session. Moderate.
- `harsh_brake` / `harsh_accel` (rule `harsh-speed-delta-obd`, the change of obd.speed per
  second): braking at -3.5 m/s2 or harder (severe at -5.0), accelerating at 3.0 m/s2 or more
  (severe at 4.0); cooldown 5 s. Confidence low. Kestrelway rule: speed-delta harsh rules are
  only reliable at 5 Hz or more; on a device that samples at 1 Hz a single noisy speed sample
  reads as a harsh event, so the rule should be off on such devices (method
  `speed_delta_1hz`). A GPS-based harsh rule exists but is never attached: GPS static-hold
  artifacts caused 11 to 29 false events per drive.
- `close_following` (beta, camera based): a vehicle within 15 m at 30 km/h or more; cooldown
  30 s; confidence low.
- Per-asset rule toggles: `overspeed-obd`, `overspeed-gps`, `throttle`, `check-engine`,
  `harsh-speed-delta-obd`, `close-following`. A change applies after a rule resync, usually
  within 120 s; the sync status reads `ok`, `pending`, `error` or `off`.

Speed limits:
- Sources: `osm` (open map data), `here` (a commercial map service), `assumed_urban`
  (no usable limit, 50 km/h assumed). `limitAgeS` is how old the limit was at the event.
- A held limit (`heldLimitKph`, the last limit kept while the lookup failed) or a stale one
  (`limitAgeS` over 60 s) is re-judged against the assumed urban limit of 50 km/h: the event
  is kept only if the speed is over 55 km/h.
- A limit under 20 km/h is implausible and the event is dropped; the inbox records it as
  `ignored` with reason `implausible_limit` (a held-limit drop is `held_limit`).
- "Low confidence" means the signal is noisy (speed-delta harsh rules, camera rules); such
  events are counted but should be reviewed before action.

Score (always labelled provisional):
- Weights: overspeed 5 x band; harsh_brake 4 (severe 8); harsh_accel 3; wot 1; over_rev 1;
  check_engine 0; close_following 0.
- score = clamp(round(100 - penalty x 10 / max(distanceKm, 10)), 0, 100).
- No score under 2 km (`scoreReason: too_short`). Bands: excellent 90 and up, good 75 and up,
  needs attention 60 and up, poor below 60. Events with a pending review are held out.
- Worked example: 7 overspeed events (three band 4, two band 3, two band 2) give a penalty of
  110; over 38.4 km the score is 71, needs attention.

Insights (per session): `duration` (totalMs, recordedMs, movingMs, idleMs; use recordedMs and
movingMs for reports, totalMs includes upload gaps; moving means over 2 km/h), `distance` (km,
from GPS fixes with fix quality 1 or better, HDOP 5 or less, jumps over 60 m/s rejected,
steps under 10 m ignored), `speed` (maxKph, maxAt, avgMovingKph), `stops` (60 s or longer),
`vehicle` (needs OBD: engine, rpm max, coolant max, fuel start, end and used, fault codes),
`deviceStatus` (the worst status code). Report numbers exactly as the insights give them.

OBD tiers: full (speed, rpm, pedal, coolant, fuel, fault codes), basic (speed and rpm),
none (GPS only: only `overspeed-gps` applies, no OBD scan).

Device status: `led_state` values green_solid (normal), green_blink (starting), amber_solid
(no network), amber_blink (no GPS fix), red_solid (fault), unknown (offline). Health flags:
under_voltage, gps_weak_fix, lte_down, throttled, storage_low.

Commands: OBD scan, reboot (back in about 90 s), power-off, SMS clear, purge, plus rule
toggles. A command lives 60 s on the device's queue (the device clamps any longer TTL to
120 s). The device must be online: otherwise 409 `device_offline`. An OBD scan on a device
without OBD: 400 `not_obd_device`. One OBD scan per 60 s per device: otherwise 429
`rate_limited` with `retryAfterMs`. A publish failure: 502 `command_publish_failed`. A repeated
request id is refused. Power-off is not remotely recoverable: someone must restore power at
the vehicle.

Roles (Kestrelway takes the stricter side where its fleet app and the device platform
disagree; the fleet app let operators reboot and power off, the device platform does not):
viewer reads only; operator reads and runs OBD scans; admin and owner also reboot, change
rules, power off, clear SMS, purge and redeliver webhooks; only the owner deletes the
organisation. Power-off, SMS clear and purge always need a human approval (the approver is
the fleet lead on duty), and every command decision is written to the audit log. In the
harness's dev console the user acts as an admin.

Inbox outcome classes (webhook deliveries of driver events to the fleet backend): `accepted`
(2xx, stored), `duplicate` (same event id, or same asset, kind and time; dropped, counts as
delivered), `ignored` (accepted but discarded by a rule: implausible_limit, held_limit,
too_short), `rejected` (refused for good: 400 bad payload or 410 gone; no retry), `failed`
(all 8 attempts over about 44 hours errored: 401, 5xx or timeout), `pending` (still retrying;
the run stays `started` until the delivery succeeds or fails). Retry schedule after the first
attempt: 60 s, 5 min, 30 min, 2 h, 6 h, 12 h, 24 h.

Webhook signing: each delivery carries a signature made with the shared signing secret; the
receiver checks it. Rotating the secret in the platform console takes effect at once, so the
receiver must be updated in the same change; otherwise every delivery gets 401
`signature_mismatch`. Fix: put the new secret in the receiver, redeliver the failed deliveries
(owner or admin), then resync the asset's rules.

Purge: owner or admin; scopes `workflows`, `telemetry`, `media`; ordered steps
workflows.attachments, workflows.stop, workflows.runs, telemetry.s3, telemetry.sessions,
telemetry.ingestLog, telemetry.readings, media.s3, media.sessions, done; a preview gives the
counts; the human approval is the confirmation; statuses queued, running, succeeded, failed;
409 `purge_in_progress` or `transfer_pending`. Irreversible.

SMS: a device modem receives carrier notices and occasional messages. Message text and sender
numbers are personal data: the desk never reads them out in chat; only an admin views them in
the device console. Clearing deletes every message on the modem, is irreversible and needs
approval.

## Documents

Write the documents the docs plan lists. Do not write any phone number, email address, web
address or street address. Do not name real map providers, vendors or places.
