// The Trace node's panel: query the stored turns (kind, session, node, status, since), each with
// its receipt totals; a turn opens to its per-node receipt (tokens, ms, cost), its model calls and
// replay. Eval case turns (kind eval) replay from their case's events_url.
import { useState } from 'react'
import { Play, Receipt as ReceiptIcon, Search } from 'lucide-react'
import type { TraceQuery, TraceTurn } from '@/api/types'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import {
  Trace,
  TraceCollapsible,
  TraceCollapsibleContent,
  TraceCollapsibleTrigger,
  TraceDescription,
  TraceIcon,
  TraceLine,
  TraceTitle,
} from '@/components/ui/trace'
import { formatMs, formatTokens } from '@/diagram/stats'
import { useApp } from '@/state/store'
import { receiptLine, tokens, usd } from './opsView'
import { useLoad } from './useOps'

const ANY = '__any__'
const SINCE: { value: string; label: string; ms: number | null }[] = [
  { value: ANY, label: 'any time', ms: null },
  { value: '15m', label: 'last 15 minutes', ms: 15 * 60_000 },
  { value: '1h', label: 'last hour', ms: 3600_000 },
  { value: '1d', label: 'last day', ms: 86_400_000 },
]
const STATUSES = ['running', 'paused', 'done']
const KINDS: { value: string; label: string }[] = [
  { value: 'chat', label: 'chat turns' },
  { value: 'eval', label: 'eval case turns' },
]


function when(iso: string): string {
  const d = new Date(iso)
  return Number.isFinite(d.getTime()) ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : iso
}

