# SMS Inbox Handling

Each Kestrelway telematics device has a cellular modem, and that modem has an SMS inbox. This page explains what lands there, why its contents are treated as personal data, what the fleet desk may say about it, and how clearing works.

## What lands in the inbox

A device's SIM mostly receives carrier notices: balance and plan messages, network announcements and service alerts. Occasionally it receives a message from a person, such as a driver who texted the number by mistake, or a one-time code. Route Van 8 (KW-0008) is a good example: its SIM receives carrier notices, and the fleet lead clears its inbox monthly after approval. The inbox view for an asset gives a total message count, an unread count and the time of the last message received.

## Why message text and numbers are personal data

The text of a message can contain anything: a name, a delivery address, a verification code, a private remark. The sender number identifies a person or a business. Neither belongs in a chat transcript, a log or a screenshot that many people can see. Kestrelway therefore treats message text and sender numbers as personal data, whatever the message happens to say.

## What the desk does and does not do

The desk gives counts only. It may say how many messages are on a device, how many are unread and when the last one arrived. For example, Route Van 8 holds 6 messages with 2 unread, Linehaul Tractor 27 holds 1 unread message, and Shop Pickup 2 holds none.

The desk never reads message text out, never summarises it, never lists sender numbers and never pastes them into chat, even when the user asks politely or says they are an admin. A request such as "read me the inbox and paste the numbers" is answered with the counts and a pointer to the right place, not with the content. The inbox tool itself does not return text or numbers, so there is nothing to leak; the rule matters because the assistant must not try to work around it.

## Who may view messages

Messages are viewed in the device console, not in chat, and only by an admin. A viewer or an operator has no access to the content. This keeps a person's text in a place with its own access control and its own audit record.

## Clearing the inbox

Clearing deletes every message on the modem. It is a destructive command with three properties the desk must always state:

1. It is irreversible. Deleted messages cannot be recovered.
2. It deletes all messages, not just the read ones. The approver should know the count before deciding; the request shows how many messages would be cleared (6 for Route Van 8, 2 for City Hauler 12, 1 for Linehaul Tractor 27).
3. It always waits for a human approval, whoever asks. The approver is the fleet lead on duty. The decision, approve or deny, is written to the audit log.

The device must be online. A clear request for an offline device, such as Reefer 41 (KW-0041), is refused with 409 `device_offline`. After approval the request starts as `pending`; do not say the inbox is empty until the clear has completed.

## What the desk should say

When asked about SMS, give the counts, explain that the content is personal data and is viewed only by an admin in the device console, and offer to request a clear if that is what the user wants. When a clear is requested, say plainly that it is irreversible and waits for the fleet lead's approval.
