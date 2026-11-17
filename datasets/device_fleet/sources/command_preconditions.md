# Device Command Preconditions

A command is a small message sent from the platform to a device. Before one is accepted, the platform checks that the device can act on it. This page lists the commands, what each needs, and the errors a caller can get back. Who may run which command is in the roles and permissions guide; this page is about the device side.

## The commands

Kestrelway fleet staff can use five commands, plus rule toggles:

- **OBD scan**: asks the device to read the vehicle's OBD data now.
- **Reboot**: restarts the device. It is usually back in about 90 s.
- **Power-off**: switches the device off.
- **SMS clear**: deletes every message on the device's modem.
- **Purge**: deletes workflow runs, telemetry and media for the device.
- **Rule toggle**: turns a driving rule on or off. It applies after a rule resync, usually within 120 s.

## Queue time

A command does not run instantly. It waits on the device's queue and is delivered when the device picks it up. A command lives for 60 s on the queue. If the device has not picked it up by then, the command expires and nothing happens. A caller can ask for a longer time, but the device clamps any requested value above 120 s down to 120 s. Because of this, a command that returns `queued` or `accepted` has not necessarily run yet; check the result by the request id.

## The device must be online

Commands need an online device. If the device is offline, the platform answers **409 `device_offline`**. Reefer 41 (KW-0041), which has been offline since 2026-10-19, is the example. Rule toggles are the exception: they are stored and applied when the device reconnects.

## OBD scan errors

- **400 `not_obd_device`**: the device has no OBD connection. City Hauler 12 (KW-0012) is a GPS-only device, so a scan will always fail there.
- **429 `rate_limited`**: only one scan is allowed per 60 s per device. The answer includes `retryAfterMs`, the time to wait. Linehaul Tractor 27 (KW-0027) might answer with `retryAfterMs: 41000`, meaning wait 41 s.

A device with the basic OBD tier answers a scan with speed and rpm only.

## Publish failures and repeated requests

If the platform cannot hand the command to the message queue, it answers **502 `command_publish_failed`**. Nothing reached the device, and the caller may try again. Each command carries a request id. A repeated request id is refused, so a retry must be sent as a fresh command, not a replay of the old one.

## Timing and consequences

A reboot makes the device unavailable for about 90 s; telemetry during that time is not recorded. A power-off is different: it is **not remotely recoverable**. The device stays off until someone restores power at the vehicle, for example by cycling the ignition or reconnecting the supply. Plan a power-off only for a vehicle that is in a place where someone can switch it back on.

SMS clear and purge are irreversible too. Power-off, SMS clear and purge therefore always need a human approval before they are sent, as described in the roles guide.

## Reporting back

When reporting a command to a user, give the result exactly: `queued` or `accepted` with the request id, or the error and its status code. Never say a command ran unless the tool result says it was queued or accepted, and remember that queued still means waiting on the device.
