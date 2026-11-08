# Session Insights Reference

A session is one recorded trip of one asset. The platform computes its insights from the telemetry the device uploaded. This reference describes every field so that reports quote the numbers exactly as the insights give them.

## The rule for reports

Quote numbers exactly as returned, with their units. Do not round, convert or recompute them. If a number looks odd, say so and point at the likely reason, but do not replace it.

## duration

The `duration` block has four values in milliseconds:

- `totalMs`: wall-clock time from the first to the last reading. It includes gaps where the device was not uploading.
- `recordedMs`: time covered by actual readings. Use this for reports.
- `movingMs`: time with speed over 2 km/h. Use this for driving time.
- `idleMs`: recorded time that was not moving.

Use `recordedMs` and `movingMs` in reports, not `totalMs`, because `totalMs` includes upload gaps and overstates the trip. For example, a trip with `totalMs` of 7140000 and `recordedMs` of 6960000 has 180000 ms of gaps.

## The moving threshold

A reading counts as moving when speed is over 2 km/h. Slower readings are treated as standing still, which keeps GPS drift at a red light from counting as driving.

## distance

`distance.km` is computed from GPS fixes, not from the odometer. A fix is used only when its fix quality is 1 or better and its HDOP is 5 or less. A jump of more than 60 m/s between two fixes is rejected as a GPS glitch, and steps under 10 m are ignored so that a parked vehicle does not slowly "drive". Because of these filters, a weak-fix trip can show a shorter distance than the route really was.

## speed

The `speed` block has `maxKph` (the top speed), `maxAt` (the time it happened) and `avgMovingKph` (the average over moving time only, so stops do not drag it down). Quote the top speed with its time when asked about speeding, but remember that exceeding a limit is decided by the rules engine, not by the top speed alone.

## stops

`stops.count` is the number of stops of 60 s or longer. Shorter pauses, such as a traffic light, do not count. A multi-drop vehicle shows many stops; a trunk route shows few.

## vehicle

The `vehicle` block needs OBD data: engine state, maximum rpm, maximum coolant temperature, fuel at the start and end with the amount used, and the count of fault codes. A device without OBD returns no vehicle block. A basic OBD device returns only the speed and rpm values.

## deviceStatus

`deviceStatus.worstCode` is the worst device status code seen during the session, for example `ok` or `gps_weak_fix`. It explains data quality problems: a trip with `gps_weak_fix` may have gaps in its distance.

## Short sessions

A session under 2 km gets no driver score. Its insights are still valid, but the score is empty with the reason `too_short`.
