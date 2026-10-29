// Inspector views for the P3 payloads, picked by the shape of what GET /v1/nodes/{id} returns
// (never by node id): a hash chain with a Verify action, store records with their scope, a
// knowledge document list, memory writes as diffs, proposals at the review gate, and the
// context builder's per-slice budget.
import { useState } from 'react'
import { BookOpen, Check, ExternalLink, Link2, ShieldCheck, ShieldX, X } from 'lucide-react'
import type { AuditExport, Citation, MemoryProposal } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'
import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { cn } from '@/lib/utils'
import { useApp } from '@/state/store'
import { KeyValues } from './DataView'
import { isRecord } from './shapes'

function str(v: unknown): string | undefined {
  return typeof v === 'string' && v.length > 0 ? v : undefined
}
function num(v: unknown): number | undefined {
  return typeof v === 'number' && Number.isFinite(v) ? v : undefined
}
function text(v: unknown): string {
  if (v === null || v === undefined) return ''
  if (typeof v === 'string') return v
  return JSON.stringify(v)
}

const short = (h: unknown) => (typeof h === 'string' ? h.slice(0, 12) : '·')

// ---------- audit ----------

/** The hash chain, newest first, each record linked to the one before by its `prev_hash`. */
export function AuditView({ ins }: { ins: Record<string, unknown> }) {
  const api = useApp((s) => s.api)
  const [check, setCheck] = useState<{ state: 'idle' | 'running' | 'done' | 'error'; result?: AuditExport['verify']; error?: string }>({ state: 'idle' })
  const verify = isRecord(ins.verify) ? (ins.verify as AuditExport['verify']) : undefined
  const records = Array.isArray(ins.records) ? (ins.records as Record<string, unknown>[]) : []
  const exportUrl = str(ins.export)
  const shown = check.result ?? verify
  const run = async () => {
    if (!api) return
    setCheck({ state: 'running' })
    try {
      const out = await api.audit(1)
      setCheck({ state: 'done', result: out.verify })
    } catch (err) {
      setCheck({ state: 'error', error: err instanceof Error ? err.message : String(err) })
    }
  }
  return (
    <div className="flex flex-col gap-3" data-testid="audit-view">
      <div className="flex flex-wrap items-center gap-2">
        {shown && (
          <Badge
            variant="outline"
            className={cn('gap-1', shown.ok ? 'border-state-done/60 text-state-done' : 'border-state-error/60 text-state-error')}
            data-testid="audit-verify"
            data-ok={shown.ok ? 'true' : 'false'}
          >
            {shown.ok ? <ShieldCheck aria-hidden /> : <ShieldX aria-hidden />}
            {shown.ok ? `chain intact, ${shown.records} record${shown.records === 1 ? '' : 's'}` : `chain broken at ${shown.first_bad_record ?? '?'}`}
          </Badge>
        )}
        <Button size="xs" variant="outline" onClick={() => void run()} disabled={check.state === 'running' || !exportUrl} data-testid="audit-verify-button">
          <ShieldCheck aria-hidden /> {check.state === 'running' ? 'Verifying…' : 'Verify'}
        </Button>
        {exportUrl && (
          <Button size="xs" variant="ghost" asChild>
            <a href={`${api?.transport.baseUrl ?? ''}${exportUrl}`} target="_blank" rel="noreferrer" data-testid="audit-export">
              <ExternalLink aria-hidden /> /v1/audit
            </a>
          </Button>
        )}
        {check.state === 'done' && <span className="text-[11px] text-muted-foreground">verified the whole chain on the server just now</span>}
        {check.state === 'error' && <span className="text-[11px] text-destructive">{check.error}</span>}
      </div>
      <p className="text-[11px] text-muted-foreground">
        Each record's hash covers the previous record's hash, its id, time and entry, so editing or dropping one breaks every hash after it. Verify walks the whole chain of your tenant.
      </p>
      {records.length === 0 ? (
        <p className="text-xs text-muted-foreground">No audit records yet. A tool call that changes something, or one policy denies, writes one.</p>
      ) : (
        <ol className="flex flex-col gap-1.5" data-testid="audit-records">
          {records.map((r, i) => {
            const entry = isRecord(r.entry) ? r.entry : {}
            const older = records[i + 1]
            // Newest first: this record's prev_hash must be the next row's hash.
            const linked = older ? r.prev_hash === older.hash : true
            return (
              <li key={text(r.record_id)} className="rounded-md border p-2 text-[11px]" data-testid="audit-record" data-linked={linked ? 'true' : 'false'}>
                <div className="flex flex-wrap items-center gap-1.5">
                  <Badge variant="outline" className="rounded-sm px-1 py-0 text-[10px] font-normal">
                    {text(entry.action)}
                  </Badge>
                  {str(entry.tool) && <span className="font-mono">{text(entry.tool)}</span>}
                  {str(entry.decision) && <span className="text-muted-foreground">{text(entry.decision)}</span>}
                  {str(entry.actor) && <span className="text-muted-foreground">by {text(entry.actor)}</span>}
                  <span className="ml-auto text-muted-foreground">{text(r.ts).replace('T', ' ').slice(0, 19)}</span>
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-1 font-mono text-[10px] text-muted-foreground">
                  <span title={text(r.prev_hash)}>prev {short(r.prev_hash)}</span>
                  <Link2 className={cn('size-3', linked ? 'text-state-done' : 'text-state-error')} aria-label={linked ? 'linked to the record before' : 'not linked to the record before'} />
                  <span className="text-foreground" title={text(r.hash)}>
                    hash {short(r.hash)}
                  </span>
                  <span className="ml-auto">{text(r.record_id)}</span>
                </div>
              </li>
            )
          })}
        </ol>
      )}
    </div>
  )
}

