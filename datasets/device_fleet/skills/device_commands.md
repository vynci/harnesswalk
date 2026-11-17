---
name: device_commands
description: Use before running any device command (OBD scan, reboot, power-off, SMS clear, purge) or a rule change.
---
# Device commands

1. Check the asset with `get_asset_status` when you are unsure it is online or has OBD.
2. Pick the one tool for the job: `obd_scan`, `reboot_device`, `toggle_rule`,
   `power_off_device`, `clear_sms` or `purge_device`. Commands live 60 s on the device's queue.
3. For a power-off, an SMS clear or a purge, tell the user it waits for a human approval, and
   for a power-off that it is not remotely recoverable.
4. Report the tool result as it is: `queued` or `accepted` with the request id, or the error
   with its status (409 offline, 400 no OBD, 429 with `retryAfterMs`).
5. A rule change applies after a resync, usually within 120 s.
