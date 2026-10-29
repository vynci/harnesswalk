# web

Placeholder. The web UI arrives in **P2**: React and Vite, a chat panel, and a diagram panel
rendered with React Flow from `GET /v1/diagram` and lit by the event stream
(`GET /v1/turns/{id}/events`). Read-only inspectors, a code peek per node, a simple and expert
view toggle, and a lesson rail.

Until then, the contract the UI will consume is in `spec/events.md` and
`spec/contracts/http_api.md`.
