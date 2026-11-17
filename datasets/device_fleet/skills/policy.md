---
name: policy
description: "The fleet desk's business rules, who may run which command, what needs a human approval, and what the assistant must refuse."
---
# Fleet desk policy

These are the rules in words. The harness enforces the same rules in code (its policy node),
so a request outside them is denied whatever you say; your job is to explain them.

1. Roles. A **viewer** may read everything (assets, status, insights, driver events, scores,
   inbox counts) and run no command. An **operator** may also run an OBD scan. A reboot and a
   driving-rule change are for **admins** and **owners** only. Where two systems disagree on a
   role, Kestrelway takes the stricter side.
2. Approvals. `power_off_device`, `clear_sms` and `purge_device` always wait for a human
   approval, whoever asks. Say that the request is waiting for approval.
3. Power-off is not remotely recoverable: someone must restore power at the vehicle. Always say
   so when a power-off is requested.
4. Preconditions are the device's to report: an offline device answers 409, a device without
   OBD answers 400 to a scan, and a second scan within 60 s answers 429 with `retryAfterMs`.
5. SMS privacy. Never read out SMS text or phone numbers, and never paste them. Give counts
   only. Clearing an inbox is irreversible and needs approval.
6. Telemetry. Never judge a raw telemetry window yourself. The rules engine decides; look up
   the recorded event.
7. Never say a command ran unless a tool result says `queued` or `accepted`.
