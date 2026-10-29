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

- The checks MUST be deterministic for the same input and config. The one exception is the
  optional injection `classifier` hook (P3, off by default, never used by tests or the
  conformance suite): when configured it MAY call a model through the model layer, and its
  score is reported separately as `classifier_score`.
- Checks run in configured order; a `block` stops later checks.
- Redaction replaces each finding with a typed placeholder (`[EMAIL]`, `[PHONE]`, `[CARD]`,
  `[ID]`, `[NAME]`). The placeholder kind, not the value, is what reaches events. Input
  redaction MAY number the placeholder (`[EMAIL_1]`, `[EMAIL_2]`) so the reply can restore it;
  the same value gets the same placeholder within a turn (P3).
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

## P3 additions (normative unless marked)

### Input stage checks

| Check | What it does | Action |
|---|---|---|
| `control` | Strips control characters (except tab, newline, carriage return) and bidirectional overrides | `redact` when anything was stripped |
| `size` | Input longer than `size.max_chars` | `block` |
| `pii` | Emails, phone numbers, card numbers (13 to 19 digits that pass the Luhn check), national ids (patterns per pack), and, when enabled, names after a cue such as "my name is" | `redact` (numbered placeholders) or `block` (`pii.action`) |
| `injection` | Heuristic score in 0..1 from instruction-override phrases, system prompt extraction, role-play escalation, fake chat role markup, and encoded payloads (base64, hex, percent or unicode escapes) whose decoded text is rescanned | Per mode (`injection.action`): `warn` reports and passes, `block` blocks at or above `block_threshold` |

- The placeholder map (placeholder to original value) lives in memory for one turn only. It is
  never written to an event, the trace, the session store or a checkpoint. A turn resumed
  after a restart has no map, so its reply keeps the placeholders.
- Tools receive the redacted text (the model only ever saw placeholders).
- Score combination is a noisy-or over signal categories: `score = 1 - prod(1 - weight)` for
  each category that fired at least once. Weights are implementation detail; the reference
  weights are in `harnesswalk/control/injection.py`.
- Pack overrides: `datasets/<pack>/guardrails.yaml` MAY hold `pii.national_id.patterns`
  (`[{name, regex, luhn?}]`) and `pii.names`, merged over `config/harness.yaml`.

### Output stage checks

| Check | What it does | Action |
|---|---|---|
| `schema` | When a structured reply was requested (`output_schema` on the turn), the answer must be JSON valid against it | `block` with a safe message |
| `content` | Literal blocklist | `block` |
| `length` | Longer than `max_chars` | `repair` (truncate) |
| `citations` | When knowledge chunks were in context: counts `[n]` markers, invalid markers (no such chunk), and uncited claims (sentences of six or more words with no marker) | `repair` once, or `block` (`on_missing: block`, or `block_public: true` in public mode) |
| `pii` | PII in the answer that is not one of this turn's input placeholders (a leak, for example from a tool result) | `repair` (typed, non-reversible placeholder) or `block` (`pii.action`) |

After the checks pass, input placeholders are restored in the reply when the mode is in
`input_guardrails.pii.restore_modes` (default `[dev]`). The restored reply is what
`message_delta` and `turn_end.summary.reply` carry; the session store keeps the redacted text.

### Summaries

- `input_guardrails` exit: `action`, `checks`, `redactions` (counts by kind), `injection_score`,
  `injection_signals` (counts by category), `warnings` (for example `["injection"]`),
  `classifier_score` when the hook ran, `reason` on a block.
- `output_guardrails` exit: `action`, `checks`, `citations`
  (`required`, `present`, `invalid`, `uncited_claims`), `schema` (`valid`, `invalid` or `none`),
  `leaks` (counts by kind), `restored` (placeholders put back), `reasons`.

### Configuration keys added

| Key | Default | Meaning |
|---|---|---|
| `input_guardrails.pii.restore_modes` | `[dev]` | Modes whose reply gets the original values back |
| `input_guardrails.pii.national_id.patterns` | US SSN, Canadian SIN (Luhn) | `[{name, regex, luhn}]` |
| `input_guardrails.pii.names.enabled` | `false` | Cue-based name detection |
| `input_guardrails.injection.action` | `{dev: warn, private: block, public: block}` | Per mode |
| `input_guardrails.injection.classifier.enabled` | `false` | The model hook (router step `classify`) |
| `output_guardrails.citations.block_public` | `false` | Block uncited answers in public mode |
| `output_guardrails.pii.action` | `repair` | `repair` or `block` |
