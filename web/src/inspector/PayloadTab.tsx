import { useMemo, useState, type ReactNode } from 'react'
import { Check, Copy, Wrench } from 'lucide-react'
import type {
  ContextBuilderPayload,
  GatewayPayload,
  HarnessEvent,
  InputGuardrailsPayload,
  ModelLayerPayload,
  OutputGuardrailsPayload,
  Payload,
  PayloadCheck,
  PayloadSection,
  PayloadText,
  PayloadToolCall,
  PayloadVerdict,
} from '@/api/types'
import { ToolLine } from '@/chat/ToolTrace'
import { toolCalls } from '@/chat/toolCalls'
import { Code } from '@/components/code'
import { Markdown } from '@/components/markdown'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { Trace, TraceCollapsible, TraceCollapsibleContent, TraceCollapsibleTrigger, TraceIcon, TraceLine, TraceTitle } from '@/components/ui/trace'
import { cn } from '@/lib/utils'
import { KIND_LABEL, kindsInWords, preview, share, wordDiff } from './payload'
import { usePayload } from './usePayload'

const fmt = (n: number) => n.toLocaleString('en-US')

function Heading({ children, id }: { children: ReactNode; id?: string }) {
  return (
    <h3 className="text-sm font-medium" id={id}>
      {children}
    </h3>
  )
}

function Note({ children, testId }: { children: ReactNode; testId?: string }) {
  return (
    <p className="text-xs text-muted-foreground" data-testid={testId}>
      {children}
    </p>
  )
}

function Cut({ text }: { text: PayloadText }) {
  if (!text.truncated) return null
  return <Note testId="payload-truncated">Cut at {fmt(text.text.length)} of {fmt(text.chars)} characters.</Note>
}

/** One text as the harness stored it, on the Code Block (plain text, wrapped). */
function TextBlock({ label, text, testId }: { label: string; text: PayloadText; testId?: string }) {
  return (
    <div className="flex min-w-0 flex-col gap-1.5" data-testid={testId}>
      <span className="text-xs text-muted-foreground">
        {label} <span className="tabular-nums text-muted-foreground/70">· {fmt(text.chars)} chars</span>
      </span>
      <Code code={text.text || ' '} language="text" size="sm" wrap label={label} contentClassName="max-h-64 text-xs" />
      <Cut text={text} />
    </div>
  )
}

function CopyJson({ value, label = 'Copy as JSON' }: { value: unknown; label?: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <Button
      type="button"
      variant="outline"
      size="xs"
      data-testid="payload-copy-json"
      onClick={() => {
        void navigator.clipboard?.writeText(JSON.stringify(value, null, 2)).then(() => {
          setCopied(true)
          setTimeout(() => setCopied(false), 2000)
        })
      }}
    >
      {copied ? <Check aria-hidden /> : <Copy aria-hidden />}
      {copied ? 'Copied' : label}
    </Button>
  )
}

// -- Gateway ------------------------------------------------------------------------------------

function Replaced({ p }: { p: GatewayPayload }) {
  const ph = p.placeholders
  if (!ph) return null
  if (ph.count === 0) return <Note testId="payload-replaced">No values were replaced.</Note>
  return (
    <Note testId="payload-replaced">
      {ph.count} {ph.count === 1 ? 'value was' : 'values were'} replaced with placeholders ({kindsInWords(ph.kinds)}):{' '}
      {ph.tokens.map((t, i) => (
        <code key={t} className="font-mono">
          {t}
          {i < ph.tokens.length - 1 ? ', ' : ''}
        </code>
      ))}
      . The values themselves are never stored.
    </Note>
  )
}

