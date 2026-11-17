---
node: policy
requires: [policy]
try:
  pack: _starter
  disable: [policy]
  fixture: cancel_order
  prompt: "Please cancel order A-1002."
also:
  - pack: ecommerce
    disable: [policy]
    fixture: cancel_approved_qf20430
    prompt: "Please cancel order QF-20430, I ordered the wrong size."
    note: "cancel_order runs with no approval card (rule=disabled in the audit), and the scripted reply still says an approval came through."
  - pack: device_fleet
    disable: [policy]
    fixture: power_off_approved
    prompt: "Power off Shop Pickup 2 (KW-0002), the workshop needs it fully down for a wiring job."
    note: "power_off_device runs at once with no approval card, and only the tool's own warning says it is not remotely recoverable. With policy on, a viewer's reboot is denied by rule roles before anyone is asked."
---

# Policy

> Allow, deny or needs approval, decided by rules on every single tool call.

## What it does

Policy answers one question for every tool call: may this run? The answer is one of three
words. `allow` lets the call go to the sandbox. `deny` stops it, and the model gets a short
observation naming the rule. `needs_approval` pauses the turn until a person decides. The
decision comes from rules in `config/policy.yaml`, and the main input is the tool's action class:
`read`, `write`, `destructive`, `external`, or `memory.write` for the memory writer. In the
starter pack, reads are allowed, `cancel_order` is destructive, and destructive calls need
approval in dev and private mode and are denied in public mode.

The evaluation order is fixed and short: the agent's allowlist of tools, then what the user's
roles allow, then the tenant's tool-call budget, then the rules in file order (first match wins),
and a deny when nothing matches. The decision is pure: the prompt and the tool results never
reach it, so nothing the model writes can change it. A line in the system prompt saying "never
cancel an order without asking" is advice the model may or may not follow. This node is the
control. Every decision also carries a `why`, one sentence for the inspector, and every deny,
every approval request and every allowed non-read call goes to the audit log.

## What breaks without it

Pack `_starter`, fixture `cancel_order`, prompt:

```text
Please cancel order A-1002.
```

Send it with policy on. The Policy node shows `decision=needs_approval rule=dev.destructive.approve`,
an approval card appears in the chat ("Run cancel_order?"), the Approval node waits, and nothing
has run. Deny it and the order stays as it was.

Now open the Policy node, switch on "Disable on the next turn", and send the same prompt. Policy
turns grey with `decision=allow rule=disabled`. There is no approval card. The sandbox runs
`cancel_order` straight away, and the audit log gains two records: a `policy_decision` allow
with rule `disabled` and a `tool_executed` record whose `before` is `status: processing` and
whose `after` is `status: cancelled, refund: 42.00`. The order was cancelled and refunded on
the model's say-so, with no person involved. The audit trail is still there (disabling a control
never hides what happened), but the action already ran.

The scripted reply even says "The tool result above says whether a human approved it", which
shows why the model's own text is no evidence: only the policy and audit records say what
really happened.

On a domain pack: the second Try button runs this on `ecommerce`.

## The code walk

[`Policy.check`](../harnesswalk/control/policy.py#Policy.check) is the node's span. The first
branch is the disable switch (dev and private only): allow with rule `disabled`, and still
`audit=True` for anything that is not a read. The `except` branch below it fails closed: if
deciding throws, the call is denied with rule `policy.error`. The summary carries the tool, the
action class, the decision, the rule and `why`.

[`Policy.decide_class`](../harnesswalk/control/policy.py#Policy.decide_class) is the decision
itself, and it is short enough to read in one go. Follow the order from the module docstring:
allowlist, roles, budget, rules, default deny. Notice the budget branch returns a `limit`
(`budget_tool_calls`) as well as a deny, which is how a spent budget ends the loop instead of
letting the model try again. Notice too that the rules match on modes, action classes, tool
names and roles, never on arguments or text.

[`Policy.decide_memory_write`](../harnesswalk/control/policy.py#Policy.decide_memory_write)
runs the same function for the memory writer, as a `remember` call of class `memory.write`, so a
rule written for tools applies to memory writes too.

[`Policy.check_public_mode`](../harnesswalk/control/policy.py#Policy.check_public_mode) runs at
start-up in public mode and refuses to start the harness if any rule could let a destructive or
external call through. A misconfigured public deployment fails loudly instead of quietly.

[`Budgets`](../harnesswalk/control/budgets.py#Budgets) holds the per-tenant limits and the hourly
ledger that the budget step reads (`policy.budgets` in the same file). The rules themselves
live in [`config/policy.yaml`](../config/policy.yaml), which the Policy node's Config tab edits as
a versioned artifact.

## Try this

1. **Read the why.** Send "Where is order A-1001?" with fixture `tool_call` and open Policy.
   Expected: `decision=allow rule=dev.read.allow` and a why such as "rule dev.read.allow (modes
   dev, private, public; classes read) allows read calls".
2. **Spend a budget.** Run fixture `three_lookups` with "Look up orders A-1001, A-1002 and
   A-1003." and `--set policy.budgets.default.tool_calls_per_turn=2`. Expected: two lookups
   allowed, the third `decision=deny rule=budget.tool_calls_per_turn` and audited, the loop
   exits `budget_tool_calls` with `budget_source=policy.budgets`, and the reply is the tool-call
   budget notice.
3. **Write a rule.** In the Policy node's Config tab, add
   `{ id: no.cancel, modes: [dev], tools: [cancel_order], decision: deny }` above
   `dev.destructive.approve` and send the cancel prompt. Expected: `decision=deny rule=no.cancel`,
   no approval card, an audited deny, and an observation that tells the model it was denied.
   Remove the rule afterwards.

## Check yourself

### 1. Why can no prompt talk the policy into allowing a call?

- [ ] Because the policy runs before the model.
- [x] Because the decision reads only the call's tool, class, mode, roles and budget, never the text.
- [ ] Because the system prompt forbids it.

Answer: `decide_class` is a pure function of those inputs. Prompt text and tool results never
reach it.

### 2. With policy disabled, what does the audit log record for `cancel_order`?

- [x] An allow with rule `disabled`, then `tool_executed` with the before and after state.
- [ ] Nothing, because the node was skipped.
- [ ] An approval request that nobody answered.

Answer: switching the control off allows every call but still audits every non-read one, so the
record shows what ran and that policy was off.

### 3. What happens when no rule matches a call?

- [ ] The call is allowed.
- [ ] The turn pauses for approval.
- [x] The call is denied with rule `default.deny`.

Answer: the policy is an allowlist. Anything the rules do not explicitly allow or send to a
human is refused.

## Further reading

- [OWASP Top 10 for LLM Applications (2025), LLM06: Excessive Agency](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/)
- [Saltzer and Schroeder, The Protection of Information in Computer Systems (1975)](https://www.cs.virginia.edu/~evans/cs551/saltzer/), where fail-safe defaults and least privilege come from
- [`spec/contracts/policy.md`](../spec/contracts/policy.md)
