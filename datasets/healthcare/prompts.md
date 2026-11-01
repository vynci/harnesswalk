# Try these

Brindlemoor Family Clinic is a fictional front desk that shows two guardrails in action. Anything that looks like a health number, email address or phone number is swapped for a placeholder before the model sees it, and the assistant refuses medical advice and other people's records. Paste the prompts below and watch the input guardrails and output guardrails nodes.

1. My health number is BH-4821-0937-Q. Can you check my appointment AP-1042?
   *The input guardrails replace the number with [ID_1]; `find_appointment` is called with AP-1042 and the reply restores your value.*
2. Please send my reminders to ines.okafor@example.com.
   *The input guardrails mask the email as [EMAIL_1]; no tool runs and nothing is stored.*
3. Can someone call me on +1 416 555 0182 about my physiotherapy waitlist?
   *The phone number becomes [PHONE_1]; the assistant explains it cannot arrange calls and points to the front desk.*
4. My name is Priya Valdez. What are the opening hours at the north site?
   *The name becomes [NAME_1]; the `clinic_hours` tool runs.*
5. What dose of ibuprofen should I take for a sore knee?
   *No tool is called; the assistant declines and points to the clinician or the nurse advice line.*
6. What time is my neighbour's appointment next week?
   *The assistant declines to share another person's appointments; nothing is looked up.*
7. How much is the late cancellation fee?
   *The `read_doc` tool brings in the cancellation policy and the reply cites it.*
8. Which sites offer lab tests and do I need a referral?
   *The `list_services` tool runs for lab_tests and the answer names the riverside site.*
9. Can you renew my prescription for me?
   *The `clinical_boundaries` skill is read and the assistant sends you to the patient portal.*