// ---------- store records ----------

const PRIMARY = ['value', 'text', 'title', 'content']
const META = ['key', 'kind', 'outcome', 'version', 'updated', 'ts', 'created']

/** Store records as a list: the scope tag first, then the record's text and a line of metadata. */
export function RecordList({ rows }: { rows: Record<string, unknown>[] }) {
  if (rows.length === 0) return <p className="text-xs text-muted-foreground">No records.</p>
  return (
    <ul className="flex flex-col gap-1.5" data-testid="store-records">
      {rows.map((r, i) => {
        const primary = PRIMARY.map((k) => r[k]).find((v) => v !== undefined && v !== null)
        const meta = META.filter((k) => r[k] !== undefined && r[k] !== null).map((k) => `${k} ${text(r[k])}`)
        const scope = text(r.scope)
        return (
          <li key={text(r.id) || i} className="rounded-md border p-2 text-[11px]" data-testid="store-record" data-scope={scope}>
            <div className="flex items-start gap-1.5">
              <Badge
                variant="outline"
                className={cn('shrink-0 rounded-sm px-1 py-0 text-[10px] font-normal', scope === 'pack' ? 'text-muted-foreground' : 'border-kind-store/60 text-foreground')}
                title={scope === 'pack' ? 'Seeded by the dataset pack: every tenant reads it, none can write it' : 'Written for your tenant'}
                data-testid="scope-tag"
              >
                {scope}
              </Badge>
              <span className="min-w-0 leading-snug break-words">{text(primary)}</span>
            </div>
            {meta.length > 0 && <p className="mt-1 truncate font-mono text-[10px] text-muted-foreground">{meta.join(' · ')}</p>}
          </li>
        )
      })}
    </ul>
  )
}

