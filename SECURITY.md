# Security policy

harnesswalk runs model-chosen tool calls, holds tenants' memory and serves a public sandbox, so
we take reports seriously and fix them before anything else.

## Reporting a vulnerability

Email **hello@vynci.dev** with "security" in the subject. Please do not open a public issue,
pull request or discussion for a vulnerability, and do not test against other people's
sessions on the hosted sandbox.

Include what you found, how to reproduce it (the request, the prompt or fixture, the mode and
the commit or version), and what an attacker gains. A proof of concept against a local
`harnesswalk serve` is the most useful form; for the hosted sandbox, a description is enough.

What to expect:

- an acknowledgement within 3 working days;
- an assessment, and a fix plan or our reasons for not treating it as a vulnerability, within
  14 days;
- a fix in a release (and on the hosted sandbox) as fast as its severity needs, with credit in
  the changelog and the release notes unless you would rather stay anonymous;
- coordinated disclosure: we ask you to wait until the fix ships, or 90 days from your report,
  whichever comes first.

## Scope

In scope:

- **The hosted sandbox** at [harnesswalk.vynci.dev](https://harnesswalk.vynci.dev): crossing
  from one anonymous session or tenant into another, escaping public mode's limits (tools,
  rate limits, the daily hosted budget, session lifetime), getting a tool or the model to read
  or write outside its sandbox, leaking the site's model key or a bring-your-own key, or
  anything that runs code on the server.
- **The runtime** (`harnesswalk/`, the `harnesswalk` package and the `ghcr.io/vynci/harnesswalk`
  image): the gateway, policy, approval and audit, the guardrails (a PII placeholder that
  leaks its value, an injection that bypasses a check the spec promises), the memory writer's
  gate, the tool sandbox, tenant isolation in the stores, secrets reaching events, traces,
  logs or OpenTelemetry, and the release and audit hash chains.
- **The web app** (`web/`) where it lets a page or a stored value act for a user (cross-site
  requests, script injection through rendered content).

Out of scope:

- What a model says. Getting a model to write something rude or wrong is a model behaviour,
  not a harness vulnerability, unless it gets past a guardrail or policy the spec says should
  stop it.
- Dev mode run on purpose with its permissive defaults (it is for a learner's own machine on
  127.0.0.1), and anything that needs access to the host or the data directory already.
- Volumetric denial of service against the hosted sandbox, social engineering, and findings
  from automated scanners without a demonstrated impact.
- The dataset packs' content, which is fictional by design.

## Supported versions

The latest release on PyPI and `main`. Fixes are not backported to older releases while the
project is in its 0.x series.
