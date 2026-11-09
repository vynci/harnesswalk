// The Tune tab of the Release panel (0.2.0, spec/contracts/tuning.md): the datasets curate made,
// the training runs with their loss curve (live from the run's stream), the model gate's
// comparisons (pack x arm, the verdict, the prompt arm, the tool_calls split) and the canary
// with Promote, Promote full and Rollback. A public server, or any recorded replay, shows the
// recorded run with every action off: the sandbox never trains.
import { useEffect, useMemo, useState } from 'react'
import { ArrowUpRight, Database, FlaskConical, GitCompare, History, LoaderCircle, Play, Rocket, ShieldCheck, ShieldX, Undo2 } from 'lucide-react'
import { ApiError } from '@/api/client'
import { openTuneStream } from '@/api/stream'
import type {
  CanaryState,
  Comparison,
  Curation,
  DatasetManifest,
  OpsEvent,
  TuneArtifactRef,
  TuneOverview,
  TuneRefusal,
  TuneRun,
} from '@/api/types'
import { ConfirmItem } from '@/components/confirm-item'
import { DiffView } from '@/components/diff-view'
import { Hint } from '@/components/hint'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { Trace, TraceDescription, TraceIcon, TraceLine, TraceTitle } from '@/components/ui/trace'
import { parseUnifiedDiff } from '@/lib/diff'
import { cn } from '@/lib/utils'
import { useApp } from '@/state/store'
import { KeyValues } from './DataView'
import {
  ARM_HINTS,
  ARM_LABELS,
  armText,
  canaryRuleText,
  chartGeometry,
  compareRows,
  comparisonArms,
  lossSeries,
  num,
  parseFraction,
  promptArmText,
  refText,
  REPLAY_NOTE,
  rulePacks,
  runSteps,
  stepText,
  toolSplitRows,
  tuneBlockReason,
  verdictText,
  type LossSeries,
} from './tuneView'
import { TUNE_SECTIONS, useTuneUi, type TuneSection } from './tuneUi'
import { useLoad } from './useOps'

const short = (h: string | null | undefined) => (h ? h.slice(0, 10) : '·')

function when(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  return Number.isFinite(d.getTime()) ? d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : iso
}

const message = (err: unknown) => (err instanceof Error ? err.message : String(err))
const errorText = (e: unknown) => (typeof e === 'string' ? e : e && typeof e === 'object' && 'message' in e ? String((e as { message: unknown }).message) : '')

/** What every section needs: whether actions are on, and a way to read everything again. */
interface Ctx {
  blocked: string | null
  replay: boolean
  overview: TuneOverview | null
  /** Read every tune list again (after an action this browser took). */
  refresh: () => void
  tick: number
}

function Section({ title, children, testId }: { title: string; children: React.ReactNode; testId?: string }) {
  return (
    <section className="flex flex-col gap-1.5" aria-label={title} data-testid={testId}>
      <h4 className="text-xs font-medium text-muted-foreground">{title}</h4>
      {children}
    </section>
  )
}

function StatusBadge({ status }: { status: string | null | undefined }) {
  if (!status) return null
  const bad = status === 'failed' || status === 'error'
  const good = status === 'passed' || status === 'done'
  return (
    <Badge variant={bad ? 'destructive' : good ? 'secondary' : 'outline'} className={cn('h-5 shrink-0 font-normal', good && 'text-state-done')} data-status={status}>
      {status === 'running' && <LoaderCircle className="animate-spin motion-reduce:animate-none" aria-hidden />}
      {status}
    </Badge>
  )
}

/** A row of a list that selects one item. */
function PickRow({ selected, onPick, children, testId, id }: { selected: boolean; onPick: () => void; children: React.ReactNode; testId: string; id: string }) {
  return (
    <div role="listitem">
      <button
        type="button"
        className="flex w-fit max-w-full min-w-0 rounded-sm text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/30"
        onClick={onPick}
        aria-current={selected ? 'true' : undefined}
        data-testid={testId}
        data-id={id}
      >
        {children}
      </button>
    </div>
  )
}

/** An action button that is off (aria-disabled, with the reason) while `blocked`. */
function ActionButton({
  blocked,
  hint,
  busy,
  onClick,
  children,
  variant = 'default',
  testId,
  type = 'button',
}: {
  blocked: string | null
  hint: string
  busy?: boolean
  onClick?: () => void
  children: React.ReactNode
  variant?: 'default' | 'outline' | 'destructive'
  testId?: string
  type?: 'button' | 'submit'
}) {
  return (
    <Hint label={blocked ?? hint}>
      <Button
        type={type}
        size="sm"
        variant={variant}
        aria-disabled={blocked || busy ? true : undefined}
        onClick={(e) => {
          if (blocked || busy) {
            e.preventDefault()
            return
          }
          onClick?.()
        }}
        data-testid={testId}
      >
        {busy ? <LoaderCircle className="animate-spin motion-reduce:animate-none" aria-hidden /> : null}
        {children}
      </Button>
    </Hint>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex min-w-0 flex-col gap-1 text-xs text-muted-foreground">
      <span>{label}</span>
      {children}
    </label>
  )
}

const csv = (text: string) =>
  text
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)

// ---------- datasets ----------

