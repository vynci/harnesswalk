# Try these

Hollowmere Capital Partners shows citations and the memory writer. Fund answers come from the fund documents and carry [1] and [2] markers that the output guardrails check, and a stated preference is saved after the reply and recalled in a later session.

1. What is the management fee for Hollowmere Growth Partners III?
   *Lights up knowledge retrieval, the model, then output_guardrails checking the [1] citation.*
2. What hurdle rate does HCP-02 use before carry is paid?
   *Knowledge passages from the HCP-02 fact sheet; the citation check runs on the reply.*
3. When is the next capital call for HCP-03 due, and how much is it?
   *The capital_calls tool through policy, then a cited answer from the capital call notice.*
4. What share of the latest HCP-02 distribution is return of capital?
   *Knowledge retrieval from the distribution notice; the reply cites it.*
5. What is the NAV per unit for HCP-04?
   *The fund_nav tool through policy; no citation needed because the figure comes from a tool.*
6. Please note that I want all my reports in euro from now on.
   *The remember tool, then the memory_writer node stores a fact after the reply.*
7. Start a new session and ask: which reporting currency should you use for me?
   *Semantic memory recalls the stored fact in the context builder; no tool is called.*
8. What does carry mean in a fund like this?
   *The glossary passage is retrieved and cited.*
9. My account is LP-4417-2093-58. Can you see my balance?
   *Input guardrails replace the account number with a placeholder before the model sees it.*
