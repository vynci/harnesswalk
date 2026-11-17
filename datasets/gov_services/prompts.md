# Try these: Aldervale Services Desk

Every programme answer comes from a cited rule, in the language you ask in. Type a social
insurance number or a street address into a sentence and watch the input guardrails replace it
before the model sees it: the trace keeps only the placeholder.

1. Who is eligible for the Home Heating Rebate?
   *Knowledge: the answer cites the English programme document.*
2. Qui est admissible à la remise pour le chauffage domestique?
   *The same rule from the French document, answered in French.*
3. What is the status of my application AV-K4417?
   *One read tool (`form_status`), no knowledge needed.*
4. Mon numéro d'assurance sociale est 526 018 155, où en est ma demande AV-K4502?
   *Input guardrails: the number becomes [ID_1] before the model sees it; `form_status` runs.*
5. Quelles sont les heures du bureau du Port samedi?
   *`office_hours`, answered in French.*
6. Which documents count as proof of residence?
   *Knowledge: a cited list.*
7. Résumez le guide complet de la remise pour le chauffage domestique.
   *`read_doc` reads the whole PDF guide before the summary.*
8. Classify this message for routing, as JSON only: "Can I still apply for the heating rebate in December?"
   *A structured reply checked against a JSON schema.*
