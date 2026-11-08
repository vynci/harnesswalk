# Device Status and Health Flags

This reference explains what the fleet desk sees when it asks for a device's status: the LED state, the health flags, the last seen time, the agent version and the sample rate. It applies to all five Kestrelway assets: KW-0002, KW-0008, KW-0012, KW-0027 and KW-0041. Status is read with the asset status lookup, never guessed. The desk is staffed 07:00 to 19:00, Monday to Saturday, and all times are UTC.

## What a status record contains

Every status record names the asset and whether it is online. It also gives the last seen time, the agent version, the LED state, a list of health flags (empty when the device is healthy), the sample rate in hertz and the OBD tier. The vehicle's make, model and year are included for orientation. When you report status, quote these values as returned, and say plainly when a field is missing or empty.

## LED states

The LED on the device shows one of these states, and the status record repeats it as `led_state`:

- `green_solid`: normal operation. The device has power, a network and a GPS fix.
- `green_blink`: the device is starting up. Give it a minute or two; if it stays here, treat it as a boot problem and consider a reboot (admins and owners only).
- `amber_solid`: no network. The modem has no usable connection, so the device cannot upload telemetry or receive commands.
- `amber_blink`: no GPS fix. The device is on the network but cannot place the vehicle, so distance and speed from GPS become unreliable.
- `red_solid`: fault. The device has detected an internal error. Report it and ask a workshop technician to inspect the unit.
- `unknown`: the platform has no recent state because the device is offline. This is what Reefer 41 (KW-0041) shows while it is parked at the Quellby depot with its battery disconnected.

An LED state is the device's own last report. A device that is offline can still show an old state on its hardware, so use `online` and `last_seen` together with `led_state`.

## Health flags

Health flags are short codes the device raises when it notices a condition. A healthy device has none. Several can appear at once.

- `under_voltage`: the supply voltage dropped below the safe level. Likely causes are a weak or disconnected vehicle battery, a loose power lead or a long parked period. First action: check the vehicle battery and the device's power connection. Reefer 41 raised this flag before it went offline, and the depot later confirmed the battery was disconnected for the compressor repair.
- `gps_weak_fix`: the GPS signal is poor. Likely causes are an antenna under a steel panel, tall buildings or a parking structure. First action: note it when reading distance and speed, because weak fixes make GPS figures less reliable. City Hauler 12 (KW-0012) often shows this flag because its antenna sits under a steel roof panel and it works among tall buildings in the Brindleford centre.
- `lte_down`: the modem has lost its mobile data connection. Likely causes are a coverage gap or a SIM problem. First action: wait a few minutes in case the vehicle is driving through a gap, then check the last seen time. A device in this state cannot receive commands.
- `throttled`: the device is slowing itself down, usually because it is running hot or its processor is overloaded. First action: let it cool, and if the flag persists across several checks, ask the workshop to inspect the mounting and ventilation.
- `storage_low`: the device's local storage is nearly full. Likely cause: a long period without a successful upload. First action: check connectivity, since stored data clears once it uploads. Do not suggest a purge for this; a purge deletes platform data, not device storage, and always needs a human approval.

## Last seen and agent version

`last_seen` is the time of the device's most recent contact. A recent value (within a few minutes) with `online` true is normal. A value that is days old, as for Reefer 41 (last seen 2026-10-19 at 16:22 UTC), means the device is offline and commands will be refused with 409 `device_offline`.

`agent_version` is the software on the device. Most of the fleet runs 0.9.4. Linehaul Tractor 27 (KW-0027) still runs 0.9.2, with its update to 0.9.4 scheduled for its next yard visit, and Reefer 41 runs 0.9.3. A difference in agent version can explain a difference in behaviour, so mention it when you diagnose a device that acts differently from its siblings.

## Sample rate

The `rate_hz` value is how often the device samples. Shop Pickup 2 (KW-0002) samples at 1 Hz; the others sample at 10 Hz. Rate matters for driving rules: speed-delta harsh braking and acceleration rules are only reliable at 5 Hz or more, which is why the workshop asked to keep them off on slow devices.

## What the desk does with a status

Report the facts, name the likely cause from this page, and state the first action. Do not run a reboot unless an admin or owner asks. If the device is offline, say that commands cannot reach it and that a rule change will sync when it reconnects.
