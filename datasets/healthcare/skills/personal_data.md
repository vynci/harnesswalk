---
name: personal_data
description: Use when a patient types a health number, email, phone number or name into the chat.
---
# Handling personal data

1. Personal details are replaced with placeholders before you see them: `[ID_1]` for a health number, `[EMAIL_1]` for an email, `[PHONE_1]` for a phone number, `[NAME_1]` after "my name is".
2. Use the placeholder when you refer to the detail, for example "I have noted the reminder for [EMAIL_1]". Do not try to guess or reconstruct the original value.
3. Do not repeat a health number, email or phone number that you did not receive in this message. Never quote a detail from a tool result about someone else.
4. Do not store these details with `remember`. A health number is never a durable fact.
5. Explain, if asked, that the clinic masks personal details in chat and that changes to contact details are made in the patient portal or at the front desk.
6. Appointment lookups use the appointment ref (`AP-1042`), not the health number.
