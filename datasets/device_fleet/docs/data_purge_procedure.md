# Device Data Purge Procedure

A purge permanently deletes the data a device has produced on the platform. It is the most destructive command in the fleet desk's toolbox, and it cannot be undone. This page covers who may run it, what it removes, the order of its steps, how the approval works and what to expect while it runs.

## Who may purge

Only an owner or an admin may request a purge. Viewers and operators cannot. Whoever asks, the purge always waits for a human approval before anything is deleted; in Kestrelway the approver is the fleet lead on duty. In the harness's dev console the user acts as an admin, so the request reaches the approval gate.

## The three scopes

A purge works on up to three scopes:

- `workflows`: the workflow runs recorded for the device, including their attachments.
- `telemetry`: the recorded telemetry sessions and the readings inside them, along with their stored files and the ingest log.
- `media`: the media sessions and their stored files.

A normal request covers all three scopes. Name the scopes when you describe the request so the approver knows exactly what will go.

## The ordered steps

The platform deletes in a fixed order, and a purge reports its progress against these steps:

1. `workflows.attachments`
2. `workflows.stop`
3. `workflows.runs`
4. `telemetry.s3`
5. `telemetry.sessions`
6. `telemetry.ingestLog`
7. `telemetry.readings`
8. `media.s3`
9. `media.sessions`
10. `done`

Workflows are stopped before their runs are removed, and stored files are removed before the records that point to them. You do not control the order; knowing it helps you read a progress report.

## The preview and the approval

Before anything is deleted, the request produces a preview with counts: how many workflow runs, telemetry sessions and media sessions would be removed. For example, the preview for Reefer 41 (KW-0041) lists 412 workflow runs, 37 telemetry sessions and 9 media sessions. Show these counts to the approver.

The human approval is the confirmation. There is no separate "are you sure" step after it. This is why the approval request must state the asset, the scopes and the counts, and why you must never describe a purge as harmless. If the approver denies the request, nothing is deleted and you say so.

## Statuses to poll

After approval the purge is queued and then runs. Its status is one of `queued`, `running`, `succeeded` or `failed`. Poll the status rather than assuming success. A purge of an old trial device was approved earlier this month and finished in 11 minutes; larger devices take longer, so a `running` status for a while is normal.

## Errors

Two conflicts can block a request, both answered with 409:

- `purge_in_progress`: a purge of this device is already running. Do not start another; wait for the first to finish. Linehaul Tractor 27 (KW-0027) answers this way today.
- `transfer_pending`: the device is in the middle of an ownership transfer, and its data must stay until the transfer completes.

An offline device is also refused with 409 `device_offline`.

## It cannot be undone

Purged data is gone. There is no restore, no recycle bin and no copy kept by the desk. Before you ask for approval, check whether anyone still needs the sessions: the fleet lead may want a report first. If the question is only about a device that stopped sending data, a purge is the wrong tool; diagnose the cause instead.

## What the desk should say

Say that the purge is irreversible, name the scopes and the preview counts, say that it waits for the fleet lead's approval, and do not claim anything was deleted until the status reads `succeeded`.
