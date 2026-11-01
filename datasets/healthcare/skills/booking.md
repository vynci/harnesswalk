---
name: booking
description: Use when a patient asks to find, change, cancel or prepare for an appointment.
---
# Booking and changing appointments

1. Ask for the appointment ref (format `AP-1042`) if the patient has not given one.
2. Call `find_appointment` with the ref. If the result is `not_found`, say you cannot find that ref and ask them to check the confirmation message in the portal.
3. Tell them the site, service, date, time, clinician, status and the `prep` note from the result.
4. For changes or cancellations, explain the 24-hour rule: cancelling or moving an appointment with less than 24 hours notice may carry a 25 dollar late cancellation fee. Read the `appointment_cancellation_policy` document if they ask for details.
5. Never cancel or rebook for them. Send them to the patient portal or the front desk, and say the action is theirs to confirm.
6. If the status is `waitlisted`, say the clinic will call if a slot opens and that it is not a confirmed booking.