function ReceiptView({ turnId }: { turnId: string }) {
  const api = useApp((s) => s.api)
  const diagram = useApp((s) => s.diagram)
  const label = (id: string) => diagram?.nodes.find((n) => n.id === id)?.label ?? id
  const receipt = useLoad(api ? `receipt:${turnId}` : null, () => api!.receipt(turnId))
  if (receipt.status === 'error') return <p className="text-xs text-destructive">No receipt: {receipt.error}</p>
  if (!receipt.data) return <p className="text-xs">Loading the receipt…</p>
  const r = receipt.data
  const nodes = [...new Set([...Object.keys(r.latency_ms ?? {}), ...Object.keys(r.by_node ?? {})])].sort(
    (a, b) => tokens(r.by_node[b]) - tokens(r.by_node[a]) || (r.latency_ms[b] ?? 0) - (r.latency_ms[a] ?? 0),
  )
  return (
    <div className="flex flex-col gap-3" data-testid="trace-receipt">
      <div className="rounded-xl ring-1 ring-foreground/5">
        <Table className="text-xs">
          <TableCaption className="sr-only">Receipt per node</TableCaption>
          <TableHeader>
            <TableRow>
              <TableHead scope="col" className="h-7 px-2">
                Node
              </TableHead>
              <TableHead scope="col" className="h-7 px-2 text-right">
                Tokens
              </TableHead>
              <TableHead scope="col" className="h-7 px-2 text-right">
                Time
              </TableHead>
              <TableHead scope="col" className="h-7 px-2 text-right">
                Cost
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {nodes.map((id) => (
              <TableRow key={id} data-testid="receipt-row" data-node={id}>
                <TableCell className="px-2 py-1 text-foreground">{label(id)}</TableCell>
                <TableCell className="px-2 py-1 text-right font-mono tabular-nums">{r.by_node[id] ? formatTokens(tokens(r.by_node[id])) : '·'}</TableCell>
                <TableCell className="px-2 py-1 text-right font-mono tabular-nums">{formatMs(r.latency_ms[id]) ?? '·'}</TableCell>
                <TableCell className="px-2 py-1 text-right font-mono tabular-nums">{r.by_node[id] ? usd(r.by_node[id]) : '·'}</TableCell>
              </TableRow>
            ))}
            <TableRow>
              <TableCell className="px-2 py-1 font-medium text-foreground">Total</TableCell>
              <TableCell className="px-2 py-1 text-right font-mono tabular-nums">{formatTokens(tokens(r.total))}</TableCell>
              <TableCell className="px-2 py-1" />
              <TableCell className="px-2 py-1 text-right font-mono tabular-nums">{usd(r.total)}</TableCell>
            </TableRow>
          </TableBody>
        </Table>
      </div>
      {Object.keys(r.by_model ?? {}).length > 0 && (
        <ul className="flex flex-col gap-0.5 text-xs" aria-label="By model">
          {Object.entries(r.by_model).map(([model, c]) => (
            <li key={model} className="tabular-nums">
              <span className="font-mono text-foreground">{model}</span>: {c.input_tokens} in, {c.output_tokens} out, {usd(c)}
            </li>
          ))}
        </ul>
      )}
      {(r.calls ?? []).length > 0 && (
        <ul className="flex flex-col gap-0.5 text-xs" aria-label="Model calls" data-testid="receipt-calls">
          {r.calls!.map((c) => (
            <li key={c.seq} className="tabular-nums">
              Step {c.step}: <span className="font-mono text-foreground">{c.model}</span> on {c.provider}, {c.input_tokens} in, {c.output_tokens} out, {usd(c)}
              {formatMs(c.ms) ? `, ${formatMs(c.ms)}` : ''}
              {c.fallback ? ', fallback' : ''}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** The Trace node: a query over stored turns, a turn list with receipts, replay. */
export function TracePanel() {
  const api = useApp((s) => s.api)
  const diagram = useApp((s) => s.diagram)
  const sessionId = useApp((s) => s.sessionId)
  const version = useApp((s) => s.inspectorVersion)
  const enterReplay = useApp((s) => s.enterReplay)
  const [form, setForm] = useState({ kind: 'chat', session: sessionId ?? '', node: ANY, status: ANY, since: ANY })
  const [query, setQuery] = useState<TraceQuery>(() => ({ kind: 'chat', ...(sessionId ? { session: sessionId } : {}), limit: 20 }))
  const [pages, setPages] = useState<{ after: string; turns: TraceTurn[]; next_after: string | null }[]>([])
  const key = api ? `trace:${JSON.stringify(query)}:${version}` : null
  const first = useLoad(key, () => api!.traceTurns(query))
  const label = (id: string) => diagram?.nodes.find((n) => n.id === id)?.label ?? id

  const search = () => {
    const since = SINCE.find((s) => s.value === form.since)?.ms
    setPages([])
    setQuery({
      kind: form.kind === 'eval' ? 'eval' : 'chat',
      ...(form.session.trim() ? { session: form.session.trim() } : {}),
      ...(form.node !== ANY ? { node: form.node } : {}),
      ...(form.status !== ANY ? { status: form.status } : {}),
      ...(since ? { since: new Date(Date.now() - since).toISOString() } : {}),
      limit: 20,
    })
  }
  const next = pages.length ? pages[pages.length - 1]!.next_after : (first.data?.next_after ?? null)
  const more = async () => {
    if (!api || !next) return
    const page = await api.traceTurns({ ...query, after: next })
    setPages((p) => [...p, { after: next, ...page }])
  }
  const turns = [...(first.data?.turns ?? []), ...pages.flatMap((p) => p.turns)]

  return (
    <div className="flex flex-col gap-4" data-testid="trace-panel">
      <form
        className="grid grid-cols-2 gap-2"
        aria-label="Query stored turns"
        onSubmit={(e) => {
          e.preventDefault()
          search()
        }}
      >
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          Kind
          <Select value={form.kind} onValueChange={(kind) => setForm({ ...form, kind, ...(kind === 'eval' ? { session: '' } : {}) })}>
            <SelectTrigger size="sm" aria-label="Kind" data-testid="trace-kind">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {KINDS.map((k) => (
                <SelectItem key={k.value} value={k.value}>
                  {k.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          Session
          <Input value={form.session} onChange={(e) => setForm({ ...form, session: e.target.value })} placeholder="any session" className="h-7 font-mono text-xs" data-testid="trace-session" />
        </label>
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          Node
          <Select value={form.node} onValueChange={(node) => setForm({ ...form, node })}>
            <SelectTrigger size="sm" aria-label="Node" data-testid="trace-node">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>any node</SelectItem>
              {(diagram?.nodes ?? []).map((n) => (
                <SelectItem key={n.id} value={n.id}>
                  {n.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          Status
          <Select value={form.status} onValueChange={(status) => setForm({ ...form, status })}>
            <SelectTrigger size="sm" aria-label="Status">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>any status</SelectItem>
              {STATUSES.map((s) => (
                <SelectItem key={s} value={s}>
                  {s}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          Since
          <Select value={form.since} onValueChange={(since) => setForm({ ...form, since })}>
            <SelectTrigger size="sm" aria-label="Since">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {SINCE.map((s) => (
                <SelectItem key={s.value} value={s.value}>
                  {s.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
        <Button type="submit" size="sm" variant="outline" className="self-end" data-testid="trace-search">
          <Search aria-hidden /> Search
        </Button>
      </form>
      <section className="flex flex-col gap-1.5" aria-label="Turns">
        <h4 className="text-xs font-medium text-muted-foreground">
          {first.data ? `${turns.length}${next ? '+' : ''} turn${turns.length === 1 ? '' : 's'}, newest first` : 'Turns'}
        </h4>
        {first.status === 'error' && <p className="text-xs text-destructive">Could not query the trace: {first.error}</p>}
        {first.data && turns.length === 0 && <p className="text-xs text-muted-foreground">No stored turn matches.</p>}
        <Trace role="list" data-testid="trace-turns">
          {turns.map((t) => (
            <TraceCollapsible key={t.turn_id} role="listitem">
              <TraceCollapsibleTrigger>
                <TraceLine status={t.status === 'running' || t.status === 'paused' ? 'running' : t.exit === 'error' ? 'error' : undefined} data-testid="trace-turn" data-turn={t.turn_id}>
                  <TraceIcon>
                    <ReceiptIcon />
                  </TraceIcon>
                  <TraceTitle className="shrink-0">
                    {t.case_id ? `${t.case_id}: ` : ''}
                    {t.exit ?? t.status} · {when(t.started_at)}
                  </TraceTitle>
                  <TraceDescription className="min-w-0 shrink truncate">{receiptLine(t)}</TraceDescription>
                </TraceLine>
              </TraceCollapsibleTrigger>
              <TraceCollapsibleContent className="flex flex-col gap-3">
                <p className="font-mono text-xs break-all">
                  {t.turn_id}
                  {t.run_id ? ` · ${t.run_id}` : ''}
                </p>
                {t.path && t.path.length > 0 && <p className="text-xs leading-relaxed">{t.path.map(label).join(' → ')}</p>}
                <ReceiptView turnId={t.turn_id} />
                <Button
                  size="xs"
                  variant="outline"
                  className="self-start"
                  onClick={() => void enterReplay(t.turn_id, t.events_url ? { eventsUrl: t.events_url, label: `Eval case ${t.case_id ?? t.turn_id}` } : undefined)}
                  data-testid="trace-replay"
                >
                  <Play aria-hidden /> Open in replay
                </Button>
              </TraceCollapsibleContent>
            </TraceCollapsible>
          ))}
        </Trace>
        {next && (
          <Button size="xs" variant="ghost" className="self-start" onClick={() => void more()}>
            Load more
          </Button>
        )}
      </section>
    </div>
  )
}