function DatasetDetail({ name, ctx }: { name: string; ctx: Ctx }) {
  const api = useApp((s) => s.api)
  const v = useApp((s) => s.inspectorVersion)
  const [version, setVersion] = useState<number | undefined>(undefined)
  const detail = useLoad(api ? `tune-dataset:${name}:${version ?? 'latest'}:${v}:${ctx.tick}` : null, () => api!.tuneArtifact('dataset', name, version))
  if (detail.status === 'error') return <p className="text-xs text-destructive">Could not load dataset/{name}: {detail.error}</p>
  if (!detail.data) return <p className="text-xs text-muted-foreground">Loading dataset/{name}…</p>
  const d = detail.data
  const m = d.manifest as DatasetManifest
  const c = m.counts ?? ({} as DatasetManifest['counts'])
  const diff = d.diff_from_previous ? parseUnifiedDiff(d.diff_from_previous) : []
  return (
    <section className="flex flex-col gap-3" aria-label={`Dataset ${name}`} data-testid="tune-dataset-detail" data-name={name} data-version={d.version}>
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="text-sm font-medium">
          dataset/{d.name} v{d.version}
        </h4>
        {m.synthetic && (
          <Badge variant="outline" className="h-5 font-normal">
            synthetic
          </Badge>
        )}
        <span className="text-xs text-muted-foreground tabular-nums">{when(d.updated_at)}</span>
      </div>
      <p className="text-xs text-muted-foreground">{m.source}</p>
      <KeyValues
        value={{
          teacher: `${m.teacher}${m.teacher_model && m.teacher_model !== m.teacher ? ` (${m.teacher_model})` : ''}`,
          'teacher prompt arm': m.teacher_prompt_arm ? 'on (context distillation: the examples keep the pack prompt)' : 'off',
          packs: (m.packs ?? []).join(', '),
          scenarios: `${m.scenarios?.golden ?? 0} golden + ${m.scenarios?.paraphrase ?? 0} rewrites (${m.scenarios?.variants ?? 0} per case, seed ${m.scenarios?.seed ?? '·'})`,
        }}
      />
      <div className="grid grid-cols-3 gap-2 text-center sm:grid-cols-6" data-testid="tune-dataset-counts">
        {(
          [
            ['turns', c.turns],
            ['kept', c.kept],
            ['dropped', Object.values(c.dropped ?? {}).reduce((a, b) => a + b, 0)],
            ['deduplicated', c.deduplicated],
            ['train', c.train],
            ['validation', c.val],
          ] as const
        ).map(([label, value]) => (
          <div key={label} className="rounded-md p-1.5 ring-1 ring-foreground/10">
            <div className="text-sm font-medium tabular-nums">{value ?? 0}</div>
            <div className="text-[11px] text-muted-foreground">{label}</div>
          </div>
        ))}
      </div>
      {Object.keys(c.dropped ?? {}).length > 0 && (
        <p className="text-xs text-muted-foreground">
          Dropped by first failing scorer:{' '}
          {Object.entries(c.dropped)
            .map(([k, n]) => `${k} ${n}`)
            .join(', ')}
          .
        </p>
      )}
      {Object.keys(c.by_pack ?? {}).length > 0 && (
        <p className="text-xs text-muted-foreground" data-testid="tune-dataset-by-pack">
          Examples by pack:{' '}
          {Object.entries(c.by_pack)
            .map(([k, n]) => `${k} ${n}`)
            .join(', ')}
          .
        </p>
      )}
      <Section title="Filters">
        <ul className="list-disc pl-5 text-xs">
          {(m.filters ?? []).map((f) => (
            <li key={f}>{f}</li>
          ))}
        </ul>
      </Section>
      <Section title="Files (under tune.artifacts_dir, never in git; named here by hash)">
        <ul className="flex flex-col gap-0.5 font-mono text-xs break-all text-muted-foreground">
          {Object.entries(m.files ?? {}).map(([k, f]) => (
            <li key={k}>
              {k}: {f.path} · sha256 {short(f.sha256)}
              {typeof f.bytes === 'number' ? ` · ${f.bytes} bytes` : ''}
            </li>
          ))}
        </ul>
      </Section>
      {m.regenerate && (
        <Section title="Rebuild it">
          <code className="rounded-md bg-muted px-2 py-1 font-mono text-xs break-all">{m.regenerate}</code>
        </Section>
      )}
      <Section title="History">
        <ul className="flex flex-wrap gap-1.5">
          {d.history.map((h) => (
            <li key={h.version}>
              <Button size="xs" variant={h.version === d.version ? 'secondary' : 'outline'} onClick={() => setVersion(h.version)} title={h.hash} aria-current={h.version === d.version ? 'true' : undefined}>
                v{h.version}
                {h.note ? `: ${h.note}` : ''}
              </Button>
            </li>
          ))}
        </ul>
      </Section>
      {diff.length > 0 && (
        <Section title="Changes from the version before">
          <DiffView lines={diff} label={`Diff of dataset/${d.name}`} />
        </Section>
      )}
    </section>
  )
}

