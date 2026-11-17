---
name: incident_diagnosis
description: Use when driving rules show errors, runs are stuck or failing, or events stop arriving.
---
# Incident diagnosis

1. Call `get_driver_events` for the asset: read each rule's `syncStatus`, `error` and run counts.
2. Call `get_inbox` for the asset: read the outcome counts (`accepted`, `duplicate`, `ignored`,
   `rejected`, `failed`, `pending`), `last_error` and any rotation time.
3. Match the pattern to the inbox outcome glossary: `pending` deliveries keep their run in
   `started`; `failed` means every one of the 8 attempts over about 44 hours failed.
4. A 401 `signature_mismatch` after a signing-secret rotation means the receiver still checks
   the old secret.
5. Give the cause, the timeline (first error, how many runs failed and how many still retry),
   and the fix: update the receiver's secret, redeliver the failed deliveries (owner or admin),
   then resync the asset's rules. Cite the documents you used.
