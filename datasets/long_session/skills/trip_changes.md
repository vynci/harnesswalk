---
name: trip_changes
description: Use when a traveller changes dates, travellers, hotels or crossings on a trip, or asks what they changed earlier.
---
# Trip changes

1. Find the trip reference (TR- and four digits) in the chat; ask for it only if it was never given.
2. Call `trip_lookup` to see the trip as booked.
3. Compare the booking with the changes the traveller asked for in this chat, oldest first.
4. List each change once, with the new value, and say which ones the desk still has to make
   (you can only read bookings).
5. If an earlier change is no longer in the chat or its summary, say so and ask for it again.