/** The knowledge documents the caller can retrieve from, each opening in the reader. */
export function DocList({ docs }: { docs: Record<string, unknown>[] }) {
  const openDoc = useApp((s) => s.openDoc)
  return (
    <div className="rounded-md border" data-testid="doc-list">
      <Table className="text-[11px]">
        <TableCaption className="sr-only">Knowledge documents</TableCaption>
        <TableHeader className="bg-muted/60">
          <TableRow>
            <TableHead className="h-7 px-2">document</TableHead>
            <TableHead className="h-7 px-2">chunks</TableHead>
            <TableHead className="h-7 px-2">scope</TableHead>
            <TableHead className="h-7 px-2">
              <span className="sr-only">Open</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {docs.map((d) => (
            <TableRow key={text(d.doc_id)} data-testid="doc-row" data-doc={text(d.doc_id)}>
              <TableCell className="max-w-48 truncate px-2 py-1" title={text(d.path)}>
                {text(d.title) || text(d.doc_id)}
                <span className="ml-1 font-mono text-muted-foreground">{text(d.kind)}</span>
              </TableCell>
              <TableCell className="px-2 py-1 font-mono">{text(d.chunks)}</TableCell>
              <TableCell className="px-2 py-1">
                <Badge variant="outline" className="rounded-sm px-1 py-0 text-[10px] font-normal">
                  {text(d.scope)}
                </Badge>
              </TableCell>
              <TableCell className="px-2 py-1 text-right">
                <Button size="xs" variant="ghost" onClick={() => openDoc(text(d.doc_id))} aria-label={`Open ${text(d.title) || text(d.doc_id)}`}>
                  <BookOpen aria-hidden /> Open
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

// ---------- memory writes ----------

function lines(v: unknown): string[] {
  return v === null || v === undefined ? [] : text(v).split('\n')
}

/** before -> after as removed and added lines, coloured by the state tokens. */
export function Diff({ before, after }: { before: unknown; after: unknown }) {
  const old = lines(before)
  const neu = lines(after)
  return (
    <pre className="overflow-x-auto rounded-md border bg-muted/40 p-1.5 font-mono text-[10.5px] leading-relaxed whitespace-pre-wrap" data-testid="memory-diff">
      {old.map((l, i) => (
        <div key={`-${i}`} className="bg-state-error/10 text-foreground" data-op="remove">
          <span className="text-state-error select-none">- </span>
          {l}
        </div>
      ))}
      {neu.map((l, i) => (
        <div key={`+${i}`} className="bg-state-done/10 text-foreground" data-op="add">
          <span className="text-state-done select-none">+ </span>
          {l}
        </div>
      ))}
    </pre>
  )
}

const STATUS_TONE: Record<string, string> = {
  accepted: 'border-state-done/60 text-state-done',
  rejected: 'border-state-error/60 text-state-error',
  pending: 'border-state-waiting/60 text-state-waiting',
}

function ProposalHead({ p }: { p: MemoryProposal }) {
  return (
    <CardHeader className="flex flex-wrap items-center gap-1.5 px-2.5">
      <Badge variant="kind" data-kind="store" className="rounded-sm px-1 py-0 text-[10px] font-normal">
        {p.store}
      </Badge>
      <Badge variant="outline" className={cn('rounded-sm px-1 py-0 text-[10px] font-normal', STATUS_TONE[p.status])} data-testid="proposal-status">
        {p.status}
      </Badge>
      {p.gate && <span className="text-[10px] text-muted-foreground">gate {p.gate}</span>}
      {p.source && <span className="text-[10px] text-muted-foreground">from {p.source}</span>}
      <CardTitle className="ml-auto font-mono text-[10px] font-normal text-muted-foreground">{p.proposal_id}</CardTitle>
    </CardHeader>
  )
}

function contentText(c: unknown): string {
  if (isRecord(c)) {
    if (typeof c.key === 'string' && 'value' in c) return `${c.key}: ${text(c.value)}`
    if ('text' in c) return text(c.text)
  }
  return text(c)
}

/** The writer's last proposals: writes as a diff of the record, rejections with their reason. */
export function WritesView({ writes }: { writes: MemoryProposal[] }) {
  if (writes.length === 0) return <p className="text-xs text-muted-foreground">No proposals yet. The writer runs after the reply when the loop asked to remember something, and records each turn as an episode.</p>
  return (
    <div className="flex flex-col gap-2" data-testid="memory-writes">
      {writes.map((p) => (
        <Card key={p.proposal_id} className="gap-1.5 py-2 shadow-none" data-testid="memory-write" data-status={p.status} data-store={p.store}>
          <ProposalHead p={p} />
          <CardContent className="flex flex-col gap-1 px-2.5 text-[11px]">
            {p.diff ? (
              <>
                <span className="text-[10px] text-muted-foreground">
                  {p.diff.op} {p.record_id ?? ''}
                  {p.version ? ` · v${p.version}` : ''}
                </span>
                <Diff before={p.diff.before} after={p.diff.after} />
              </>
            ) : (
              <span className="break-words">{contentText(p.content)}</span>
            )}
            {p.reason && <span className="text-[10px] text-muted-foreground">reason: {p.reason}</span>}
          </CardContent>
        </Card>
      ))}
    </div>
  )
}

/** Proposals waiting at the review gate, with Accept and Reject when this mode allows review. */
export function PendingView({ pending }: { pending: MemoryProposal[] }) {
  const review = useApp((s) => s.capabilities?.features.memory_review === true)
  const decideProposal = useApp((s) => s.decideProposal)
  const [busy, setBusy] = useState<string | null>(null)
  if (pending.length === 0) return <p className="text-xs text-muted-foreground">Nothing waiting at the review gate.</p>
  const decide = async (id: string, decision: 'accept' | 'reject') => {
    setBusy(id)
    await decideProposal(id, decision)
    setBusy(null)
  }
  return (
    <div className="flex flex-col gap-2" data-testid="memory-pending">
      {!review && <p className="text-[11px] text-muted-foreground">Review is off in this mode: nothing here can be accepted.</p>}
      {pending.map((p) => (
        <Card key={p.proposal_id} className="gap-1.5 py-2 shadow-none" data-testid="pending-proposal" data-store={p.store}>
          <ProposalHead p={p} />
          <CardContent className="flex flex-col gap-1.5 px-2.5 text-[11px]">
            <span className="break-words">{contentText(p.content)}</span>
            {review && (
              <div className="flex gap-1.5">
                <Button size="xs" onClick={() => void decide(p.proposal_id, 'accept')} disabled={busy !== null} aria-label={`Accept proposal ${p.proposal_id}`}>
                  <Check aria-hidden /> Accept
                </Button>
                <Button size="xs" variant="outline" onClick={() => void decide(p.proposal_id, 'reject')} disabled={busy !== null} aria-label={`Reject proposal ${p.proposal_id}`}>
                  <X aria-hidden /> Reject
                </Button>
              </div>
            )}
          </CardContent>
        </Card>
      ))}
    </div>
  )
}

// ---------- context budget ----------

/**
 * The context builder's budget, from a summary with `slices` (tokens per slice): one bar for
 * the whole budget, then one per slice as its share of what was used, with the items retrieved
 * and trimmed. Every slice the summary names is listed, empty ones too; the slices that hold
 * retrieved items (the `retrieved` keys, which are the retrieval stores) take the store colour.
 */
export function ContextBudget({ summary }: { summary: Record<string, unknown> }) {
  const slices = isRecord(summary.slices) ? (summary.slices as Record<string, number>) : {}
  const retrieved = isRecord(summary.retrieved) ? (summary.retrieved as Record<string, number>) : {}
  const trimmed = isRecord(summary.trimmed) ? (summary.trimmed as Record<string, number>) : {}
  const degraded = Array.isArray(summary.degraded) ? summary.degraded.map(text) : []
  const budget = num(summary.budget_tokens)
  const used = num(summary.used_tokens) ?? Object.values(slices).reduce((a, b) => a + (typeof b === 'number' ? b : 0), 0)
  const names = [...new Set([...Object.keys(slices), ...Object.keys(retrieved)])]
  const citations = num(summary.citations) ?? (Array.isArray(summary.citations) ? summary.citations.length : undefined)
  return (
    <section className="flex flex-col gap-2" aria-label="Context budget" data-testid="context-budget">
      <h4 className="text-xs font-semibold text-muted-foreground">Context budget</h4>
      {budget !== undefined && (
        <div className="flex flex-col gap-1">
          <div className="flex justify-between text-[11px]">
            <span>used</span>
            <span className="font-mono">
              {used} / {budget} tok ({Math.round((used / budget) * 100)}%)
            </span>
          </div>
          <Progress value={Math.min(100, (used / budget) * 100)} aria-label={`Context used ${used} of ${budget} tokens`} className="h-1.5" />
        </div>
      )}
      <div className="flex flex-col gap-1.5">
        {names.map((name) => {
          const t = typeof slices[name] === 'number' ? slices[name] : 0
          const pct = used > 0 ? (t / used) * 100 : 0
          const isStore = name in retrieved
          return (
            <div key={name} className="flex flex-col gap-0.5" data-testid="context-slice" data-slice={name} data-tokens={t}>
              <div className="flex items-baseline gap-1.5 text-[11px]">
                <span className={cn('font-medium', !t && 'text-muted-foreground')}>{name}</span>
                {retrieved[name] !== undefined && <span className="text-muted-foreground">{retrieved[name]} retrieved</span>}
                {trimmed[name] ? (
                  <Badge variant="outline" className="rounded-sm border-state-waiting/60 px-1 py-0 text-[10px] font-normal text-state-waiting" data-testid="slice-trimmed">
                    {trimmed[name]} trimmed
                  </Badge>
                ) : null}
                {degraded.includes(name) && (
                  <Badge variant="outline" className="rounded-sm border-state-error/60 px-1 py-0 text-[10px] font-normal text-state-error" data-testid="slice-degraded">
                    degraded
                  </Badge>
                )}
                <span className="ml-auto font-mono text-muted-foreground">{t} tok</span>
              </div>
              <Progress
                value={pct}
                aria-label={`${name}: ${t} tokens, ${Math.round(pct)} percent of the context`}
                className={cn('h-1', isStore ? 'bg-kind-store/15 [&>[data-slot=progress-indicator]]:bg-kind-store' : 'bg-muted [&>[data-slot=progress-indicator]]:bg-muted-foreground/60')}
              />
            </div>
          )
        })}
      </div>
      <KeyValues
        value={{
          ...(citations !== undefined ? { citations: `${citations} numbered knowledge chunk${citations === 1 ? '' : 's'}` } : {}),
          ...(Object.keys(trimmed).length ? { trimmed: Object.entries(trimmed).map(([k, v]) => `${k} ${v}`).join(', ') } : { trimmed: 'nothing' }),
          ...(degraded.length ? { degraded: degraded.join(', ') } : { degraded: 'none' }),
          ...(summary.compacted !== undefined ? { compacted: String(summary.compacted) } : {}),
          ...(num(summary.stable_prefix_tokens) !== undefined ? { 'stable prefix': `${summary.stable_prefix_tokens} tok` } : {}),
        }}
      />
    </section>
  )
}

/** The chunks numbered for the last context, opening in the reader. */
export function CitationList({ citations }: { citations: (Citation & { text?: string })[] }) {
  const openDoc = useApp((s) => s.openDoc)
  if (citations.length === 0) return <p className="text-xs text-muted-foreground">No knowledge chunks in the last context.</p>
  return (
    <ol className="flex flex-col gap-1" data-testid="context-citations">
      {citations.map((c) => (
        <li key={c.n} className="flex items-center gap-1.5 text-[11px]">
          <span className="inline-flex h-4 min-w-4 items-center justify-center rounded-sm bg-kind-store/15 px-1 font-mono text-[10px] ring-1 ring-kind-store/40 ring-inset">{c.n}</span>
          <span className="min-w-0 truncate" title={c.text}>
            {c.citation}
          </span>
          <Button size="xs" variant="ghost" className="ml-auto" onClick={() => openDoc(c.doc, c.chunk)} aria-label={`Open ${c.citation}`}>
            <BookOpen aria-hidden />
          </Button>
        </li>
      ))}
    </ol>
  )
}
