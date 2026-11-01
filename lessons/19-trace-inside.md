---
node: trace
covers: [cost]
try:
  pack: _starter
  fixture: read_skill
  prompt: "How long do I have to return order A-1003?"
---

# Inside the trace: cost

> The receipt of a turn: tokens, time and dollars per node and per model call, and the budgets that read it.

## What it does

The Expert view opens Trace into one child, Cost. It is small on purpose, because the numbers it
reports are already on the events: every model call's `node_exit` carries a `cost` object (input
and output tokens, and dollars priced from `config/models.yaml`), and every span carries its
duration. The receipt is the sum of those node exits and nothing else, never `turn_end`, so a
number can never be counted twice.

### Cost

At the end of each turn, the Cost span reports the turn's total tokens and dollars and the
dollars by node. The full receipt (`GET /v1/trace/turns/{id}/receipt`, and the Trace node's Turns
tab) adds the time and cost per node and one line per model call: step, provider, model, tokens,
dollars, milliseconds, whether it was a fallback. The same numbers drive three controls. The loop
stops on its dollar and token budgets. The router switches to its `cheap` rule once a turn has
spent three quarters of its dollar budget (`router.cost.tight_fraction`). And the tenant's
budgets in `policy.budgets` (per turn and per hour, from an hourly ledger) narrow the loop's own
limits, with `budget_source` saying which limit applied.

A model whose alias has no prices in config is reported with `price_known: false` and costs zero
as far as the harness knows. The fake and local models are priced at zero, which is why the other
chapters show `$0.000000`.

## What breaks without it

Pack `_starter`, fixture `read_skill`, prompt:

```text
How long do I have to return order A-1003?
```

On the fake provider's zero prices the turn takes three steps, answers, and costs nothing, and the
dollar budget (`loop.budget_usd: 0.05`) can never fire. Give the fake model real prices for one
run:

```sh
uv run harnesswalk turn "How long do I have to return order A-1003?" --provider fake \
  --fixture read_skill --set models.models.fake-small.price_in_per_mtok=100 \
  --set models.models.fake-small.price_out_per_mtok=500
```

Now the loop stops after two steps with `exit_reason=budget_usd`, the reply is "I stopped because
this turn used its cost budget. Try a narrower question.", and the last line says `$0.082500`.
Notice that is more than the $0.05 budget: the loop checks between steps, so a turn can overshoot
by the price of one model call. A budget is a stopping rule, not a spending cap, and the receipt
is how you see the overshoot.

Without the receipt, the dollar budget, the cheap rule and the tenant budgets would all be
reading zeros, so none of them would ever act, and the first sign of a runaway turn would be the
provider's invoice.

## The code walk

[`run_cost_node`](../harnesswalk/ops/cost.py#run_cost_node) is the whole Cost node: it puts the
receipt's totals and `by_node` on its summary and deliberately sets no `cost` of its own. The
module docstring says why: a span that carried the total as its cost would be summed again.

[`receipt_of`](../harnesswalk/ops/trace.py#receipt_of) sums `cost` from node exits and errors
only, by node and by model, with latency per node. [`full_receipt`](../harnesswalk/ops/trace.py#full_receipt)
adds the per-call lines. Both take a list of events, so the same function prices a live turn, a
stored one and an eval case.

[`price`](../harnesswalk/execution/model_layer.py#price) is where dollars come from, and
[`ModelLayer.complete`](../harnesswalk/execution/model_layer.py#ModelLayer.complete) sets the
call's cost on its span (`span.cost`) so it lands on the `node_exit`.

[`Loop._exit_before_step`](../harnesswalk/execution/loop.py#Loop._exit_before_step) reads the
running usage before each step; [`Loop._model`](../harnesswalk/execution/loop.py#Loop._model)
sets `cost: low` on the request once spending passes the tight fraction, which is what the
router's `cheap` rule matches.

[`TurnBudget.loop_limit`](../harnesswalk/control/budgets.py#TurnBudget.loop_limit) narrows the
loop's token and dollar budgets by the tenant's per-turn limit and what is left of its hour, and
[`Budgets.record`](../harnesswalk/control/budgets.py#Budgets.record) writes the turn's spend to
the hourly ledger when the turn ends.

## Try this

1. **Feel the pressure.** Run the prompt with `price_in_per_mtok=200` and
   `price_out_per_mtok=1`. Expected: step 1's router says `rule=default`, step 2's says
   `rule=cheap`, because step 1 alone spent more than three quarters of the budget; the turn then
   stops on `budget_usd` at `$0.125040`.
2. **A tenant budget.** Add `--set policy.budgets.default.usd_per_turn=0.01` to the priced run.
   Expected: the loop's enter summary shows `budget_usd: 0.01` and
   `budget_source: {usd: policy.budgets.usd_per_turn}`, and it stops after one step.
3. **Read the receipt.** Run any priced turn against a server and fetch
   `GET /v1/trace/turns/<turn_id>/receipt`. Expected: `total` equals the turn's `turn_end.cost`,
   `nodes.model_layer.usd` holds all of it, and `calls` has one line per model call with its step
   and milliseconds.

## Check yourself

### 1. Why is the receipt summed from node exits and never from `turn_end`?

- [x] Because `turn_end` repeats the total, so adding it would count everything twice.
- [ ] Because `turn_end` has no cost field.
- [ ] Because node exits are more precise.

Answer: the rule in `spec/events.md` is that only node exits carry spend; the Cost span itself
reports totals in its summary, not as a cost, for the same reason.

### 2. The dollar budget is $0.05 and the turn spent $0.0825. Is that a bug?

- [ ] Yes, the loop should refuse any call that might go over.
- [x] No: the budget is checked between steps, so the last step can overshoot it.
- [ ] Yes, the prices are applied twice.

Answer: the loop cannot know a call's price before it is made. It stops at the first check after
the budget is spent.

### 3. What makes the router choose its `cheap` rule?

- [ ] The tenant asks for a cheaper answer.
- [ ] The token budget is nearly spent.
- [x] The turn has spent at least `tight_fraction` of its dollar budget, so the request carries `cost: low`.

Answer: the loop marks the request `cost: low` and the rule `when: { cost: low }` matches it,
with `max_price_out_per_mtok` filtering expensive aliases.

## Further reading

- [OpenTelemetry semantic conventions, Generative AI metrics](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-metrics/)
- [Anthropic documentation, Pricing](https://docs.anthropic.com/en/docs/about-claude/pricing)
- [`spec/contracts/tracer.md`](../spec/contracts/tracer.md) and [`spec/contracts/policy.md`](../spec/contracts/policy.md)
