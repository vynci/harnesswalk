# Contract: Loop

Node: `loop` (execution plane, kind `code`).

## Purpose

Plan, act, observe, adjust. The loop calls the model, executes the tools it asks for through
`tools`, feeds observations back, and stops on an answer or an exit condition. It checkpoints
after every step so a paused or crashed turn can resume.

## Methods

```
interface Loop {
  run(ctx: TurnContext, context: AssembledContext, tools: [ToolSpec], limits: Limits) -> async LoopResult
  resume(ctx: TurnContext, checkpoint_id: string) -> async LoopResult
  interrupt(turn_id: string) -> void
}

record Limits {
  max_steps: int
  budget_tokens: int
  budget_usd: number
  timeout_s: number
  stuck_repeats: int
  final_call_on_exit: bool
}

record Checkpoint {
  checkpoint_id: string
  turn_id: string
  step: int
  messages: [Message]
  pending_call?: ToolCall                        // set while waiting for approval
  usage: Usage
}

record LoopResult {
  answer: FinalAnswer
  steps: int
  tool_calls: int
  exit_reason: "answer" | "max_steps" | "budget_tokens" | "budget_usd" | "timeout" | "stuck" | "interrupted"
  usage: Usage
  proposals: [MemoryProposal]
}
```

## Algorithm (normative outline)

```
step = 0
loop:
  if step + 1 > limits.max_steps: exit("max_steps")
  if usage.tokens >= budget_tokens: exit("budget_tokens")
  if usage.usd >= budget_usd: exit("budget_usd")
  if now >= deadline: exit("timeout")
  step += 1
  response = model_layer.complete(ctx, request(messages, tools, step))
  append(response as assistant message)
  if response.tool_calls is empty: exit("answer", response.text)
  for call in response.tool_calls (in order):
    if same (name, arguments) seen stuck_repeats times this turn: exit("stuck")
    observation = tools.invoke(ctx, call)        // policy, approval, sandbox happen inside
    append(observation as tool message)
  checkpoint(step)

exit(reason, text?):
  if reason == "answer": answer = text
  elif reason in ("max_steps", "stuck") and final_call_on_exit:
    answer = model_layer.complete(ctx, request(messages, tools = [], step + 1)).text
  else:
    answer = fixed notice for reason
```

## Inputs and outputs

- In: the `AssembledContext` from the context builder, the tool schemas visible to this tenant and mode, and `Limits`.
- Out: a `FinalAnswer` to `output_guardrails`; `Checkpoint`s to the session store; `MemoryProposal`s for `memory_writer`.

## Invariants

- A step is one model call plus the tool calls it requested. The final tools-off call after
  `max_steps` or `stuck` is not counted as a step.
- MUST checkpoint to the `session` store after every step and before any approval pause.
- MUST NOT call the model again after `budget_tokens`, `budget_usd`, `timeout` or `interrupted`.
- MUST execute tool calls only through `tools`, never directly.
- A denied or failed tool call becomes an observation; it does not end the loop by itself.
- Breakpoints: in step mode the loop's own span pauses like every other node (`node_paused`
  reason `breakpoint`), and nested nodes pause on their own enter.
- Memory proposals are collected, not written. They are handed to `memory_writer` after the reply.

## Failure behaviour

| Condition | Result |
|---|---|
| Model layer error (after fallbacks) | Loop span closes with `error` `provider_error`; the turn replies with an error notice and `turn_end.status` `error` |
| Tool error or timeout | Observation with outcome `error` or `timeout`; loop continues |
| Process crash | `resume` from the last checkpoint replays no completed step and re-enters the loop at `step + 1` |

## Events

`node_enter` (summary `max_steps`, `budget_tokens`, `budget_usd`) and `node_exit` (summary
`steps`, `exit_reason`, `tool_calls`). Edges: `context_builder_to_loop`, `loop_to_model_layer`,
`model_layer_to_loop`, `loop_to_tools`, `tools_to_loop`, `loop_to_output_guardrails`,
`loop_to_memory`, `loop_to_memory_writer`, `loop_to_trace`.

## Configuration keys (`config/harness.yaml`)

| Key | Default | Meaning |
|---|---|---|
| `loop.max_steps` | `8` | |
| `loop.budget_tokens` | `16000` | Per turn, input plus output |
| `loop.budget_usd` | `0.05` | Per turn; `0` disables the dollar check for free providers |
| `loop.timeout_s` | `120` | Wall clock, excluding time paused for approval |
| `loop.stuck_repeats` | `3` | Same tool with the same arguments |
| `loop.final_call_on_exit` | `true` | Tools-off final call after `max_steps` or `stuck` |
| `loop.notices.<reason>` | built in | Fixed reply text per exit reason |