function GatewayView({ p }: { p: GatewayPayload }) {
  if (p.phase === 'reply' && p.at === 'enter') {
    return p.reply ? <ReplyBlock label="Reply about to be sent" text={p.reply} /> : null
  }
  const received = p.received ?? null
  const redacted = p.phase === 'reply' ? (p.redacted ?? null) : null
  const differs = !!received && !!redacted && received.text !== redacted.text
  return (
    <div className="flex flex-col gap-3">
      <Heading>Message</Heading>
      {differs ? (
        <div className="grid grid-cols-2 gap-2" data-testid="payload-side-by-side">
          <TextBlock label="As received" text={received!} testId="payload-received" />
          <TextBlock label="After redaction" text={redacted!} testId="payload-redacted" />
        </div>
      ) : redacted ? (
        <TextBlock label={received ? 'As received (unchanged by redaction)' : 'After redaction'} text={redacted} testId="payload-redacted" />
      ) : received ? (
        <TextBlock label="As received" text={received} testId="payload-received" />
      ) : null}
      {p.withheld && (
        <Note testId="payload-withheld">
          The message as typed is not kept in {p.phase === 'receive' ? 'this mode before redaction' : 'this mode'}: only the text with placeholders is stored.
        </Note>
      )}
      {p.phase === 'reply' && <Replaced p={p} />}
      {p.blocked && <Note>The input guardrails blocked this message: the model never saw it.</Note>}
      {p.phase === 'reply' && p.reply && <ReplyBlock label="Reply sent" text={p.reply} />}
    </div>
  )
}

function ReplyBlock({ label, text }: { label: string; text: PayloadText }) {
  return (
    <div className="flex flex-col gap-1.5" data-testid="payload-reply">
      <Heading>{label}</Heading>
      <div className="rounded-2xl bg-muted/40 px-3 py-2.5 text-sm ring-1 ring-foreground/5">
        <Markdown text={text.text} />
      </div>
      <Cut text={text} />
    </div>
  )
}

// -- Input guardrails ---------------------------------------------------------------------------

const VERDICT_VARIANT = {
  pass: 'secondary',
  skipped: 'secondary',
  redact: 'outline',
  repair: 'outline',
  warn: 'outline',
  block: 'destructive',
} as const

function VerdictChip({ verdict }: { verdict: keyof typeof VERDICT_VARIANT }) {
  return (
    <Badge variant={VERDICT_VARIANT[verdict]} data-testid="verdict-chip" data-verdict={verdict}>
      {verdict}
    </Badge>
  )
}

function CheckRow({ c }: { c: PayloadCheck }) {
  return (
    <li className="flex flex-col gap-0.5 py-2 first:pt-0 last:pb-0" data-testid="payload-check" data-check={c.check}>
      <div className="flex items-center gap-2">
        <VerdictChip verdict={c.verdict} />
        <span className="text-sm font-medium">{c.check}</span>
        <span className="ml-auto text-xs text-muted-foreground/70 tabular-nums">
          read {fmt(c.looked_at.chars)} chars, {c.looked_at.stage === 'received' ? 'as received' : c.looked_at.stage}
        </span>
      </div>
      {c.change && <p className="text-xs text-muted-foreground">Changed: {c.change}</p>}
      {c.reason && <p className="text-xs text-muted-foreground">Why: {c.reason}</p>}
      {c.score !== undefined && <p className="text-xs text-muted-foreground tabular-nums">Injection score {c.score.toFixed(2)}</p>}
    </li>
  )
}

function InputGuardrailsView({ p }: { p: InputGuardrailsPayload }) {
  const input = p.input
  const output = p.output ?? null
  return (
    <div className="flex flex-col gap-3">
      {p.at === 'enter' ? (
        <>
          {input ? <TextBlock label="Input about to be checked" text={input} testId="payload-input" /> : null}
          {p.withheld && <Note testId="payload-withheld">The input contains values the checks will replace; it is not kept as typed in this mode.</Note>}
        </>
      ) : (
        <>
          <Heading>Checks</Heading>
          {p.disabled ? (
            <Note>The input guardrails were disabled for this turn: the input went on unchecked.</Note>
          ) : (
            <ul className="flex flex-col divide-y" data-testid="payload-checks">
              {(p.checks ?? []).map((c, i) => (
                <CheckRow key={`${c.check}-${i}`} c={c} />
              ))}
            </ul>
          )}
          {(p.warnings ?? []).length > 0 && <Note>Warnings: {p.warnings!.join(', ')}</Note>}
          {input && output && input.text !== output.text && <TextBlock label="Input" text={input} testId="payload-input" />}
          {output ? <TextBlock label="Clean input (what goes on)" text={output} testId="payload-output" /> : <Note>Blocked: nothing went on to the context builder.</Note>}
          {p.withheld && <Note testId="payload-withheld">The input as typed is not kept in this mode.</Note>}
        </>
      )}
    </div>
  )
}

// -- Sections (context builder, model request) ------------------------------------------------

