---
node: loop
try:
  fixture: read_skill
  prompt: "How long do I have to return order A-1003?"
---

# Loop

> Plan, act, observe, adjust, and every way the loop is made to stop.

## What it does

The loop is the part most people mean when they say "agent". It sends the assembled context to
the model and reads what comes back. If the model answers in text, the turn is done. If it asks
for tools instead, the loop runs each call through the Tools node (where policy, approval and
the sandbox live), appends the results to the conversation as tool messages, and asks the model
again. Each round trip is a step. A question that needs a skill and an order lookup takes three
steps: read the skill, look the order up, answer.

The other half of the loop is stopping. A model can ask for tools forever, repeat the same call
without progress, or burn through a budget, so before every step the loop checks its exits: a
cancel from the user, `max_steps`, the token budget, the dollar budget and a wall-clock
timeout. Before every tool call it checks whether that exact call has already run
`stuck_repeats` times. After `max_steps` or `stuck` it makes one last model call with tools
turned off, so the user still gets an answer; after a budget or a timeout it stops with a fixed
notice. After every step it writes a checkpoint to the session store, so a turn paused for
approval, or cut off by a restart, can carry on.

## What breaks without it

The loop is always on; what you can take away are its limits, and that shows what they are for.

Pack `_starter`, fixture `read_skill`, prompt:

```text
How long do I have to return order A-1003?
```

Run it normally and the loop reports `exit_reason=answer steps=3 tool_calls=2`: the model reads
the `returns` skill, looks up an order, and answers. On the diagram the edges between Loop,
Model layer and Tools light three times. Now allow a single step:

```sh
uv run harnesswalk turn "How long do I have to return order A-1003?" \
  --provider fake --fixture read_skill --set loop.max_steps=1
```

The loop stops with `exit_reason=max_steps steps=1`, makes its final call with
`tools_enabled=False`, and because the scripted model still wants a tool, the reply is the
notice "I stopped after the maximum number of steps without reaching an answer." The turn's exit
is `limit`. Try `--set loop.budget_tokens=500` instead and it stops after two steps with
`exit_reason=budget_tokens` and the token notice. In the web app the same exits show on the
turn status bar under "{n}% budget".

Those notices are the loop doing its job. Without the exits, the first model that kept calling
tools would hold a request open until something outside the harness killed it, with nothing in
the trace to say why.

## The code walk

The module docstring of [`loop.py`](../harnesswalk/execution/loop.py#Loop) is the algorithm in
pseudocode. Read it first; the code follows it line for line.

[`Limits`](../harnesswalk/execution/loop.py#Limits) holds the exits, read from `loop` in
`config/harness.yaml`. [`Limits.from_config`](../harnesswalk/execution/loop.py#Limits.from_config)
then narrows the token and dollar budgets by the tenant's own (`policy.budgets`), so the
smallest wins and `budget_source` says where it came from.

[`Loop._drive`](../harnesswalk/execution/loop.py#Loop._drive) is the `while` loop. Each pass
checks the exits in [`Loop._exit_before_step`](../harnesswalk/execution/loop.py#Loop._exit_before_step)
(the cancel is checked first so it wins over every limit), labels the step (`first`,
`after_tool`, `next`) for the router, calls the model, and either stops on text or runs the
calls. After the loop come the final call with `[]` as the tool list and the notices.

[`Loop._run_calls`](../harnesswalk/execution/loop.py#Loop._run_calls) runs one step's calls in
order. The `seen` counter keyed by [`call_key`](../harnesswalk/execution/loop.py#call_key) (the
name plus the arguments as sorted JSON) is the stuck check. Notice that every call gets a tool
message even when it does not run (`_not_run`): providers reject a conversation where a tool
call has no result. Also notice `state.risk` turning `high` once any non-read tool is called,
which the router reads on the next model call.

[`Loop._checkpoint`](../harnesswalk/execution/loop.py#Loop._checkpoint) saves the messages,
usage and memory proposals after each step, plus the pending call when a tool is about to pause
for approval. [`Loop.resume`](../harnesswalk/execution/loop.py#Loop.resume) is the other end:
it rebuilds the state from a checkpoint, appends the observation that arrived while the turn
was paused, and drives on.

## Try this

1. **Count the steps.** Run the prompt above normally with `--json` and count the `model_layer`
   spans. Expected: three, with `step=1`, `2` and `3` on their summaries, and a `router` span
   inside each one.
2. **Skip the final call.** Run with `--set loop.max_steps=2 --set loop.final_call_on_exit=false`.
   Expected: `exit_reason=max_steps steps=2 tool_calls=2`, no third `model_layer` span, and the
   max-steps notice as the reply.
3. **Make it stuck.** Copy `spec/conformance/fixtures/loop_max_steps.yaml` to
   `datasets/_starter/fixtures/fake/stuck_demo.yaml`, set `fixture: stuck_demo`, and change all
   three calls to `lookup_order` with `{order_id: A-1001}`. Run
   `uv run harnesswalk turn "Where is A-1001?" --provider fake --fixture stuck_demo --set loop.stuck_repeats=2`.
   Expected: two lookups run, the third is not run, the loop exits `stuck` after three steps,
   and a fourth model call answers with `tools_enabled=False`. Delete the file afterwards.

## Check yourself

### 1. After `max_steps`, why does the loop call the model once more with no tools?

- [ ] To retry the last tool call.
- [x] So the user still gets an answer from what was gathered, instead of a bare error.
- [ ] To summarise the conversation for the session store.

Answer: with tools turned off the model can only answer in text, so the turn ends with the best
answer it can give from the observations so far.

### 2. Why does a tool call that was not run still get a tool message?

- [x] Because every tool call needs a result, or the next model request is invalid.
- [ ] So the audit log has a record of it.
- [ ] Because the sandbox runs it later.

Answer: providers expect a result for every call in an assistant message, so the loop answers
skipped calls with a short "Not run" observation.

### 3. Why does the loop write a checkpoint after every step?

- [ ] To speed up the next model call.
- [ ] To compute the cost receipt.
- [x] So a turn paused for approval or cut off by a restart can resume where it was.

Answer: the checkpoint holds the messages, usage and pending call, and `resume` rebuilds the
loop from it.

## Further reading

- [Yao et al., ReAct: Synergizing Reasoning and Acting in Language Models (2022)](https://arxiv.org/abs/2210.03629)
- [Anthropic documentation, Tool use with Claude](https://docs.anthropic.com/en/docs/build-with-claude/tool-use/overview)
- [`spec/contracts/loop.md`](../spec/contracts/loop.md)
