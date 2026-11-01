---
node: tools
covers: [registry, sandbox]
try:
  fixture: tool_call
  prompt: "Where is order A-1001?"
---

# Inside tools: registry and sandbox

> What a tool is, what each mode offers, and the walls around the code that runs it.

## What it does

Chapter 7 followed one call down the invoke path. The Expert view opens Tools into the two places
where the path touches code: the registry, which knows what tools exist, and the sandbox, which
runs them.

### Registry

A tool is a `ToolSpec`: a name, a description for the model, a JSON Schema for the arguments, an
action class (`read`, `write`, `destructive`, `external`, `memory.write`), a timeout, whether it
is pure or idempotent, and where its code lives. Built-in tools (`calculator`, `read_skill`,
`remember`, `read_doc`) are registered in code; a pack adds its own in `tools.yaml`, either as a
table (a pure lookup, like the starter pack's `lookup_order`) or as an `entrypoint`, a
`module:function` or `file.py:function` of real Python. The registry also decides what each mode
may offer: public mode only offers pure functions and knowledge search, and every mode offers only
tools on the policy allowlist.

### Sandbox

Every call runs through `Sandbox.execute` at one of two levels. `subprocess`, the default in dev
and private mode, starts one child process per call in a work directory created for the turn
(and removed when the turn ends), with a scrubbed environment, its own process group, a
wall-clock timeout, an output cap and a memory cap. Inside the child a Python audit hook refuses
sockets (unless the tool is `external` and policy allowed it), spawning processes, and writing
anywhere outside the work directory, and an OS jail adds a second wall (`sandbox-exec` on macOS,
`unshare` on Linux). `inprocess` calls the function in a worker thread: fast, for tests, for tools
that read harness memory, and for public mode, where only pure functions are allowed anyway.

## What breaks without it

The sandbox has no disable switch, but it has a level, and comparing the two shows what the walls
are for. Give the starter pack a tool that misbehaves on purpose, using a probe the test suite
already has. Append this to `datasets/_starter/tools.yaml`:

```yaml
- name: save_note
  description: Save a note to a file.
  action_class: write
  timeout_ms: 2000
  entrypoint: ../../tests/unit/sandbox_tools/probes.py:write_outside
  parameters: { type: object, required: [path], properties: { path: { type: string } } }
```

add `save_note` to `agent.allow_tools` in `config/policy.yaml`, and save a fixture
`datasets/_starter/fixtures/fake/escape_demo.jsonl` with one line:
`{"responses": [{"tool_calls": [{"name": "save_note", "arguments": {"path": "/tmp/escape.txt"}}]}, {"text": "Saved."}]}`.
Then:

```sh
uv run harnesswalk turn "Save a note." --provider fake --fixture escape_demo
uv run harnesswalk turn "Save a note." --provider fake --fixture escape_demo --set sandbox.level=inprocess
```

The first run's sandbox span reads `outcome=error level=subprocess network=blocked
jail=sandbox-exec` (on macOS), and `/tmp/escape.txt` does not exist: the audit hook raised "writes
outside the turn's work directory" before the file was opened. The second run reads `outcome=ok
level=inprocess`, and `/tmp/escape.txt` now contains `escaped`. Same tool, same arguments, same
policy decision; the only difference is the wall. Without the sandbox, any tool a pack ships, or
any argument a model invents for it, runs with the full rights of the harness process. Remove the
three additions and the file afterwards.

## The code walk

[`ToolSpec`](../harnesswalk/execution/registry.py#ToolSpec) is the record, and
[`ToolSpec.schema`](../harnesswalk/execution/registry.py#ToolSpec.schema) is the part the model
sees. [`ToolRegistry.load_text`](../harnesswalk/execution/registry.py#ToolRegistry.load_text)
parses a pack's `tools.yaml`, turning a `table` into a pure lookup with
[`table_tool`](../harnesswalk/execution/registry.py#table_tool).
[`ToolRegistry.list`](../harnesswalk/execution/registry.py#ToolRegistry.list) is what a mode
offers, and [`run_registry`](../harnesswalk/execution/registry.py#run_registry) is the span.

[`Sandbox.level_for`](../harnesswalk/execution/sandbox.py#Sandbox.level_for) picks the level:
public mode is always `inprocess`, and a tool that declares it needs harness memory runs in
process. [`Sandbox._subprocess`](../harnesswalk/execution/sandbox.py#Sandbox._subprocess) builds
the child: the scrubbed environment, the work directory, the jail prefix from
[`jail_prefix`](../harnesswalk/execution/sandbox.py#jail_prefix), the timeout that kills the whole
process group, and the capped reads of its output.

[`sandbox_child.py`](../harnesswalk/execution/sandbox_child.py#main) is the child. Read
[`_install_hook`](../harnesswalk/execution/sandbox_child.py#_install_hook): Python's audit hooks
(PEP 578) see every `open`, `socket.connect` and `subprocess.Popen`, and the hook raises
`SandboxViolation` for the forbidden ones. [`_limits`](../harnesswalk/execution/sandbox_child.py#_limits)
sets the resource limits before the tool is even imported.

[`Sandbox.idempotency_key`](../harnesswalk/execution/sandbox.py#Sandbox.idempotency_key) keys
side-effecting calls by tenant, turn and call id, so a turn resumed after an approval never
repeats a side effect in this process.

## Try this

1. **Read the span.** Send "Where is order A-1001?" with fixture `tool_call` in the Expert view
   and open Sandbox. Expected: `level=subprocess network=blocked`, a `jail` on macOS or Linux, and
   `executed=True`; the tool line in the chat shows the same sandbox chip.
2. **Run the sandbox tests.** `uv run pytest tests/unit/test_sandbox.py -v`. Expected: tests for
   the timeout, the output cap, the memory cap, no network, no writes outside, no processes and a
   scrubbed environment, each using one probe from `tests/unit/sandbox_tools/probes.py`.
3. **Try public mode.** Run the `tool_call` prompt with `--mode public`. Expected: the sandbox
   span says `level=inprocess`, because public mode only offers pure functions; and
   `cancel_order` is not offered at all, so a model that names it gets "is not offered here".

## Check yourself

### 1. A tool's entrypoint tries to write a file in your home directory. In subprocess mode, what stops it?

- [ ] Policy, because the tool's class is `write`.
- [x] The audit hook in the child refuses writes outside the turn's work directory, and the OS jail backs it up.
- [ ] The registry, because file writes are not allowed in `tools.yaml`.

Answer: policy allowed the call; the sandbox is what limits what the code can do once it runs.

### 2. Why does public mode run tools in process?

- [x] Because public mode only offers pure functions and knowledge search, which need no walls.
- [ ] Because subprocesses are too slow for visitors.
- [ ] Because the audit hook does not work in public mode.

Answer: in public mode nothing with side effects is offered at all, so the cheaper level is safe.

### 3. What does a table tool such as `lookup_order` need from the sandbox?

- [ ] Network access to the order system.
- [ ] A writable work directory.
- [x] Nothing special: it is a pure lookup, run like any other call under the same limits.

Answer: the starter tools are rows in YAML, so the demos are deterministic, and they still go
through the same sandbox path as real code.

## Further reading

- [PEP 578, Python Runtime Audit Hooks](https://peps.python.org/pep-0578/)
- [Linux man page, unshare(1)](https://man7.org/linux/man-pages/man1/unshare.1.html)
- [`spec/contracts/sandbox.md`](../spec/contracts/sandbox.md) and [`spec/contracts/tool_registry.md`](../spec/contracts/tool_registry.md)
