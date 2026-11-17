# Webhook Signing and Secret Rotation

Kestrelway's platform delivers driver events (overspeed, harsh braking and the others) to the fleet backend by webhook. Every delivery is signed, and the receiver checks the signature before it accepts the event. This page explains how that works, what happens when the shared secret is rotated, and how to recover when deliveries start failing with 401.

## How signing works

Each delivery carries a signature made with the shared signing secret. The platform computes it over the delivery, and the fleet backend's webhook receiver recomputes it with its own copy of the secret. If the two match, the delivery is accepted and stored. If they do not match, the receiver refuses it with 401 `signature_mismatch`.

The signature proves two things: the delivery came from the platform, and it was not changed on the way. Because both sides must hold the same secret, the secret has to be changed on both sides together.

## Rotating the secret

Kestrelway rotates the signing secret each quarter, in the platform console. The important rule is timing: rotation takes effect at once. From the moment the new secret is saved, the platform signs every delivery with it. The receiver keeps checking with the old secret until someone updates it, so the receiver must be updated in the same change as the rotation.

Rotation procedure:

1. Generate the new secret in the platform console.
2. Put the new secret into the webhook receiver's configuration immediately, before or in the same change.
3. Confirm with a test delivery that the receiver accepts a signed event.
4. Record the rotation time in the change log.

If step 2 is skipped or delayed, deliveries made after the rotation are all refused.

## The symptom: 401 signature_mismatch

When the receiver still uses the old secret, every delivery fails the check and the receiver answers 401 `signature_mismatch`. The platform treats this as a failed attempt and retries.

What the desk sees:

- The driving rule whose events are delivered shows sync status `error`, with an error text naming the 401 and the time of the first failure.
- The inbox shows deliveries counted as `pending` while they are still retrying, and `failed` once every attempt is used up. The run behind each delivery stays in `started` until the delivery succeeds or fails.
- Events that arrived before the rotation show as `accepted`; the failures begin right after the rotation time.

A real example from this fleet: the secret was rotated on 2026-10-21 at 08:12 UTC, and the first 401 on Linehaul Tractor 27 (KW-0027) came at 08:15 UTC, three minutes later. That timing is the clue: when the first error follows a rotation closely, the cause is almost always a receiver still holding the old secret.

## Retries and the failure window

After the first attempt, the platform retries at 60 seconds, 5 minutes, 30 minutes, 2 hours, 6 hours, 12 hours and 24 hours. That is eight attempts over about 44 hours. A delivery that is still inside that window is `pending`. A delivery that has used all eight attempts is `failed`. Deliveries refused for good with 400 or 410 are `rejected` and are not retried, so a signature problem does not end up there; it stays retryable.

## The fix

1. Update the receiver with the current signing secret (the receiver's owner does this).
2. Redeliver the failed deliveries. This is a command for an owner or an admin; viewers and operators cannot do it.
3. Resync the asset's driving rules so the sync status returns to `ok`. A rule change applies after the resync, usually within 120 seconds.
4. Check the inbox again: the `pending` count should drain and new events should be `accepted`.

Pending deliveries will often succeed on their own once the receiver is fixed, because their next retry now passes the check. Failed ones need the redelivery.

## What the desk should say

State the cause (receiver still on the old secret), give the timeline (rotation time, first error, how many runs failed and how many are still retrying), and list the fix in order. Do not claim the problem is fixed until the inbox shows accepted deliveries again.