function SectionCard({ s, total }: { s: PayloadSection; total: number }) {
  const [open, setOpen] = useState(false)
  const { head, more } = preview(s.text)
  const pct = share(s.tokens, total)
  const sources = s.sources.map((x) => x.label).filter(Boolean)
  return (
    <li className="flex flex-col gap-1.5 rounded-2xl px-3 py-2.5 ring-1 ring-foreground/10" data-testid="payload-section" data-kind={s.kind} data-role={s.role} aria-label={`${s.label}, ${s.role}, ${s.tokens} tokens`}>
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge variant="outline" data-testid="section-role">
          {s.role}
        </Badge>
        {(KIND_LABEL[s.kind] ?? s.kind) !== s.role && (
          <Badge variant="secondary" data-testid="section-kind">
            {KIND_LABEL[s.kind] ?? s.kind}
          </Badge>
        )}
        <span className="min-w-0 truncate text-sm font-medium">{s.label}</span>
        <span className="ml-auto text-xs text-muted-foreground tabular-nums" data-testid="section-tokens">
          {fmt(s.tokens)} tokens · {pct}%
        </span>
      </div>
      {sources.length > 0 && (
        <p className="truncate text-xs text-muted-foreground/70" title={sources.join(', ')} data-testid="section-sources">
          from {sources.join(', ')}
        </p>
      )}
      <Progress value={pct} className="h-1" aria-label={`${s.label}: ${s.tokens} of ${total} tokens`} />
      {s.text ? (
        <pre className="font-mono text-xs leading-relaxed break-words whitespace-pre-wrap text-muted-foreground" data-testid="section-text" data-open={open ? 'true' : 'false'}>
          {open ? s.text : head}
        </pre>
      ) : (
        <p className="text-xs text-muted-foreground/70">(no text)</p>
      )}
      {s.tool_calls && s.tool_calls.length > 0 && <AskedCalls calls={s.tool_calls} />}
      {open && <Cut text={s} />}
      {(more || s.truncated) && (
        <Button type="button" variant="ghost" size="xs" className="-ml-2 w-fit text-muted-foreground" aria-expanded={open} onClick={() => setOpen((o) => !o)} data-testid="section-expand">
          {open ? 'Collapse' : 'Expand'}
        </Button>
      )}
    </li>
  )
}

function SectionList({ sections, total, label }: { sections: PayloadSection[]; total: number; label: string }) {
  return (
    <ol className="flex flex-col gap-2" aria-label={label} data-testid="payload-sections">
      {sections.map((s) => (
        <SectionCard key={s.index} s={s} total={total} />
      ))}
    </ol>
  )
}

function Totals({ count, messages, total, extra }: { count: number; messages: number; total: number; extra?: ReactNode }) {
  return (
    <p className="text-xs text-muted-foreground tabular-nums" data-testid="payload-totals">
      {count} sections in {messages} messages · {fmt(total)} tokens{extra}
    </p>
  )
}

function ContextBuilderView({ p }: { p: ContextBuilderPayload }) {
  if (p.at === 'enter' || !p.sections) {
    return (
      <div className="flex flex-col gap-3">
        {p.input && <TextBlock label="Clean input to assemble a context for" text={p.input} testId="payload-input" />}
        <Note>Budget {fmt(p.budget_tokens)} tokens. The assembled context is recorded when the node finishes.</Note>
      </div>
    )
  }
  const total = p.total_tokens ?? 0
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Heading>Assembled context</Heading>
        <span className="ml-auto">
          <CopyJson value={p} />
        </span>
      </div>
      <Totals
        count={p.sections.length}
        messages={p.messages ?? 0}
        total={total}
        extra={
          <>
            {' '}
            of a {fmt(p.budget_tokens)} budget
            {p.limit_tokens !== undefined ? ` (${fmt(p.limit_tokens)} after the output reserve)` : ''}
          </>
        }
      />
      <SectionList sections={p.sections} total={total} label="Assembled context sections" />
    </div>
  )
}

// -- Model layer --------------------------------------------------------------------------------

function AskedCalls({ calls }: { calls: PayloadToolCall[] }) {
  return (
    <Trace aria-label="Tool calls asked for">
      {calls.map((c, i) => (
        <AskedCall key={c.call_id ?? i} call={c} />
      ))}
    </Trace>
  )
}

