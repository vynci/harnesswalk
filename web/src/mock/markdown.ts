// A reply in GitHub-flavoured markdown: headings, lists, a table, code and links. The mock plays
// it for a message that asks for a table or markdown, the renderer's unit test reads it, and
// e2e-live/fixtures/markdown_reply.yaml holds the same text for the live public-mode test.
export const MARKDOWN_REPLY = `## Order summary

Here is what I found for **A-1001**, with *one* note:

- Shipped on 2026-10-30
- Carrier: \`UPS\`
  - Tracking updates hourly

1. Check the tracking page.
2. Reply here if it stalls.

| Order | Status | Total |
| --- | --- | ---: |
| A-1001 | shipped | $42.00 |
| A-1002 | processing | $18.50 |

\`\`\`json
{"order": "A-1001", "status": "shipped"}
\`\`\`

See the [returns policy](https://example.com/returns) for more.`

/** Messages the mock answers with the markdown reply. */
export const MARKDOWN_PROMPT = /\b(table|markdown)\b/i
