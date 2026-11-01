---
node: tools
try:
  pack: _starter
  fixture: tool_call
  prompt: "Where is order A-1001?"
---

# Tools

> Typed tool calls, resolved, checked by policy and executed in a sandbox.

## What it does

A tool is a function the model may ask the harness to run: look up an order, cancel it, read a
skill, search a document. The model never runs anything itself. It writes a tool call (a name
and a JSON object of arguments) into its response, and the loop hands that call to this node.
The Tools node takes every call down the same path, in the same order, with no shortcuts:
resolve the name in the registry, validate the arguments against the tool's JSON Schema, ask
policy, record the decision in the audit log when policy says so, wait for a human when policy
says so, execute in the sandbox with a timeout, and record what ran.

Whatever happens on that path, the loop gets back an observation: `ok` with the tool's output,
`error` with a short reason, or `denied` with the policy rule that refused it. A failed or
refused call is information for the model, not a crash, so the model can apologise, try a
different order id, or answer without the tool. On the diagram the node shows the two sub-steps
you care most about, policy check then execute, so you can see a call that was stopped before it
did anything.

## What breaks without it

The node cannot be switched off, but you can send it calls the model should never have made and
watch it refuse each one before anything runs.

Pack `_starter`, fixture `loop_max_steps` (a conformance fixture written for a logistics pack),
prompt:

```sh
uv run harnesswalk turn "Check shipments S-1, S-2 and S-3 one at a time." \
  --provider fake --fixture loop_max_steps --set loop.max_steps=3
```

The scripted model calls `track_shipment` three times. The starter pack has no such tool, so
each `tools` span reaches `registry` and stops there: `outcome=error executed=False`, with no
`policy` and no `sandbox` span under it. The observation the model gets back is
`Unknown tool 'track_shipment'.` Then read the reply: "I checked three shipments. S-1 and S-2 are
in transit and S-3 is delayed in Halifax." Nothing checked anything. The Tools node kept a
nonexistent function from running, and it could not stop the model from inventing a result
afterwards. That is a job for output checks and evals (chapters 11 and 14), and it is why the
receipt of every call (`executed`, `outcome`) matters.

Compare the normal path with fixture `tool_call` and `Where is order A-1001?`: the span tree is
`tools`, then `registry`, `policy` (`decision=allow rule=dev.read.allow`) and `sandbox`
(`level=subprocess network=blocked`), and the reply quotes the real row.

## The code walk

The docstring of [`tools.py`](../harnesswalk/execution/tools.py#Tools) lists the invoke path
step by step. It is the most important ten lines in the execution plane; read it before the
code.

[`Tools.invoke`](../harnesswalk/execution/tools.py#Tools.invoke) opens the node's span and
calls `_invoke`. Its one `except` is worth noticing: if the audit store is unavailable the call
fails closed, with outcome `denied`, rather than running an action nobody could record.

[`Tools._invoke`](../harnesswalk/execution/tools.py#Tools._invoke) is the path itself. First
the registry lookup (an unknown name is an error observation). Then a check that this mode
offers the tool at all, because a model can name a tool it was never shown. Then
`jsonschema.validate` on the arguments, so `{"order_id": "A-1"}` never reaches a function that
expects `A-1001`. Then `policy.check` on every single call, with no cache, then audit, then
either a denial (the sandbox span is still drawn, as skipped), an approval request, or
`_execute`.

[`Tools._execute`](../harnesswalk/execution/tools.py#Tools._execute) runs the call in the
sandbox. For a write it first runs the tool named by `audit.before` (for `cancel_order`, that
is `lookup_order`) so the audit record keeps the state before and after. Every non-read call
that ran gets a `tool_executed` audit record. Long outputs are cut at `max_observation_chars`
so one tool cannot flood the context.

[`ToolSpec`](../harnesswalk/execution/registry.py#ToolSpec) is one tool: name, description,
JSON Schema parameters, action class, timeout, and whether it is pure. [`ToolRegistry.list`](../harnesswalk/execution/registry.py#ToolRegistry.list)
is what each mode may offer. The starter tools themselves are rows in
[`datasets/_starter/tools.yaml`](../datasets/_starter/tools.yaml): pure lookups in a table, so
the demos are deterministic.

## Try this

1. **Send bad arguments.** Use fixture `golden_unknown_order` with "Where is order A-9999?".
   Expected: the call is valid by schema, runs, and returns `{"error":"not_found"}`; the reply
   asks you to check the id. Then copy the fixture to `bad_args.jsonl` in the same folder,
   change the id to `A-99` and delete the second response's `expect`. Expected:
   `outcome=error executed=False` and no policy span, because `A-99` fails the schema pattern.
2. **Watch the before and after.** Run fixture `cancel_order` with "Please cancel order
   A-1002." and `--approval approve`. Expected: a `tool_executed` audit record whose `before`
   has `status: processing` and whose `after` has `status: cancelled` and `refund: 42.00`.
3. **Add a tool.** Add a `lookup_store` read tool to `datasets/_starter/tools.yaml` (copy
   `lookup_order`, a table of two stores) and a fixture that calls it. Expected, first run:
   `decision=deny rule=agent.allowlist`, because a new tool is not on `agent.allow_tools` in
   `config/policy.yaml`. Add it there and run again. Expected: `dev.read.allow`, and the
   sandbox runs it. Revert both files afterwards.

## Check yourself

### 1. The model calls a tool name that is not in the registry. What happens?

- [ ] The sandbox tries to import it and fails.
- [x] The call stops at the registry with an error observation, and nothing executes.
- [ ] Policy denies it with a rule.

Answer: resolution comes first, so an unknown name never reaches policy or the sandbox; the
model gets `Unknown tool ...` as its observation.

### 2. Why is policy checked on every call, with no cache?

- [ ] Because policy is cheap.
- [x] Because the same tool can be allowed for one call and not for the next, for example when a budget runs out.
- [ ] Because the audit log needs one record per call.

Answer: decisions depend on arguments, mode and budgets that change during a turn, so a cached
"allow" could let a call through that should be refused.

### 3. The tools refused every call, yet the reply describes the shipments. What does that show?

- [x] Tools control what runs, not what the model says; checking the answer is another layer's job.
- [ ] The sandbox ran the calls anyway.
- [ ] The registry returned fake data.

Answer: the reply was invented after three error observations. Output checks and evals exist
for exactly this.

## Further reading

- [JSON Schema 2020-12, Validation vocabulary](https://json-schema.org/draft/2020-12/json-schema-validation)
- [Model Context Protocol specification, Tools](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)
- [`spec/contracts/tool_registry.md`](../spec/contracts/tool_registry.md)
