# OBD Tiers and Vehicle Data

Kestrelway's devices connect to a vehicle in one of three ways, called OBD tiers. The tier decides which signals exist, which driving rules can run and what the vehicle insights contain.

## The three tiers

**Full.** The device reads speed, rpm, accelerator pedal position, coolant temperature, fuel level and fault codes. Shop Pickup 2 (KW-0002), Linehaul Tractor 27 (KW-0027) and Reefer 41 (KW-0041) are full-tier.

**Basic.** The device reads only speed and rpm. Route Van 8 (KW-0008) is basic-tier. There is no pedal, coolant, fuel or fault code data.

**None.** The device has no OBD connection and relies on GPS alone. City Hauler 12 (KW-0012) is in this tier. Speed comes from the GPS fix, which is why a weak fix matters more for this vehicle.

## Which rules each tier supports

- **Full:** all rules can run: `overspeed-obd`, `throttle` (wide-open throttle and over-rev), `check-engine` and `harsh-speed-delta-obd`, plus the camera beta rule where a camera is fitted.
- **Basic:** `overspeed-obd` and the over-rev part of `throttle`, since speed and rpm exist. Rules that need pedal position or the check-engine lamp cannot run.
- **None:** only `overspeed-gps` applies. The OBD rules have no signal to work with, and an OBD scan is refused with 400 `not_obd_device`.

A rule that needs a signal the tier lacks should stay off. Turning it on does not create the signal.

## Sample rate matters too

The tier says which signals exist; the sample rate says how reliable the speed-delta rules are. The harsh braking and acceleration rules estimate acceleration from the change in speed per second. Kestrelway's rule is that they are only reliable at 5 Hz or more. At 1 Hz, as on Shop Pickup 2, one noisy speed sample can read as a harsh event, so those events are marked low confidence and the rule should be off on such devices.

## Vehicle data in the insights

The `vehicle` block of a session's insights depends on the tier:

- Full tier: engine state, maximum rpm, maximum coolant temperature, fuel at the start, at the end and used, and the number of fault codes.
- Basic tier: engine state and maximum rpm only.
- None: no vehicle block. The insights still have duration, distance, speed, stops and device status, all from GPS.

## The OBD scan command

An OBD scan asks the device to read the vehicle's OBD data now. It needs an online device with OBD, so it is refused with 409 `device_offline` when the device is offline and 400 `not_obd_device` on a GPS-only vehicle. Only one scan per 60 s is allowed per device; a second request returns 429 `rate_limited` with `retryAfterMs`. On a basic-tier device the scan returns speed and rpm only.

## Reporting guidance

When a user asks for vehicle data and the asset is GPS-only or basic, say which signals the tier has rather than saying the data is missing. When a user asks to enable a rule the tier cannot support, explain the limit instead of toggling it.
