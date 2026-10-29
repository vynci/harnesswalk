# Contract: Guardrail (input and output)

Nodes: `input_guardrails` and `output_guardrails` (kind `code`, both can be disabled in dev and
private modes).

## Purpose

Deterministic checks at the two edges of the model. Input guardrails run before the model sees
anything (size, PII, injection). Output guardrails run before the answer leaves (schema, content,
citations). Both are code, not prompt instructions, so they cannot be argued with.

## Methods

```
interface Guardrail {
  stage: "input" | "output"
  checks: [Check]                                // run in order
  run(ctx: TurnContext, subject: GuardrailSubject) -> GuardrailResult
}

interface Check {
  id: string                                     // "size", "pii", "injection", "schema", "content", "citations"
  apply(ctx: TurnContext, subject: GuardrailSubject) -> CheckResult
}

record GuardrailSubject {
  text: string
  output_schema?: JSONSchema                     // output stage, when structured output was requested
  context_slices?: [SliceRef]                    // output stage: which slices were in context
  citations?: [Citation]                         // output stage
}

record CheckResult {
  check: string
  action: "pass" | "redact" | "repair" | "block"
  findings: {string: int}                        // counts only, for example {"email": 2}
  replacement?: string                           // redacted or repaired text
  reason?: string                                // short, safe to show
}

record GuardrailResult {
  action: "pass" | "redact" | "repair" | "block" // the strongest action of any check
  text: string                                   // the text to pass on (redacted or repaired)
  results: [CheckResult]
  score?: number                                 // injection score, 0..1
}
```

Action strength, weakest to strongest: `pass`, `redact` (input) or `repair` (output), `block`.
`redact` is input only; `repair` is output only.

## Inputs and outputs

- Input stage: in `Message.content` from the gateway; out the possibly redacted message to the
  context builder, or a block that sends the turn straight to the gateway reply.
- Output stage: in the loop's final answer and the context slice list; out the answer to the
  gateway, repaired or replaced with a safe refusal.

## Invariants

- MUST be deterministic for the same input and config. MUST NOT call a model in v1.
- Checks run in configured order; a `block` stops later checks.
- Redaction replaces each finding with a typed placeholder (`[EMAIL]`, `[PHONE]`, `[CARD]`,
  `[ID]`). The placeholder kind, not the value, is what reaches events.
- Summaries MUST contain only counts, check ids, actions and short reasons, never the matched text.
- Output stage, citations: when `context_slices` contains `knowledge` chunks and
  `output_guardrails.citations.required` is true, an answer with no citation is `repair`ed once
  (a notice is appended and the citation list is filled from the slices used) or `block`ed when
  `output_guardrails.citations.on_missing` is `block`.
- Output stage, schema: when an output schema was requested, an invalid answer is `block`ed with
  a safe message; the loop is not re-entered in v1.
- When disabled, the node still enters and exits with status `skipped` and the text is passed
  through untouched.

## Failure behaviour

| Condition | Result |
|---|---|
| A check raises | Fail closed: `block` with reason `guardrail_error`; the span closes `done` with that summary, and the exception is recorded in the trace |
| Input block | Turn goes to gateway reply with a refusal; `turn_end.summary.exit` is `blocked` |
| Output block | The reply is a fixed safe message; `turn_end.summary.exit` is `blocked` |

## Events

`node_enter` / `node_exit` on `input_guardrails` or `output_guardrails`. Edges:
`input_guardrails_to_context_builder`, `loop_to_output_guardrails`, `output_guardrails_to_gateway`.

## Configuration keys (`config/harness.yaml`)

| Key | Default | Meaning |
|---|---|---|
| `input_guardrails.enabled` | `true` | Ignored (forced true) in public mode |
| `input_guardrails.checks` | `[size, pii, injection]` | Order matters |
| `input_guardrails.size.max_chars` | `8000` | |
| `input_guardrails.pii.kinds` | `[email, phone, card, national_id]` | |
| `input_guardrails.pii.action` | `redact` | `redact` or `block` |
| `input_guardrails.injection.block_threshold` | `0.8` | Heuristic score at which input is blocked |
| `output_guardrails.enabled` | `true` | Ignored (forced true) in public mode |
| `output_guardrails.checks` | `[schema, content, citations]` | |
| `output_guardrails.citations.required` | `true` | When knowledge chunks were in context |
| `output_guardrails.citations.on_missing` | `repair` | `repair` or `block` |
| `output_guardrails.content.blocklist` | `[]` | Literal patterns that block an answer |