function AskedCall({ call }: { call: PayloadToolCall }) {
  const args = call.arguments_text ? call.arguments_text.text : JSON.stringify(call.arguments ?? {}, null, 2)
  return (
    <TraceCollapsible>
      <TraceCollapsibleTrigger>
        <TraceLine data-testid="asked-tool-call" data-tool={call.name ?? undefined}>
          <TraceIcon>
            <Wrench />
          </TraceIcon>
          <TraceTitle>Asked for {call.name}</TraceTitle>
        </TraceLine>
      </TraceCollapsibleTrigger>
      <TraceCollapsibleContent>
        <Code code={args} language="json" size="sm" wrap label="Arguments" contentClassName="max-h-64" />
      </TraceCollapsibleContent>
    </TraceCollapsible>
  )
}

/** The reply's tool calls as the chat's tool lines, once the turn has run them. */
function ReplyCalls({ calls, events, turnOver }: { calls: PayloadToolCall[]; events: HarnessEvent[]; turnOver: boolean }) {
  const ran = useMemo(() => new Map(toolCalls(events).map((c) => [c.callId, c])), [events])
  return (
    <Trace role="group" aria-label="Tool calls" data-testid="payload-tool-calls">
      {calls.map((c, i) => {
        const view = c.call_id ? ran.get(c.call_id) : undefined
        return view ? <ToolLine key={c.call_id} call={{ ...view, args: view.args ?? c.arguments }} turnOver={turnOver} /> : <AskedCall key={c.call_id ?? i} call={c} />
      })}
    </Trace>
  )
}

function ModelLayerView({ p, events, turnOver }: { p: ModelLayerPayload; events: HarnessEvent[]; turnOver: boolean }) {
  const req = p.request
  const reply = p.reply
  return (
    <div className="flex flex-col gap-4">
      {reply && (
        <section className="flex flex-col gap-2" aria-labelledby="payload-reply-heading" data-testid="payload-reply">
          <Heading id="payload-reply-heading">Reply</Heading>
          <p className="text-xs text-muted-foreground tabular-nums">
            {[p.model && `${p.provider}/${p.model}`, reply.finish_reason && `finish ${reply.finish_reason.replace(/_/g, ' ')}`, `${fmt(reply.chars)} chars`].filter(Boolean).join(' · ')}
          </p>
          {reply.text ? (
            <div className="rounded-2xl bg-muted/40 px-3 py-2.5 text-sm ring-1 ring-foreground/5">
              <Markdown text={reply.text} />
            </div>
          ) : (
            <Note>No text: the model asked for tools instead.</Note>
          )}
          <Cut text={reply} />
          {reply.tool_calls.length > 0 && <ReplyCalls calls={reply.tool_calls} events={events} turnOver={turnOver} />}
        </section>
      )}
      {req && (
        <section className="flex flex-col gap-2" aria-labelledby="payload-request-heading" data-testid="payload-request">
          <div className="flex flex-wrap items-center gap-2">
            <Heading id="payload-request-heading">{p.at === 'enter' ? 'Request about to be sent' : 'Request'}</Heading>
            <span className="ml-auto">
              <CopyJson value={req} />
            </span>
          </div>
          <Totals
            count={req.sections.length}
            messages={req.messages}
            total={req.total_tokens}
            extra={req.tools && req.tools.length ? ` · tools offered: ${req.tools.join(', ')}` : ''}
          />
          <SectionList sections={req.sections} total={req.total_tokens} label="Request sections" />
        </section>
      )}
    </div>
  )
}

// -- Output guardrails --------------------------------------------------------------------------

function DiffColumn({ label, parts, side }: { label: string; parts: ReturnType<typeof wordDiff>; side: 'before' | 'after' }) {
  return (
    <div className="flex min-w-0 flex-col gap-1.5" data-testid={`payload-${side}`}>
      <span className="text-xs text-muted-foreground">{label}</span>
      <div className="max-h-64 overflow-auto rounded-2xl bg-muted/40 px-3 py-2.5 font-mono text-xs leading-relaxed break-words whitespace-pre-wrap ring-1 ring-foreground/5">
        {parts.map((part, i) =>
          part.type === 'same' ? (
            <span key={i}>{part.text}</span>
          ) : (part.type === 'removed') === (side === 'before') ? (
            <mark
              key={i}
              data-diff={part.type}
              className={cn('rounded-sm', part.type === 'removed' ? 'bg-destructive/10 text-destructive line-through' : 'bg-foreground/10 text-foreground')}
            >
              <span className="sr-only">{part.type === 'removed' ? 'removed: ' : 'added: '}</span>
              {part.text}
            </mark>
          ) : null,
        )}
      </div>
    </div>
  )
}