function CurateForm({ ctx, onStarted }: { ctx: Ctx; onStarted: () => void }) {
  const api = useApp((s) => s.api)
  const sessionPack = useApp((s) => s.pack)
  const [name, setName] = useState('approvals')
  const [packs, setPacks] = useState(sessionPack ?? '')
  const [teacher, setTeacher] = useState(ctx.overview?.teacher ?? '')
  const [variants, setVariants] = useState('2')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const start = async () => {
    if (!api) return
    setBusy(true)
    setError(null)
    try {
      await api.startCuration({ name: name.trim(), packs: csv(packs), ...(teacher.trim() ? { teacher: teacher.trim() } : {}), ...(Number(variants) >= 0 ? { variants: Number(variants) } : {}) })
      onStarted()
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }
  return (
    <form
      className="flex flex-wrap items-end gap-2"
      aria-label="Curate a dataset"
      onSubmit={(e) => {
        e.preventDefault()
        if (!ctx.blocked && !busy) void start()
      }}
    >
      <Field label="Name">
        <Input value={name} onChange={(e) => setName(e.target.value)} className="h-7 w-28 text-xs" aria-label="Dataset name" />
      </Field>
      <Field label="Packs">
        <Input value={packs} onChange={(e) => setPacks(e.target.value)} className="h-7 w-36 text-xs" aria-label="Packs to curate from, comma separated" />
      </Field>
      <Field label="Teacher">
        <Input value={teacher} onChange={(e) => setTeacher(e.target.value)} className="h-7 w-28 text-xs" aria-label="Teacher model alias" />
      </Field>
      <Field label="Rewrites">
        <Input value={variants} onChange={(e) => setVariants(e.target.value)} className="h-7 w-14 text-xs" inputMode="numeric" aria-label="Teacher rewrites per golden case" />
      </Field>
      <ActionButton type="submit" blocked={ctx.blocked} busy={busy} hint="Run the teacher on the packs' golden cases and rewrites, keep the turns that pass, export them" testId="tune-curate-start">
        <Database aria-hidden /> Curate
      </ActionButton>
      {error && (
        <p className="basis-full text-xs text-destructive" role="alert">
          {error}
        </p>
      )}
    </form>
  )
}

function DatasetsSection({ ctx }: { ctx: Ctx }) {
  const api = useApp((s) => s.api)
  const v = useApp((s) => s.inspectorVersion)
  const list = useLoad(api ? `tune-artifacts:dataset:${v}:${ctx.tick}` : null, () => api!.tuneArtifacts('dataset'))
  const curations = useLoad(api ? `tune-curations:${v}:${ctx.tick}` : null, () => api!.curations())
  const [chosen, setChosen] = useState<string | null>(null)
  const items = list.data?.items ?? []
  const selected = (chosen && items.some((d) => d.name === chosen) ? chosen : items[0]?.name) ?? null
  return (
    <div className="flex flex-col gap-4" data-testid="tune-datasets">
      <Section title="Datasets (dataset/<name>), latest version">
        {list.status === 'error' && <p className="text-xs text-destructive">Could not list datasets: {list.error}</p>}
        {list.status === 'ok' && items.length === 0 && <p className="text-xs text-muted-foreground">No dataset yet. Curate one: harnesswalk tune curate --pack ecommerce --teacher fake-small --out dataset/approvals</p>}
        <Trace role="list" data-testid="tune-dataset-list">
          {items.map((d) => (
            <PickRow key={d.name} id={d.name} testId="tune-dataset-row" selected={d.name === selected} onPick={() => setChosen(d.name)}>
              <TraceLine className={d.name === selected ? 'text-foreground' : undefined}>
                <TraceIcon>
                  <Database />
                </TraceIcon>
                <TraceTitle>
                  {d.name} v{d.version}
                </TraceTitle>
                <TraceDescription>{datasetSummary(d)}</TraceDescription>
              </TraceLine>
            </PickRow>
          ))}
        </Trace>
      </Section>
      {selected && <DatasetDetail key={selected} name={selected} ctx={ctx} />}
      <Section title="Curations" testId="tune-curations">
        {(curations.data?.items ?? []).length === 0 ? (
          <p className="text-xs text-muted-foreground">No curation yet.</p>
        ) : (
          <Trace role="list">
            {(curations.data?.items ?? []).map((c: Curation) => (
              <div role="listitem" key={c.id}>
                <TraceLine status={c.status === 'running' ? 'running' : c.status === 'error' ? 'error' : undefined}>
                  <TraceIcon>
                    <FlaskConical />
                  </TraceIcon>
                  <TraceTitle>
                    {c.id}: {c.request.name} from {c.request.packs.join(', ')}, teacher {c.request.teacher}
                  </TraceTitle>
                  <StatusBadge status={c.status} />
                  <TraceDescription>{c.counts ? `${c.counts.kept} of ${c.counts.turns} kept` : ''}</TraceDescription>
                </TraceLine>
              </div>
            ))}
          </Trace>
        )}
      </Section>
      {!ctx.replay && <CurateForm ctx={ctx} onStarted={ctx.refresh} />}
    </div>
  )
}

function datasetSummary(d: TuneArtifactRef): string {
  const s = d.summary as { kept?: number; turns?: number; train?: number; val?: number; teacher?: string; packs?: string[] }
  return [
    s.teacher ? `teacher ${s.teacher}` : '',
    typeof s.kept === 'number' ? `${s.kept} of ${s.turns ?? '?'} kept` : '',
    typeof s.train === 'number' ? `${s.train} train, ${s.val ?? 0} val` : '',
    s.packs?.length ? s.packs.join(', ') : '',
  ]
    .filter(Boolean)
    .join(' · ')
}

// ---------- runs ----------

/** The loss curve: train loss as a line, validation loss as dots, step on x from 0 to the total. */
export function LossChart({ series }: { series: LossSeries }) {
  const g = chartGeometry(series)
  const last = series.train[series.train.length - 1]
  const label = last
    ? `Training loss over ${series.train.length} steps of ${series.total ?? series.train.length}, from ${num(series.train[0]!.loss, 3)} to ${num(last.loss, 3)}${series.eval.length ? `; validation loss ${num(series.eval[series.eval.length - 1]!.loss, 3)}` : ''}`
    : 'No steps yet'
  return (
    <figure className="flex flex-col gap-1" data-testid="tune-loss-chart" data-points={series.train.length}>
      <svg viewBox={`0 0 ${g.width} ${g.height}`} className="h-auto w-full max-w-[420px]" role="img" aria-label={label}>
        {g.yTicks.map((t) => (
          <g key={t.label}>
            <line x1={g.pad.left} x2={g.width - g.pad.right} y1={t.y} y2={t.y} className="stroke-foreground/10" strokeWidth={1} />
            <text x={g.pad.left - 4} y={t.y + 3} textAnchor="end" className="fill-muted-foreground text-[9px] tabular-nums">
              {t.label}
            </text>
          </g>
        ))}
        <text x={g.pad.left} y={g.height - 4} className="fill-muted-foreground text-[9px]">
          step 0
        </text>
        <text x={g.width - g.pad.right} y={g.height - 4} textAnchor="end" className="fill-muted-foreground text-[9px] tabular-nums">
          {g.xMax}
        </text>
        {g.path && <path d={g.path} fill="none" className="stroke-kind-model" strokeWidth={1.75} strokeLinejoin="round" strokeLinecap="round" />}
        {g.dots.map((d) => (
          <circle key={d.step} cx={d.cx} cy={d.cy} r={3} className="fill-state-waiting stroke-background" strokeWidth={1}>
            <title>{`step ${d.step}: validation loss ${num(d.loss, 4)}`}</title>
          </circle>
        ))}
      </svg>
      <figcaption className="flex flex-wrap gap-x-3 text-[11px] text-muted-foreground">
        <span className="flex items-center gap-1">
          <span className="inline-block h-0.5 w-3 bg-kind-model" aria-hidden /> train loss
        </span>
        {series.eval.length > 0 && (
          <span className="flex items-center gap-1">
            <span className="inline-block size-2 rounded-full bg-state-waiting" aria-hidden /> validation loss
          </span>
        )}
        <span>{stepText(series.last)}</span>
      </figcaption>
    </figure>
  )
}

function RunDetail({ id, ctx }: { id: string; ctx: Ctx }) {
  const api = useApp((s) => s.api)
  const v = useApp((s) => s.inspectorVersion)
  const [again, setAgain] = useState(0)
  const detail = useLoad(api ? `tune-run:${id}:${v}:${ctx.tick}:${again}` : null, () => api!.tuneRun(id))
  const [live, setLive] = useState<{ id: string; events: OpsEvent[] }>({ id, events: [] })
  const running = detail.data?.status === 'running'
  // A running run's stream `tune:<id>`: its steps as they come, then the record read again.
  useEffect(() => {
    if (!api || !running) return
    const stream = openTuneStream(api.transport, api.tuneRunEventsUrl(id), {
      onEvent: (ev) => setLive((l) => ({ id, events: l.id === id ? [...l.events, ev] : [ev] })),
      onEnd: () => setAgain((n) => n + 1),
    })
    return () => stream.close()
  }, [api, id, running])
  const run = detail.data
  const series = useMemo(() => lossSeries(runSteps(run, live.id === id ? live.events : [])), [run, live, id])
  if (detail.status === 'error') return <p className="text-xs text-destructive">Could not load {id}: {detail.error}</p>
  if (!run) return <p className="text-xs text-muted-foreground">Loading {id}…</p>
  const r: TuneRun = run
  const finished = live.events.find((e) => e.type === 'tune_run_finished')
  return (
    <section className="flex flex-col gap-3" aria-label={`Run ${r.id}`} data-testid="tune-run-detail" data-run={r.id} data-status={r.status}>
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="text-sm font-medium">{r.id}</h4>
        <StatusBadge status={r.status} />
        <span className="text-xs text-muted-foreground tabular-nums">{when(r.created_at)}</span>
      </div>
      <KeyValues
        value={{
          trainer: r.request.trainer,
          student: r.request.student,
          dataset: refText('dataset', r.request.dataset),
          ...(r.request.config && Object.keys(r.request.config).length ? { config: JSON.stringify(r.request.config) } : {}),
          adapter: refText('adapter', r.adapter),
          model: refText('model', r.model),
          ...(r.metrics ? { metrics: `${r.metrics.steps} steps, train loss ${num(r.metrics.train_loss, 4)}${r.metrics.eval_loss != null ? `, eval loss ${num(r.metrics.eval_loss, 4)}` : ''}, ${num(r.metrics.wall_s, 3)} s` } : {}),
          ...(r.error ? { error: errorText(r.error) } : {}),
        }}
      />
      <LossChart series={series} />
      {running && !finished && (
        <p className="text-xs text-muted-foreground" role="status" aria-live="polite" data-testid="tune-run-live">
          Following the run's stream: {stepText(series.last)}.
        </p>
      )}
      <p className="text-xs text-muted-foreground">A run never changes what turns are served on: the model it made reaches traffic only through Compare (the gate) and Promote (the canary).</p>
    </section>
  )
}

function RunForm({ ctx, datasets, onStarted }: { ctx: Ctx; datasets: TuneArtifactRef[]; onStarted: (id: string) => void }) {
  const api = useApp((s) => s.api)
  const trainers = (ctx.overview?.trainers ?? []).filter((t) => t.implemented)
  const [dataset, setDataset] = useState(datasets[0]?.name ?? '')
  const [trainer, setTrainer] = useState(trainers.find((t) => t.kind === 'fake')?.alias ?? trainers[0]?.alias ?? 'fake')
  const [student, setStudent] = useState('fake-student')
  const [name, setName] = useState('')
  const [outcome, setOutcome] = useState<'good' | 'forgets'>('good')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const fake = (ctx.overview?.trainers ?? []).find((t) => t.alias === trainer)?.kind === 'fake'
  const start = async () => {
    if (!api) return
    setBusy(true)
    setError(null)
    try {
      const out = await api.startTuneRun({ dataset: dataset.trim() || datasets[0]?.name || '', trainer, student: student.trim(), ...(name.trim() ? { name: name.trim() } : {}), ...(fake ? { config: { outcome } } : {}) })
      onStarted(out.id)
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }
  return (
    <form
      className="flex flex-wrap items-end gap-2"
      aria-label="Start a training run"
      onSubmit={(e) => {
        e.preventDefault()
        if (!ctx.blocked && !busy) void start()
      }}
    >
      <Field label="Dataset">
        <Input value={dataset} onChange={(e) => setDataset(e.target.value)} className="h-7 w-28 text-xs" aria-label="Dataset name, optionally name@v2" list="tune-dataset-names" />
      </Field>
      <datalist id="tune-dataset-names">
        {datasets.map((d) => (
          <option key={d.name} value={d.name} />
        ))}
      </datalist>
      <Field label="Trainer">
        <ToggleGroup type="single" variant="outline" size="sm" value={trainer} onValueChange={(v) => v && setTrainer(v)} aria-label="Trainer">
          {trainers.map((t) => (
            <ToggleGroupItem key={t.alias} value={t.alias} className="text-xs">
              {t.alias}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      </Field>
      <Field label="Student">
        <Input value={student} onChange={(e) => setStudent(e.target.value)} className="h-7 w-28 text-xs" aria-label="Student model alias" />
      </Field>
      <Field label="Name">
        <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="optional" className="h-7 w-28 text-xs" aria-label="Adapter and model name" />
      </Field>
      {fake && (
        <Field label="Outcome (fake)">
          <ToggleGroup type="single" variant="outline" size="sm" value={outcome} onValueChange={(v) => v && setOutcome(v as 'good' | 'forgets')} aria-label="What the fake trainer's model does">
            <ToggleGroupItem value="good" className="text-xs">
              good
            </ToggleGroupItem>
            <ToggleGroupItem value="forgets" className="text-xs">
              forgets
            </ToggleGroupItem>
          </ToggleGroup>
        </Field>
      )}
      <ActionButton type="submit" blocked={ctx.blocked} busy={busy} hint="Train an adapter on the dataset and record the adapter and the model it serves as" testId="tune-run-start">
        <Play aria-hidden /> Train
      </ActionButton>
      {error && (
        <p className="basis-full text-xs text-destructive" role="alert">
          {error}
        </p>
      )}
    </form>
  )
}

function RunsSection({ ctx }: { ctx: Ctx }) {
  const api = useApp((s) => s.api)
  const v = useApp((s) => s.inspectorVersion)
  const runs = useLoad(api ? `tune-runs:${v}:${ctx.tick}` : null, () => api!.tuneRuns())
  const datasets = useLoad(api ? `tune-artifacts:dataset:${v}:${ctx.tick}` : null, () => api!.tuneArtifacts('dataset'))
  const [chosen, setChosen] = useState<string | null>(null)
  const items = runs.data?.items ?? []
  const selected = (chosen && items.some((r) => r.id === chosen) ? chosen : items[0]?.id) ?? null
  return (
    <div className="flex flex-col gap-4" data-testid="tune-runs">
      <Section title="Training runs, newest first">
        {runs.status === 'error' && <p className="text-xs text-destructive">Could not list runs: {runs.error}</p>}
        {runs.status === 'ok' && items.length === 0 && <p className="text-xs text-muted-foreground">No run yet.</p>}
        <Trace role="list" data-testid="tune-run-list">
          {items.map((r) => (
            <PickRow key={r.id} id={r.id} testId="tune-run-row" selected={r.id === selected} onPick={() => setChosen(r.id)}>
              <TraceLine className={r.id === selected ? 'text-foreground' : undefined} status={r.status === 'running' ? 'running' : r.status === 'error' ? 'error' : undefined}>
                <TraceIcon>
                  <FlaskConical />
                </TraceIcon>
                <TraceTitle>
                  {r.id}: {r.request.name} ({r.request.trainer}, {r.request.student})
                </TraceTitle>
                <StatusBadge status={r.status} />
                <TraceDescription>{r.last_step ? `${r.last_step.step}/${r.last_step.total_steps ?? '?'}` : ''}</TraceDescription>
              </TraceLine>
            </PickRow>
          ))}
        </Trace>
      </Section>
      {selected && <RunDetail key={selected} id={selected} ctx={ctx} />}
      {!ctx.replay && (
        <RunForm
          ctx={ctx}
          datasets={datasets.data?.items ?? []}
          onStarted={(id) => {
            setChosen(id)
            ctx.refresh()
          }}
        />
      )}
    </div>
  )
}

// ---------- compare ----------

export function ComparisonView({ c }: { c: Comparison }) {
  const arms = comparisonArms(c)
  const rows = compareRows(c)
  const split = toolSplitRows(c)
  const v = c.verdict
  const pa = promptArmText(c)
  return (
    <section className="flex flex-col gap-3" aria-label={`Comparison ${c.id}`} data-testid="tune-comparison" data-id={c.id} data-verdict={v?.status ?? c.status}>
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="text-sm font-medium">{c.id}</h4>
        <StatusBadge status={v?.status ?? c.status} />
        <span className="text-xs text-muted-foreground">
          {refText('model', c.request.candidate)} against <code className="font-mono">{c.request.against}</code>, {c.request.samples} sample{c.request.samples === 1 ? '' : 's'} per cell
        </span>
      </div>
      {arms.length > 0 && (
        <Table data-testid="tune-compare-table">
          <TableHeader>
            <TableRow>
              <TableHead>Pack</TableHead>
              <TableHead>Role</TableHead>
              {arms.map((a) => (
                <TableHead key={a} className="text-right" title={ARM_HINTS[a]}>
                  {ARM_LABELS[a]}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={row.pack} data-testid="tune-compare-row" data-pack={row.pack} data-role={row.role}>
                <TableCell className="font-mono text-xs">{row.pack}</TableCell>
                <TableCell className={cn('text-xs', row.regressed ? 'text-destructive' : 'text-muted-foreground')} title={row.regressed ? 'This canary pack lost more cases than the tolerance' : undefined}>
                  {row.regressed ? 'canary, regressed' : row.role}
                </TableCell>
                {row.cells.map((cell) => (
                  <TableCell key={cell.arm} className="text-right text-xs tabular-nums" data-arm={cell.arm}>
                    {cell.text}
                    {cell.delta !== null && cell.delta !== 0 && (
                      <span className={cn('ml-1', cell.delta > 0 ? 'text-state-done' : 'text-destructive')}>
                        {cell.delta > 0 ? '+' : ''}
                        {num(cell.delta)}
                      </span>
                    )}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      <p className={cn('text-xs', v?.status === 'failed' ? 'text-destructive' : 'text-foreground')} data-testid="tune-verdict">
        {v?.status === 'failed' ? <ShieldX className="mr-1 inline size-3.5" aria-hidden /> : v ? <ShieldCheck className="mr-1 inline size-3.5" aria-hidden /> : null}
        {verdictText(c)}
        {v?.regressed?.length ? ` Regressed canary packs: ${v.regressed.join(', ')}.` : ''}
      </p>
      {pa && (
        <p className="text-xs text-muted-foreground" data-testid="tune-prompt-arm">
          {pa} The prompt arm is the cheaper lever (the control arm's text added to the system prompt), measured on the same cases; it reports, it does not gate.
        </p>
      )}
      {split.length > 0 && (
        <Section title="tool_calls split of the failed cases, summed over the target packs (mean per sample)">
          <Table data-testid="tune-tool-split">
            <TableHeader>
              <TableRow>
                <TableHead>Bucket</TableHead>
                {arms.map((a) => (
                  <TableHead key={a} className="text-right">
                    {ARM_LABELS[a]}
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {split.map((row) => (
                <TableRow key={row.bucket} data-bucket={row.bucket}>
                  <TableCell className="text-xs">{row.bucket}</TableCell>
                  {arms.map((a) => (
                    <TableCell key={a} className="text-right text-xs tabular-nums">
                      {num(row.values[a] ?? 0)}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Section>
      )}
      {c.error && <p className="text-xs text-destructive">{errorText(c.error)}</p>}
    </section>
  )
}

function CompareForm({ ctx, models, last, onStarted }: { ctx: Ctx; models: TuneArtifactRef[]; last: Comparison | null; onStarted: (id: string) => void }) {
  const api = useApp((s) => s.api)
  const sessionPack = useApp((s) => s.pack)
  const [candidate, setCandidate] = useState(models[0] ? `model/${models[0].name}` : '')
  const [against, setAgainst] = useState(last?.request.against ?? 'fake-student')
  const [packs, setPacks] = useState((last?.request.packs ?? (sessionPack ? [sessionPack] : [])).join(','))
  const [canaryPacks, setCanaryPacks] = useState((last?.request.canary_packs ?? []).join(','))
  const [samples, setSamples] = useState('1')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const start = async () => {
    if (!api) return
    setBusy(true)
    setError(null)
    try {
      const out = await api.startComparison({ candidate: candidate.trim(), against: against.trim(), packs: csv(packs), canary_packs: csv(canaryPacks), ...(Number(samples) > 0 ? { samples: Number(samples) } : {}) })
      onStarted(out.id)
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }
  return (
    <form
      className="flex flex-wrap items-end gap-2"
      aria-label="Start a comparison"
      onSubmit={(e) => {
        e.preventDefault()
        if (!ctx.blocked && !busy) void start()
      }}
    >
      <Field label="Candidate">
        <Input value={candidate} onChange={(e) => setCandidate(e.target.value)} className="h-7 w-40 text-xs" aria-label="Candidate model, model/<name>" list="tune-model-names" />
      </Field>
      <datalist id="tune-model-names">
        {models.map((m) => (
          <option key={m.name} value={`model/${m.name}`} />
        ))}
      </datalist>
      <Field label="Against">
        <Input value={against} onChange={(e) => setAgainst(e.target.value)} className="h-7 w-28 text-xs" aria-label="Base model alias" />
      </Field>
      <Field label="Target packs">
        <Input value={packs} onChange={(e) => setPacks(e.target.value)} className="h-7 w-36 text-xs" aria-label="Target packs, comma separated" />
      </Field>
      <Field label="Canary packs">
        <Input value={canaryPacks} onChange={(e) => setCanaryPacks(e.target.value)} className="h-7 w-32 text-xs" aria-label="Canary packs, comma separated" />
      </Field>
      <Field label="Samples">
        <Input value={samples} onChange={(e) => setSamples(e.target.value)} className="h-7 w-12 text-xs" inputMode="numeric" aria-label="Samples per arm and pack" />
      </Field>
      <ActionButton type="submit" blocked={ctx.blocked} busy={busy} hint="Run the model suite for the base, the candidate and the prompt arm on every pack, and gate the candidate" testId="tune-compare-start">
        <GitCompare aria-hidden /> Compare
      </ActionButton>
      {error && (
        <p className="basis-full text-xs text-destructive" role="alert">
          {error}
        </p>
      )}
    </form>
  )
}

function CompareSection({ ctx }: { ctx: Ctx }) {
  const api = useApp((s) => s.api)
  const v = useApp((s) => s.inspectorVersion)
  const list = useLoad(api ? `tune-comparisons:${v}:${ctx.tick}` : null, () => api!.comparisons())
  const models = useLoad(api ? `tune-artifacts:model:${v}:${ctx.tick}` : null, () => api!.tuneArtifacts('model'))
  const [chosen, setChosen] = useState<string | null>(null)
  const items = list.data?.items ?? []
  const selectedId = (chosen && items.some((c) => c.id === chosen) ? chosen : items[0]?.id) ?? null
  const listed = items.find((c) => c.id === selectedId) ?? null
  // The list carries whole records; read one again only when its cells are not there.
  const detail = useLoad(api && selectedId && listed && !listed.cells ? `tune-comparison:${selectedId}:${v}:${ctx.tick}` : null, () => api!.comparison(selectedId!))
  const shown = listed?.cells ? listed : (detail.data ?? null)
  return (
    <div className="flex flex-col gap-4" data-testid="tune-compare">
      <Section title="Models (model/<name>) and their gate">
        <ul className="flex flex-wrap gap-1.5" data-testid="tune-model-list">
          {(models.data?.items ?? []).map((m) => {
            const gate = (m.summary as { gate?: string | null }).gate
            return (
              <li key={m.name}>
                <Badge variant="outline" className="font-mono font-normal" title={m.hash} data-gate={gate ?? 'none'}>
                  {m.name} v{m.version} · gate {gate ?? 'none'}
                </Badge>
              </li>
            )
          })}
          {models.status === 'ok' && (models.data?.items ?? []).length === 0 && <li className="text-xs text-muted-foreground">No model yet: train one in Runs.</li>}
        </ul>
      </Section>
      <Section title="Comparisons, newest first">
        {list.status === 'error' && <p className="text-xs text-destructive">Could not list comparisons: {list.error}</p>}
        {list.status === 'ok' && items.length === 0 && <p className="text-xs text-muted-foreground">No comparison yet.</p>}
        <Trace role="list" data-testid="tune-comparison-list">
          {items.map((c) => (
            <PickRow key={c.id} id={c.id} testId="tune-comparison-row" selected={c.id === selectedId} onPick={() => setChosen(c.id)}>
              <TraceLine className={c.id === selectedId ? 'text-foreground' : undefined} status={c.status === 'running' ? 'running' : undefined}>
                <TraceIcon>
                  <GitCompare />
                </TraceIcon>
                <TraceTitle>
                  {c.id}: {refText('model', c.request.candidate)} against {c.request.against}
                </TraceTitle>
                <StatusBadge status={c.verdict?.status ?? c.status} />
                <TraceDescription>{c.verdict ? `gain ${num(c.verdict.gain)}` : ''}</TraceDescription>
              </TraceLine>
            </PickRow>
          ))}
        </Trace>
      </Section>
      {shown && <ComparisonView c={shown} />}
      {!ctx.replay && (
        <CompareForm
          key={items[0]?.id ?? 'none'}
          ctx={ctx}
          models={models.data?.items ?? []}
          last={items[0] ?? null}
          onStarted={(id) => {
            setChosen(id)
            ctx.refresh()
          }}
        />
      )}
    </div>
  )
}

// ---------- canary and promote ----------

function RefusalView({ refusal, onDismiss }: { refusal: TuneRefusal; onDismiss: () => void }) {
  const gate = refusal.gate as { status?: string; reason?: string | null; gain?: number; regressed?: string[] } | null | undefined
  return (
    <section className="flex flex-col gap-2 rounded-lg p-3 ring-1 ring-foreground/10" role="alert" aria-label="Promote was refused" data-testid="tune-refusal" data-code={refusal.code}>
      <div className="flex items-start gap-2">
        <ShieldX className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden />
        <div className="flex min-w-0 flex-col gap-0.5">
          <h4 className="text-sm font-medium">
            Refused: <code className="font-mono">{refusal.code}</code>
          </h4>
          <p className="text-xs text-muted-foreground">{refusal.message}. Nothing changed: turns keep the models they had.</p>
          {gate && (
            <p className="text-xs text-muted-foreground" data-testid="tune-refusal-gate">
              The model's gate: {gate.status ?? 'none'}
              {gate.reason ? ` (${gate.reason})` : ''}
              {typeof gate.gain === 'number' ? `, gain ${num(gate.gain)}` : ''}
              {gate.regressed?.length ? `, canary regressed on ${gate.regressed.join(', ')}` : ''}. Compare it again, or train a better one.
            </p>
          )}
          {refusal.canary && (
            <p className="text-xs text-muted-foreground" data-testid="tune-refusal-canary">
              The canary gate: {refusal.canary.gate.reason ?? 'failed'}. Candidate arm {armText(refusal.canary.arms.candidate)}; base arm {armText(refusal.canary.arms.base)}. Roll back, or keep the canary small.
            </p>
          )}
        </div>
      </div>
      <Button size="xs" variant="outline" className="self-end" onClick={onDismiss}>
        Close
      </Button>
    </section>
  )
}

function CanaryNow({ canary }: { canary: CanaryState }) {
  const rule = canary.rule
  return (
    <div className="flex flex-col gap-2" data-testid="tune-canary-state" data-fraction={rule?.split.fraction ?? 'none'}>
      <p className="text-sm" data-testid="tune-canary-rule">
        {canaryRuleText(rule)}
      </p>
      {rule && (
        <p className="font-mono text-xs break-all text-muted-foreground">
          router.canary: when pack {rulePacks(rule).join(', ') || 'any'} · split base {rule.split.base_alias}, candidate {rule.split.candidate_alias}, fraction {num(rule.split.fraction)}
        </p>
      )}
      <Table data-testid="tune-canary-arms">
        <TableHeader>
          <TableRow>
            <TableHead>Arm</TableHead>
            <TableHead className="text-right">Turns</TableHead>
            <TableHead className="text-right">Failed</TableHead>
            <TableHead className="text-right" title="Turns with bad feedback">Bad</TableHead>
            <TableHead className="text-right" title="Failed turns over turns">Rate</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {(['base', 'candidate'] as const).map((arm) => {
            const a = canary.arms[arm]
            return (
              <TableRow key={arm} data-arm={arm}>
                <TableCell className="text-xs">
                  <span className="flex flex-col">
                    {arm}
                    {rule ? <span className="font-mono text-[11px] text-muted-foreground">{arm === 'base' ? rule.split.base_alias : rule.split.candidate_alias}</span> : null}
                  </span>
                </TableCell>
                <TableCell className="text-right text-xs tabular-nums">{a.turns}</TableCell>
                <TableCell className="text-right text-xs tabular-nums">{a.failed}</TableCell>
                <TableCell className="text-right text-xs tabular-nums">{a.bad_feedback}</TableCell>
                <TableCell className="text-right text-xs tabular-nums">{a.failure_rate === null ? '·' : `${num(a.failure_rate * 100, 1)}%`}</TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
      <p className={cn('text-xs', canary.gate.passed ? 'text-state-done' : 'text-muted-foreground')} data-testid="tune-canary-gate" data-passed={canary.gate.passed ? 'true' : 'false'}>
        Canary gate {canary.gate.passed ? 'passes: Promote full may widen it to every turn.' : `does not pass yet: ${canary.gate.reason ?? 'no reason given'}.`}
      </p>
    </div>
  )
}

function CanarySection({ ctx }: { ctx: Ctx }) {
  const api = useApp((s) => s.api)
  const v = useApp((s) => s.inspectorVersion)
  const canary = useLoad(api ? `tune-canary:${v}:${ctx.tick}` : null, () => api!.canary())
  const models = useLoad(api ? `tune-artifacts:model:${v}:${ctx.tick}` : null, () => api!.tuneArtifacts('model'))
  const items = models.data?.items ?? []
  const passed = items.filter((m) => (m.summary as { gate?: string }).gate === 'passed')
  // null until the reader types: the newest model with a passed gate, else the newest model.
  const [candidate, setCandidate] = useState<string | null>(null)
  const [fractionText, setFraction] = useState('0.1')
  const [busy, setBusy] = useState<string | null>(null)
  const [refusal, setRefusal] = useState<TuneRefusal | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)
  const [confirming, setConfirming] = useState(false)
  const pick = candidate ?? (passed[0] ? `model/${passed[0].name}` : items[0] ? `model/${items[0].name}` : '')
  const fraction = parseFraction(fractionText)
  const act = async (what: string, fn: () => Promise<{ result?: { setting: { stage: string; fraction: number } }; refused?: TuneRefusal }>) => {
    setBusy(what)
    setRefusal(null)
    setError(null)
    setDone(null)
    try {
      const out = await fn()
      if (out.refused) setRefusal(out.refused)
      else if (out.result) setDone(`${out.result.setting.stage} at ${num(out.result.setting.fraction * 100, 1)}%: the packs were released with the new router.canary.`)
      ctx.refresh()
    } catch (err) {
      setError(err instanceof ApiError ? `${err.code}: ${err.message}` : message(err))
    } finally {
      setBusy(null)
    }
  }
  const c = canary.data
  return (
    <div className="flex flex-col gap-4" data-testid="tune-canary">
      {canary.status === 'error' && <p className="text-xs text-destructive">Could not read the canary: {canary.error}</p>}
      {c && <CanaryNow canary={c} />}
      {!ctx.replay && (
        <section className="flex flex-col gap-2" aria-label="Promote and roll back">
          <div className="flex flex-wrap items-end gap-2">
            <Field label="Candidate">
              <Input value={pick} onChange={(e) => setCandidate(e.target.value)} className="h-7 w-44 text-xs" aria-label="Model to promote, model/<name>" list="tune-promote-names" data-testid="tune-promote-candidate" />
            </Field>
            <datalist id="tune-promote-names">
              {items.map((m) => (
                <option key={m.name} value={`model/${m.name}`} />
              ))}
            </datalist>
            <Field label="Fraction">
              <Input value={fractionText} onChange={(e) => setFraction(e.target.value)} className="h-7 w-16 text-xs" aria-label="Share of the packs' turns for the candidate, 0 to 1" aria-invalid={fraction === null ? true : undefined} data-testid="tune-promote-fraction" />
            </Field>
            <ActionButton
              blocked={ctx.blocked ?? (fraction === null ? 'The fraction is a number from 0 to 1 (or a percentage).' : null)}
              busy={busy === 'canary'}
              hint="Needs a passed gate. Adds the alias to models/models, sets router.canary to this share of the packs' turns, and releases the packs"
              onClick={() => void act('canary', () => api!.promote({ candidate: pick, stage: 'canary', fraction: fraction ?? undefined }))}
              testId="tune-promote"
            >
              <Rocket aria-hidden /> Promote
            </ActionButton>
            <ActionButton
              blocked={ctx.blocked}
              busy={busy === 'full'}
              variant="outline"
              hint="Needs the canary gate to pass (enough candidate turns, a failure rate no worse than the base's). Sets the fraction to 1"
              onClick={() => void act('full', () => api!.promote({ candidate: pick, stage: 'full' }))}
              testId="tune-promote-full"
            >
              <ArrowUpRight aria-hidden /> Promote full
            </ActionButton>
            {!confirming && (
              <ActionButton blocked={ctx.blocked ?? (c?.rule ? null : 'No canary rule is set.')} variant="outline" hint="Set the canary's fraction to 0 and release the packs: no turn reaches the candidate" onClick={() => setConfirming(true)} testId="tune-rollback">
                <Undo2 aria-hidden /> Rollback
              </ActionButton>
            )}
          </div>
          {confirming && (
            <ConfirmItem
              icon={<History className="text-state-waiting" aria-hidden />}
              title="Roll the canary back?"
              description="The fraction goes to 0 and the packs are released again, so every turn answers on the base. The alias stays in models/models as the record."
              confirmLabel="Roll back"
              onDeny={() => setConfirming(false)}
              onConfirm={async () => {
                await act('rollback', () => api!.tuneRollback())
                setConfirming(false)
              }}
              data-testid="tune-rollback-confirm"
            />
          )}
          {refusal && <RefusalView refusal={refusal} onDismiss={() => setRefusal(null)} />}
          {error && (
            <p className="text-xs text-destructive" role="alert" data-testid="tune-action-error">
              {error}
            </p>
          )}
          {done && (
            <p className="text-xs text-muted-foreground" role="status" data-testid="tune-action-done">
              {done}
            </p>
          )}
        </section>
      )}
      {c && c.history.length > 0 && (
        <Section title="History, newest first" testId="tune-canary-history">
          <ul className="flex flex-col gap-0.5 text-xs text-muted-foreground">
            {c.history.map((h, i) => (
              <li key={`${h.at ?? i}-${h.stage}`} className="tabular-nums">
                {when(h.at)} · {h.stage} at {num(h.fraction * 100, 1)}%{h.model ? ` · ${h.model}` : ''} · {h.packs.join(', ')}
                {h.forced ? ' · forced' : ''}
                {h.by ? ` · ${h.by}` : ''}
              </li>
            ))}
          </ul>
        </Section>
      )}
    </div>
  )
}

// ---------- the panel ----------

/** The Tune tab: datasets, runs, comparisons and the canary. */
export function TunePanel() {
  const api = useApp((s) => s.api)
  const capabilities = useApp((s) => s.capabilities)
  const v = useApp((s) => s.inspectorVersion)
  const section = useTuneUi((s) => s.section)
  const setSection = useTuneUi((s) => s.setSection)
  const [tick, setTick] = useState(0)
  const overview = useLoad(api ? `tune:${v}:${tick}` : null, () => api!.tune())
  if (overview.status === 'error') {
    return <p className="text-xs text-muted-foreground">This API has no tuning loop yet (GET /v1/tune: {overview.error}).</p>
  }
  if (!overview.data) return <p className="text-xs text-muted-foreground">Loading the tuning loop…</p>
  const o = overview.data
  const replay = o.replay === true || capabilities?.mode === 'public'
  const ctx: Ctx = { blocked: tuneBlockReason(capabilities, replay, o.running), replay, overview: o, refresh: () => setTick((t) => t + 1), tick }
  return (
    <div className="flex flex-col gap-4" data-testid="tune-panel" data-replay={replay ? 'true' : 'false'}>
      {replay && (
        <p className="rounded-md bg-muted px-3 py-2 text-xs" role="note" data-testid="tune-replay-note">
          {REPLAY_NOTE}
        </p>
      )}
      <p className="text-xs text-muted-foreground" data-testid="tune-overview">
        Trainers {o.trainers.map((t) => `${t.alias} (${t.kind}${t.implemented ? '' : ', not implemented'})`).join(', ')}. Default teacher {o.teacher}. The gate: beat the base by {num(o.gate.min_gain_cases)} cases over the target packs, lose at most{' '}
        {num(o.gate.canary_tolerance_cases)} on each canary pack, {o.gate.samples} samples per cell.
      </p>
      {o.running && (
        <p className="text-xs text-state-waiting" role="status" data-testid="tune-running">
          <LoaderCircle className="mr-1 inline size-3.5 animate-spin motion-reduce:animate-none" aria-hidden />A {o.running.kind} is running: {o.running.id}.
        </p>
      )}
      <ToggleGroup type="single" variant="outline" size="sm" value={section} onValueChange={(s) => s && setSection(s as TuneSection)} aria-label="Tune section" className="self-start" data-testid="tune-sections">
        {TUNE_SECTIONS.map((s) => (
          <ToggleGroupItem key={s.value} value={s.value} className="text-xs" data-testid={`tune-section-${s.value}`}>
            {s.label}
          </ToggleGroupItem>
        ))}
      </ToggleGroup>
      {section === 'datasets' && <DatasetsSection ctx={ctx} />}
      {section === 'runs' && <RunsSection ctx={ctx} />}
      {section === 'compare' && <CompareSection ctx={ctx} />}
      {section === 'canary' && <CanarySection ctx={ctx} />}
    </div>
  )
}
