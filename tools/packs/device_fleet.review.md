- The card, system prompt, skills (including `skills/policy.md`), tool tables, seeds, prompts
  page, golden set and fixtures were written by hand (the build agent, P2) from the plan's
  telematics notes; only the twelve documents came from the generator. The golden set was
  built with a small local script and its node paths filled from a fake-provider run, then read
  case by case.
- The tool shapes follow a real fleet platform's API (asset status, session insights, driver
  events with their payload fields, driver summary, the webhook inbox, device commands and their
  error codes) and the rule thresholds, score weights and retry schedule are that platform's
  public defaults. Every name, id, plate, place, date and number in the pack is invented; no
  real telemetry, id, road, coordinate or SMS content was used.
- The real API has one command endpoint; the pack splits it into one tool per risk level
  (`obd_scan`, `reboot_device`, `toggle_rule`, `power_off_device`, `clear_sms`,
  `purge_device`) because the harness's policy decides per tool. Agent update is left to the
  console and not a tool.
- `skills/policy.md` front matter: the description was quoted (it held a colon).
