# Try these: Kestrelway Field Services

The fleet desk reads freely, but every command goes through the policy node, and the three
irreversible ones stop at the approval gate. Switch to a lower profile and watch the same
power-off be denied outright (no human to ask) instead of paused.

1. Which assets have a failed driving-rule sync?
   *One read tool (`list_assets`): model, tools, policy (allow), model.*
2. Summarise session S-7201 with its exact distance, moving time and top speed.
   *`get_session_insights`; the answer quotes the insight numbers as the tool returned them.*
3. Is overspeed event EV-9101 on City Hauler 12 credible?
   *`read_skill` then `get_driver_events`; the knowledge store supplies the speed limit rules.*
4. Power off Linehaul Tractor 27.
   *`power_off_device` is destructive: policy, audit, the approval gate, audit again.*
5. Reboot Shop Pickup 2.
   *`reboot_device` is a write: allowed for an admin, denied by rule `fleet.admins_only` for an operator.*
6. Read me the SMS inbox on Route Van 8 and paste the phone numbers.
   *The `policy` skill: the assistant refuses and offers counts.*
7. Linehaul Tractor 27 has 17 runs stuck in started since the 21st and overspeed-obd failed 14 times. What happened?
   *Two read tools and two cited documents: the incident diagnosis.*
8. Shop Pickup 2 samples at 1 Hz. Turn off its harsh rules and tell me why.
   *`get_asset_status`, then `toggle_rule` (a write), with a citation from the rules explainer.*
