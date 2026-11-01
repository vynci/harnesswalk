# Pack brief: finance

Business: **Hollowmere Capital Partners**, a fictional private-markets fund manager with
four invented closed-end funds (fund ids `HCP-01` to `HCP-04`, for example "Hollowmere Growth
Partners III"). The assistant serves the funds' limited partners (investors) from the
investor relations desk. Domain: finance. Investor account numbers look like
`LP-1234-5678-90` and are treated as personal data. No real instruments, no market data, no
investment advice.

Feature it demonstrates: **citations and the memory writer.** Answers about fund terms,
capital calls and distributions cite the fund documents as [1], [2] (knowledge retrieval and
the output guardrails' citation check), and an investor's standing preferences are proposed
with `remember`, written by the memory writer after the reply, and recalled in a later
session from semantic memory. Feature nodes: `knowledge`, `output_guardrails`,
`memory_writer`, `semantic`.

System prompt rules to add: you answer limited partners' questions about the funds' documents
and notices; cite every document claim; you do not give investment, tax or legal advice;
when an investor states a durable preference (reporting currency, notice delivery, statement
format, a contact window), propose it with `remember` (kind `fact`); never repeat an investor
account number unless they typed it in this message; investor relations desk hours Monday to
Friday 9:00 to 17:00.

Tools (all `read`):

1. `fund_nav`: key `fund_id`, pattern `^HCP-0[1-4]$`. Rows: fund_id, fund_name, vintage,
   nav_date (a quarter end), nav_per_unit, currency, fund_nav (like `412.6m`), status
   (`investing` or `harvesting`). 4 rows.
2. `capital_calls`: key `fund_id`. Rows: fund_id, latest_call (call number), notice_date,
   due_date, call_pct (percent of commitment), purpose, called_to_date_pct. 4 rows.

Do not write `guardrails.yaml`; the build adds it.

Skills (3 to 5): `capital_call_questions`, `distribution_questions`, `fund_documents` (always
cite), `investor_preferences` (when and how to call `remember`), optionally
`account_security`.

Documents (14 to 16): a fact sheet per fund (four: strategy, vintage, fund size, management
fee, carry, hurdle, term), a capital call notice for two funds (call number, amount as percent
of commitment, due date, wire cut-off, late payment interest), two distribution notices (amount
per unit, record date, payment date, split between return of capital and gains), a glossary of
fund terms (NAV, commitment, called capital, distribution, carry, hurdle, clawback, J-curve), an
investor reporting calendar (quarterly statements 45 days after quarter end, annual audited
accounts 120 days), a notice delivery and investor portal guide, a transfer of interests policy,
and a default and late payment policy. Two of them are PDFs: the first capital call notice and
the glossary.

Golden cases: 24. Tags: `citations`, `memory`, `tool`, `pii`. Include:

- 10 citation cases (`expect.citations` naming the document): management fee of a fund, a
  hurdle rate, a capital call due date, late payment interest, a distribution payment date,
  the return of capital share of a distribution, the definition of carry, the statement
  deadline, the transfer of interests consent rule, a fund's term.
- 6 memory cases in three pairs. The first case of each pair asks to remember a preference
  ("Please note that I want ... from now on") and calls `remember` with
  `{"kind": "fact", "key": "<two or three words>", "value": "<the preference>"}`; its path is
  the remember template. The second case of the pair comes right after it in the file, is a
  new session, asks a question whose words include every word of that key, and answers from
  memory with no tool; give it `"checks": {"retrieved": {"semantic": "<a short substring of
  the remembered value>"}}`. Keys: `reporting currency`, `notice delivery`, `statement format`.
- 4 tool cases: `fund_nav` for two funds, `capital_calls` for two funds.
- 2 account number cases: the user types `LP-4417-2093-58` (and once `LP-8802-1176-30` with
  the email `m.achterberg@example.com`); the first model call expects `[ID_1]`
  (and `[EMAIL_1]`); `answer_contains` holds the original value.
- 2 combined cases: a tool call plus a cited document (for example `capital_calls` then a
  reply citing that fund's capital call notice).
