# Driving Rules Explainer

Kestrelway's driver events are not judged by the assistant or by a person watching a chart. They come from a rules engine on the telematics platform that runs the same deterministic checks on every sample. This page lists each event kind, the rule behind it, and the numbers it uses. When someone asks "why did this event fire", the answer is always one of the rules below.

## How a rule decides

Every rule reads one or more signals (obd.speed, gps.speed, obd.pedal, obd.rpm, the check-engine lamp) and compares them with a threshold. Three settings keep a rule from firing on noise. A **debounce** or hold time means the condition must stay true for a set time before the event fires. A **hysteresis** margin means the signal must fall back below the threshold minus that margin before the rule can arm again. A **cooldown** is the quiet time after an event during which the same rule will not fire again. Each event also carries a **severity** (moderate or severe) and a **confidence** (high or low).

## Overspeed

The kind is `overspeed`. It has two rules: `overspeed-obd`, which reads obd.speed, and `overspeed-gps`, which reads gps.speed. Either fires when the speed is over the posted limit plus 5 km/h and stays there for 10 s (the debounce). The cooldown is 60 s. The band comes from how far over the limit the vehicle was: under 10 km/h is band 1, under 20 is band 2, under 30 is band 3, and anything else is band 4. Bands 3 and 4 are severe. Confidence is high. The limit itself comes from a map source and can be held or stale, which is covered in the speed limit sources guide.

## Wide-open throttle and over-revving

Both kinds come from the rule `throttle`. The kind `wot` reads obd.pedal and fires when the pedal is at 85 percent or more for 2 s, with a hysteresis of 5. The kind `over_rev` reads obd.rpm and fires at 4500 rpm or more for 2 s, with a hysteresis of 200. The cooldown for each is 10 s. Both are moderate and carry high confidence. Because they need OBD data, they only exist on devices with the full tier.

## Check engine

The kind `check_engine` comes from the rule `check-engine`. It fires when the check-engine lamp is on while the speed is over 5 km/h, held for 30 s. It fires once per session, so a lamp that stays lit all day produces one event per trip, not hundreds. It is moderate.

## Harsh braking and harsh acceleration

The kinds `harsh_brake` and `harsh_accel` come from the rule `harsh-speed-delta-obd`, which measures the change of obd.speed per second. Braking at -3.5 m/s2 or harder fires `harsh_brake`; it becomes severe at -5.0. Accelerating at 3.0 m/s2 or more fires `harsh_accel`; it becomes severe at 4.0. The cooldown is 5 s. Confidence is low, because the signal is a difference between two speed readings and any noise in either reading shows up as acceleration.

Kestrelway has a firm rule here: speed-delta harsh rules are only reliable at 5 Hz or more. A device that samples at 1 Hz has one speed reading per second, so a single noisy sample reads as a harsh event. The rule should be off on such devices, and events with the method `speed_delta_1hz` are the sign that it was left on. Shop Pickup 2 (KW-0002), which samples at 1 Hz, is the example: its harsh events on gentle stops are artifacts, and the workshop asked to keep the rule off there.

A GPS-based harsh rule also exists, but it is never attached to any asset. GPS static-hold artifacts, where a parked or crawling vehicle's position jumps, caused 11 to 29 false events per drive.

## Close following

The kind `close_following` is a beta, camera-based rule. It fires when another vehicle is within 15 m while the speed is 30 km/h or more, with a cooldown of 30 s. Confidence is low, so treat it as a lead to review, not a finding.

## Toggles and resync

Each asset has its own toggle for six rules: `overspeed-obd`, `overspeed-gps`, `throttle`, `check-engine`, `harsh-speed-delta-obd` and `close-following`. Changing a toggle does not act instantly. The platform runs a rule resync, which usually completes within 120 s. The sync status of each rule reads `ok` (in sync), `pending` (a resync is in flight), `error` (the last resync or delivery failed) or `off` (the rule is disabled).

## Low confidence in practice

Low confidence does not mean wrong. It means the signal is noisy, so the events are counted but should be reviewed before anyone acts on them, for example before a conversation with a driver.
