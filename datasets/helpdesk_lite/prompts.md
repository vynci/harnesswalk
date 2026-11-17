# Try these: Pebblebrook IT Helpdesk

The lowest rung of the ladder: the loop, the model and the tools, with the system prompt and
the session. Every other node is dimmed but still in place. Each question is one read-only
lookup, so even a small local model can answer it.

1. What is the status of ticket HD-1042?
   *One read tool, `ticket_status`.*
2. Is the VPN down?
   *`service_status` for `vpn`: an outage with a workaround.*
3. Is email working right now?
   *`service_status` for `email`.*
4. What is the guest wifi network at the harbour office?
   *`office_info` for `harbour`.*
5. When is the IT desk open?
   *No tool: the system prompt answers.*
6. What is happening with my ticket HD-1077?
   *`ticket_status`: the desk is waiting on you.*
