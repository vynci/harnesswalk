---
name: capital_call_questions
description: "Use when an investor asks about a capital call: amount, due date, purpose, wire cut-off or what happens if payment is late."
---
# Capital call questions

1. Find the fund id in the question (HCP-01 to HCP-04). If the investor names the fund, map it to its id.
2. Call `capital_calls` with that fund id to get the latest call number, notice date, due date, percent of commitment and purpose.
3. For wire cut-off, late payment interest or the notice wording, read the matching capital call notice and the default and late payment policy from the knowledge passages and cite them.
4. If an amount in money is requested, say that the call is a percent of the investor's commitment and use `calculator` only when they give their commitment.
5. Answer with the call number, percent, notice date and due date, and cite the notice as [1].
6. Never advise whether to pay, delay or fund the call another way; send that to the investor's own advisers.
