---
node: output_guardrails
requires: [output_guardrails]
try:
  pack: _starter
  disable: [output_guardrails]
  fixture: golden_pii_leak
  prompt: "Who handles returns?"
also:
  - pack: healthcare
    disable: [output_guardrails]
    fixture: leak_health_number
    prompt: "Is the north site open on Saturdays?"
    note: "Another patient's health number in the scripted reply reaches the user; with the node on it is repaired to [ID]."
---

# Output guardrails

> Schema, content and citation checks on the answer before it leaves.

## What it does

The loop's final answer is a draft until this node has read it. Output guardrails run a short
list of checks in order. `schema`: when the turn asked for a structured reply, the answer must be
JSON that validates against the given JSON Schema. `content`: literal patterns from a blocklist
refuse the answer. `length`: an answer over the limit is cut. `citations`: when knowledge passages
were in context and the answer makes a claim drawn from them, it must cite them as `[n]`, and every
`[n]` must name a passage the model was really shown. `pii`: personal data in the answer that is
not one of this turn's own placeholders is a leak, for example an email address that came back in
a tool result, and it is redacted or refused.

Each check can pass, repair or block. A repair changes the text (a redacted email, a truncated
tail, a notice that no sources were cited) and the turn answers. A block replaces the answer with
a short safe message and the turn's exit becomes `blocked`. Last, in the modes listed in
`input_guardrails.pii.restore_modes` (dev by default), the node puts this turn's own placeholders
back, so you see your own email address in the reply even though the model only ever saw
`[EMAIL_1]`. Like the input guardrails, these are code, not instructions to the model.

## What breaks without it

Pack `_starter`, fixture `golden_pii_leak`, prompt:

```text
Who handles returns?
```

The scripted model answers with a staff member's address: "Our returns team lead is Jane,
reachable at jane.doe@example.org, and she replies within one business day." With the node on,
Output guardrails reports `action=repair` with `leaks: {email: 1}` and `reasons: [citations, pii]`,
and the reply you see is "Our returns team lead is Jane, reachable at [EMAIL], and she replies
within one business day." followed by "(No sources were cited for this answer.)", because the
sentence draws on the returns policy without citing it.

Open the Output guardrails node, switch on "Disable on the next turn", and send it again. The
node is grey and `skipped`, and the reply goes out exactly as the model wrote it, with
`jane.doe@example.org` in it and no notice. Nothing else in the harness looks at the answer on the
way out: the gateway streams whatever this node returns. One poisoned tool result or one bad
generation and someone else's personal data reaches the user and the session store, where every
later turn sends it to the model again. (The memory writer's own scrub still keeps it out of the
episode, a second line of defence.)

To see a block rather than a repair, run the same prompt from the CLI with
`--set output_guardrails.pii.action=block`. The exit is `blocked` and the reply is "I cannot
share that reply because it contains personal data."

On a domain pack: the second Try button runs this on `healthcare`.

## The code walk

[`OutputGuardrails.run`](../harnesswalk/execution/output_guardrails.py#OutputGuardrails.run) is
the span. The first branch is the switch: disabled in dev or private, it only restores the
turn's placeholders and returns the text unchecked. The `except` makes any crash inside a check a
block with `guardrail_error`, so a bug fails closed. After the checks, restoration runs only when
the action is not a block.

[`OutputGuardrails.check`](../harnesswalk/execution/output_guardrails.py#OutputGuardrails.check)
holds the five checks in one loop over `output_guardrails.checks`. Notice which ones `return`
early (schema, content, a blocking citation or PII rule) and which only change `text` and raise
`action` to `repair`. Notice also that the PII check never flags this turn's placeholders: they are
the user's own data, already handled on the way in.

[`citation_report`](../harnesswalk/execution/output_guardrails.py#citation_report) counts the
`[n]` markers, the markers that name no passage (`invalid`), the sentences that draw on a passage
(`drawn_claims`) and the drawn sentences without a marker (`uncited_claims`).
[`draws_from`](../harnesswalk/execution/output_guardrails.py#draws_from) decides "draws on" by
shared content words, at least `CLAIM_WORDS` of them. It is a cheap, deterministic proxy for
grounding; it is not a fact checker, and the chapter on eval shows where a judge model comes in.

[`OutputGuardrails._restore`](../harnesswalk/execution/output_guardrails.py#OutputGuardrails._restore)
puts the vault's values back. The session store does not get them: the gateway re-redacts the
reply in [`Gateway._reredact`](../harnesswalk/control/gateway.py#Gateway._reredact) before it
saves the turn, so only the user's screen ever shows the restored text.

[`Gateway._check_and_reply`](../harnesswalk/control/gateway.py#Gateway._check_and_reply) is the
caller: every turn that reaches an answer, including limit notices, goes through this node, and a
block turns the exit into `blocked`.

## Try this

1. **Ask for JSON.** In the web app's dev Options, set an output schema for
   `{"type": "object", "required": ["order_id", "status"], "properties": {"order_id": {"type": "string"}, "status": {"enum": ["processing", "shipped", "delivered", "cancelled"]}}}`
   and send "Give me the status of order A-1001 as JSON." with fixture `golden_structured`.
   Expected: the JSON reply passes with `schema: valid`. Remove `shipped` from the enum and send
   it again. Expected: exit `blocked` and "I could not produce a reply in the required format."
2. **Block a word.** Run the PII prompt with `--set 'output_guardrails.content.blocklist=[Jane]'`.
   Expected: `action=block` and the reply "I cannot share that reply."
3. **Break a citation.** Send "Can I return order A-1003, the Trailhead backpack?" with fixture
   `trailhead_return` and then with `--set context.slices.knowledge=150`. Expected: the first
   reports `citations.invalid: 0`, the second `invalid: 2`, because the reply's `[6]` and `[5]` now
   name passages the model was never shown. Decide whether an invalid marker should repair, block
   or pass, then find the line in `check` you would change.

## Check yourself

### 1. The reply contains an email address that a tool returned. What does the node do by default?

- [ ] Passes it, because it came from a trusted tool.
- [x] Redacts it with a placeholder and marks the result `repair`.
- [ ] Blocks the whole turn.

Answer: `pii.action: repair` is the default, so the address becomes `[EMAIL]` and the turn
answers; `block` refuses the reply instead.

### 2. Why does the user see their own email in the reply when the model never saw it?

- [x] The node restores this turn's placeholders from the vault in the modes that allow it.
- [ ] The model guesses it from the context.
- [ ] The session store keeps the original.

Answer: input guardrails replaced it with `[EMAIL_1]`; the vault maps it back, and only the reply
on screen gets the value. The session store keeps the placeholder.

### 3. What happens if a check inside the node raises an exception?

- [ ] The answer goes out unchecked.
- [ ] The turn retries the loop.
- [x] The answer is blocked with a safe message, reason `guardrail_error`.

Answer: the node fails closed. A broken check never becomes a silent pass.

## Further reading

- [OWASP Top 10 for LLM Applications (2025), LLM05: Improper Output Handling](https://genai.owasp.org/llmrisk/llm052025-improper-output-handling/)
- [JSON Schema, Getting started](https://json-schema.org/learn/getting-started-step-by-step)
- [`spec/contracts/guardrail.md`](../spec/contracts/guardrail.md)