function VerdictRow({ v }: { v: PayloadVerdict }) {
  return (
    <li className="flex items-start gap-2 py-1.5 first:pt-0 last:pb-0" data-testid="payload-check" data-check={v.check}>
      <VerdictChip verdict={v.verdict} />
      <span className="text-sm font-medium">{v.check}</span>
      <span className="ml-auto text-right text-xs text-muted-foreground">{v.note}</span>
    </li>
  )
}

function OutputGuardrailsView({ p }: { p: OutputGuardrailsPayload }) {
  if (p.at === 'enter' || !p.after) return <TextBlock label="Answer about to be checked" text={p.before} testId="payload-before" />
  const parts = wordDiff(p.before.text, p.after.text)
  const same = p.before.text === p.after.text
  return (
    <div className="flex flex-col gap-3">
      <Heading>Answer and reply</Heading>
      {same ? (
        <TextBlock label="Unchanged by the checks" text={p.after} testId="payload-after" />
      ) : (
        <div className="grid grid-cols-2 gap-2" data-testid="payload-side-by-side">
          <DiffColumn label="Before (the loop's answer)" parts={parts} side="before" />
          <DiffColumn label="After (the reply that goes on)" parts={parts} side="after" />
        </div>
      )}
      <Note>
        {p.action === 'repair' ? `Repaired: ${(p.repairs ?? []).join(', ')}.` : p.action === 'block' ? `Blocked: ${(p.reasons ?? []).join(', ')}; a safe message replaces the answer.` : 'Passed every check.'}
        {p.restored ? ` ${p.restored} placeholder${p.restored === 1 ? '' : 's'} put back for this mode.` : ''}
      </Note>
      <Heading>Checks</Heading>
      {p.disabled ? (
        <Note>The output guardrails were disabled for this turn.</Note>
      ) : (
        <ul className="flex flex-col divide-y" data-testid="payload-checks">
          {(p.checks ?? []).map((v, i) => (
            <VerdictRow key={`${v.check}-${i}`} v={v} />
          ))}
        </ul>
      )}
    </div>
  )
}

// -- The tab ------------------------------------------------------------------------------------

/** One payload, rendered for its node. Pure: the tests render it to static markup. */
export function PayloadView({ payload, events = [], turnOver = true }: { payload: Payload; events?: HarnessEvent[]; turnOver?: boolean }) {
  return (
    <div className="flex flex-col gap-3" data-testid="payload-view" data-shape={payload.shape} data-at={payload.at}>
      {payload.at === 'enter' && (
        <Note testId="payload-at">
          What this node received, recorded before it ran{payload.shape === 'model_call' ? ': the request about to be sent to the model' : ''}. The full record lands when it finishes.
        </Note>
      )}
      {payload.shape === 'message' ? (
        <GatewayView p={payload} />
      ) : payload.shape === 'input_checks' ? (
        <InputGuardrailsView p={payload} />
      ) : payload.shape === 'context' ? (
        <ContextBuilderView p={payload} />
      ) : payload.shape === 'model_call' ? (
        <ModelLayerView p={payload} events={events} turnOver={turnOver} />
      ) : (
        <OutputGuardrailsView p={payload} />
      )}
    </div>
  )
}

/** The inspector's Payload tab: the latest payload of the node in this turn. */
export function PayloadTab({ event, events, turnOver }: { event: HarnessEvent; events: HarnessEvent[]; turnOver: boolean }) {
  const loaded = usePayload(event)
  if (loaded.status === 'loading' || loaded.status === 'idle') return <p className="text-xs text-muted-foreground">Loading the payload…</p>
  if (loaded.status === 'error') return <p className="text-xs text-destructive">Could not load the payload: {loaded.error}</p>
  if (!loaded.data) return <p className="text-xs text-muted-foreground">This event's detail has no payload record (it was stored before payloads were recorded).</p>
  return <PayloadView payload={loaded.data} events={events} turnOver={turnOver} />
}
