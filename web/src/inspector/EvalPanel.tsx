// The Eval node's panel: start a golden or judge run, follow its progress, and read a run as
// the AI Elements Test Results pattern (suites by case or by scorer, regressions marked, a case
// opening to expected versus actual and to its turn in replay).
import { useState } from 'react'
import { Check, CircleDashed, FlaskConical, ListChecks, LoaderCircle, OctagonX, Play, X } from 'lucide-react'
import type { EvalCase, EvalRunSummary, EvalSuite, GoldenCase } from '@/api/types'
import { Hint } from '@/components/hint'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
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
import { formatMs } from '@/diagram/stats'
import { evalBlockReason } from '@/state/ops'
import { packTitle } from '@/state/packs'
import { useApp } from '@/state/store'
import { DiagnoseAction, DiagnosisChip } from './Diagnosis'
import { afterNote } from './p5View'
import { byScorer, plural, regressionText, runHeadline, runName, showValue, sortCases } from './opsView'
import { useArtifacts, useLoad } from './useOps'

const DEFAULT_MODEL = '__default__'

function CaseIcon({ status }: { status: EvalCase['status'] }) {
  if (status === 'passed') return <Check />
  if (status === 'failed') return <X />
  if (status === 'error') return <OctagonX />
  return <CircleDashed />
}

function when(iso: string): string {
  const d = new Date(iso)
  return Number.isFinite(d.getTime()) ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : ''
}

function duration(run: EvalRunSummary): string | undefined {
  if (!run.ended_at) return undefined
  return formatMs(Date.parse(run.ended_at) - Date.parse(run.started_at))
}

function RunForm({ defaultPack, onStart }: { defaultPack: string; onStart: () => void }) {
  const capabilities = useApp((s) => s.capabilities)
  const liveEval = useApp((s) => s.liveEval)
  const startEval = useApp((s) => s.startEval)
  const packs = useApp((s) => s.packs)
  const sessionPack = useApp((s) => s.pack)
  const [suite, setSuite] = useState<EvalSuite>('golden')
  const [model, setModel] = useState(DEFAULT_MODEL)
  const blocked = evalBlockReason(capabilities)
  // The judge suite needs a judge model the API has configured (features.judge).
  const judge = capabilities?.features.judge === true
  const models = (capabilities?.providers ?? []).filter((p) => p.configured).flatMap((p) => p.models.map((m) => ({ provider: p.name, model: m })))
  // The session's pack: the panel runs that pack's golden set (P5).
  const packValue = sessionPack ?? defaultPack
  const busy = !!liveEval
  const run = () => {
    if (blocked || busy || !packValue.trim()) return
    onStart()
    const pick = judge ? suite : 'golden'
    void startEval({ pack: packValue.trim(), suite: pick, ...(pick === 'judge' && model !== DEFAULT_MODEL ? { model } : {}) })
  }
  return (
    <form
      className="flex flex-wrap items-end gap-2"
      aria-label="Start an eval run"
      onSubmit={(e) => {
        e.preventDefault()
        run()
      }}
    >
      <p className="flex min-w-0 flex-col gap-1 text-xs text-muted-foreground">
        <span>Pack</span>
        <span className="flex h-7 max-w-48 items-center truncate text-foreground" title={`datasets/${packValue}/golden.jsonl`} data-testid="eval-pack" data-pack={packValue}>
          {packTitle(packs, packValue)}
        </span>
      </p>
      {judge ? (
        <div className="flex flex-col gap-1 text-xs text-muted-foreground">
          <span id="eval-suite-label">Suite</span>
          <ToggleGroup type="single" variant="outline" size="sm" value={suite} onValueChange={(v) => v && setSuite(v as EvalSuite)} aria-labelledby="eval-suite-label">
            <ToggleGroupItem value="golden">golden</ToggleGroupItem>
            <ToggleGroupItem value="judge" data-testid="eval-suite-judge">
              judge
            </ToggleGroupItem>
          </ToggleGroup>
        </div>
      ) : (
        <p className="flex flex-col gap-1 text-xs text-muted-foreground">
          <span>Suite</span>
          <span className="flex h-7 items-center text-foreground" data-testid="eval-suite-golden">
            golden
          </span>
        </p>
      )}
      {judge && suite === 'judge' && (
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          Answer with
          <Select value={model} onValueChange={setModel}>
            <SelectTrigger size="sm" className="w-32" aria-label="Model that answers the cases">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={DEFAULT_MODEL}>the fixtures</SelectItem>
              {models.map((m) => (
                <SelectItem key={`${m.provider}/${m.model}`} value={m.model}>
                  {m.model}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
      )}
      <Hint label={blocked ?? (suite === 'golden' || !judge ? 'Replay every golden case on the fake provider and score it' : 'Score the answers with the judge model')}>
        <Button type="submit" size="sm" className="ml-auto" aria-disabled={blocked || busy ? true : undefined} data-testid="eval-run-button">
          {busy ? <LoaderCircle className="animate-spin motion-reduce:animate-none" aria-hidden /> : <Play aria-hidden />}
          {busy ? 'Running…' : 'Run'}
        </Button>
      </Hint>
      {blocked && <p className="basis-full text-xs text-muted-foreground">{blocked}</p>}
      {!blocked && !judge && <p className="basis-full text-xs text-muted-foreground">The judge suite needs a judge model (evals.judge.model); this API has none, so only golden runs here.</p>}
    </form>
  )
}

function CaseDetail({ c, runId }: { c: EvalCase; runId: string }) {
  const enterReplay = useApp((s) => s.enterReplay)
  const expected = c.expected ?? {}
  const actual = c.actual
  const rows: [string, unknown, unknown][] = []
  const keys = new Set([...Object.keys(expected), ...(actual ? Object.keys(actual) : [])])
  for (const k of keys) rows.push([k, expected[k], actual ? (actual as Record<string, unknown>)[k] : undefined])
  return (
    <div className="flex flex-col gap-3" data-testid="eval-case-detail">
      {c.after && (
        <p className="text-xs text-muted-foreground" data-testid="eval-case-after">
          {afterNote(c.after)}
        </p>
      )}
      {c.status === 'skipped' && c.detail && (
        <p className="text-xs text-muted-foreground" data-testid="eval-case-skipped">
          Skipped: {c.detail}.
        </p>
      )}
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-xs">
        {rows.map(([k, want, got]) => (
          <div key={k} className="contents">
            <dt className="text-muted-foreground">{k.replace(/_/g, ' ')}</dt>
            <dd className="flex min-w-0 flex-col gap-0.5">
              {want !== undefined && (
                <span className="break-words">
                  <span className="text-muted-foreground">expected </span>
                  <span className="font-mono text-foreground">{showValue(want)}</span>
                </span>
              )}
              {got !== undefined && (
                <span className="break-words">
                  <span className="text-muted-foreground">actual </span>
                  <span className={k === 'reply' ? 'text-foreground' : 'font-mono text-foreground'}>{showValue(got)}</span>
                </span>
              )}
            </dd>
          </div>
        ))}
      </dl>
      {Object.keys(c.scores ?? {}).length > 0 && (
        <ul className="flex flex-col gap-1.5" aria-label="Scores">
          {Object.entries(c.scores).map(([name, s]) => (
            <li
              key={name}
              className={s.pass === false ? 'flex gap-2 text-sm text-destructive' : 'flex gap-2 text-sm'}
              data-scorer={name}
              data-status={s.pass === false ? 'error' : s.pass === true ? 'ok' : 'none'}
            >
              <span className="mt-0.5 size-4 shrink-0 [&_svg]:size-4" aria-hidden>
                {s.pass === false ? <X /> : s.pass === true ? <Check /> : <CircleDashed />}
              </span>
              <span className="flex min-w-0 flex-col">
                <span>
                  <span className="font-mono">{name}</span> {showValue(s.value)}
                  <span className="sr-only">{s.pass === false ? ', failed' : s.pass === true ? ', passed' : ''}</span>
                </span>
                {s.detail && <span className="text-xs break-words text-muted-foreground">{s.detail}</span>}
              </span>
            </li>
          ))}
        </ul>
      )}
      {c.error && <p className="text-xs break-words text-destructive">Error {c.error.code}: {c.error.message}</p>}
      {(c.diagnosis || c.status === 'failed' || c.status === 'error') && <DiagnoseAction stored={c.diagnosis} target={{ run_id: runId, case_id: c.case_id }} />}
      {c.turn_id && c.events_url && (
        <Button
          size="xs"
          variant="outline"
          className="self-start"
          onClick={() => void enterReplay(c.turn_id!, { eventsUrl: c.events_url!, label: `Eval case ${c.case_id}` })}
          data-testid="eval-case-replay"
        >
          <Play aria-hidden /> Open the turn in replay
        </Button>
      )}
    </div>
  )
}

function CaseLine({ c }: { c: EvalCase }) {
  return (
    <TraceLine status={c.status === 'failed' || c.status === 'error' ? 'error' : undefined} data-testid="eval-case" data-case={c.case_id} data-status={c.status} data-regression={c.regression ? 'true' : 'false'}>
      <TraceIcon>
        <CaseIcon status={c.status} />
      </TraceIcon>
      <TraceTitle>{c.title || c.case_id}</TraceTitle>
      {c.regression && (
        <Badge variant="destructive" className="h-5 shrink-0 font-normal" data-testid="regression-badge">
          regression
        </Badge>
      )}
      {c.diagnosis?.layer && <DiagnosisChip layer={c.diagnosis.layer} />}
      <TraceDescription>
        {c.status === 'skipped'
          ? c.detail
            ? `skipped, ${c.detail}`
            : 'skipped'
          : [c.status === 'error' ? 'error, counted as failed' : '', c.previous_status && c.previous_status !== c.status ? `was ${c.previous_status}` : ''].filter(Boolean).join(', ')}
      </TraceDescription>
    </TraceLine>
  )
}

function ByCase({ cases, runId }: { cases: EvalCase[]; runId: string }) {
  return (
    <Trace role="list" aria-label="Cases" data-testid="eval-cases">
      {sortCases(cases).map((c) => (
        <TraceCollapsible key={c.case_id} role="listitem">
          <TraceCollapsibleTrigger>
            <CaseLine c={c} />
          </TraceCollapsibleTrigger>
          <TraceCollapsibleContent>
            <CaseDetail c={c} runId={runId} />
          </TraceCollapsibleContent>
        </TraceCollapsible>
      ))}
    </Trace>
  )
}

function ByScorer({ cases }: { cases: EvalCase[] }) {
  const suites = byScorer(cases)
  if (suites.length === 0) return <p className="text-xs text-muted-foreground">No scorer ran.</p>
  return (
    <Trace role="list" aria-label="Scorers" data-testid="eval-scorers">
      {suites.map((s) => (
        <TraceCollapsible key={s.scorer} role="listitem" defaultOpen={s.failed > 0}>
          <TraceCollapsibleTrigger>
            <TraceLine status={s.failed ? 'error' : undefined} data-scorer={s.scorer}>
              <TraceIcon>{s.failed ? <X /> : <Check />}</TraceIcon>
              <TraceTitle className="font-mono">{s.scorer}</TraceTitle>
              <TraceDescription>
                {s.passed} passed{s.failed ? `, ${s.failed} failed` : ''}
              </TraceDescription>
            </TraceLine>
          </TraceCollapsibleTrigger>
          <TraceCollapsibleContent>
            <Trace>
              {s.cases.map(({ c, pass, value }) => (
                <TraceLine key={c.case_id} status={pass === false ? 'error' : undefined}>
                  <TraceIcon>{pass === false ? <X /> : pass === true ? <Check /> : <CircleDashed />}</TraceIcon>
                  <TraceTitle>{c.title || c.case_id}</TraceTitle>
                  {c.regression && pass === false && (
                    <Badge variant="destructive" className="h-5 shrink-0 font-normal">
                      regression
                    </Badge>
                  )}
                  <TraceDescription className="font-mono">{showValue(value)}</TraceDescription>
                </TraceLine>
              ))}
            </Trace>
          </TraceCollapsibleContent>
        </TraceCollapsible>
      ))}
    </Trace>
  )
}

function RunDetail({ runId }: { runId: string }) {
  const api = useApp((s) => s.api)
  const version = useApp((s) => s.inspectorVersion)
  const liveEval = useApp((s) => s.liveEval)
  const opsRun = useApp((s) => s.opsRun)
  const live = liveEval?.runId === runId
  const done = live ? liveEval.done : opsRun?.runId === runId ? opsRun.done : undefined
  const run = useLoad(api ? `run:${runId}:${version}:${done ?? 'x'}` : null, () => api!.evalRun(runId))
  const [group, setGroup] = useState<'case' | 'scorer'>('case')
  const [open, setOpen] = useState<boolean | undefined>(undefined)
  if (run.status === 'error') return <p className="text-xs text-destructive">Could not load the run: {run.error}</p>
  if (!run.data) return <p className="text-xs text-muted-foreground">Loading the run…</p>
  const r = run.data
  const failing = r.totals.failed > 0
  const errors = r.cases.filter((c) => c.status === 'error').length
  return (
    <section className="flex flex-col gap-3" aria-label={`Eval run ${r.run_id}`} data-testid="eval-run-detail" data-run={r.run_id} data-status={r.status}>
      <Trace>
        <TraceCollapsible open={open ?? true} onOpenChange={setOpen}>
          <TraceCollapsibleTrigger>
            <TraceLine status={r.status === 'running' ? 'running' : failing || r.status === 'error' ? 'error' : undefined} data-testid="eval-headline">
              <TraceIcon>
                <FlaskConical />
              </TraceIcon>
              <TraceTitle>{runHeadline(r, done, errors)}</TraceTitle>
              {r.regressions > 0 && (
                <Badge variant="destructive" className="h-5 shrink-0 font-normal" title={regressionText(r.regressions, r.previous_run_id)}>
                  {plural(r.regressions, 'regression')}
                </Badge>
              )}
              <TraceDescription>{duration(r)}</TraceDescription>
            </TraceLine>
          </TraceCollapsibleTrigger>
          <TraceCollapsibleContent className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span>
                {runName(r)}
                {r.purpose === 'release_gate' ? ', the release gate' : ''}
              </span>
              <span className="tabular-nums">
                {r.totals.passed} passed, {r.totals.failed} failed{errors ? ` (${errors} with an error)` : ''}, {r.totals.skipped} skipped
              </span>
              {r.previous_run_id && <span>compared with {r.previous_run_id}</span>}
              <ToggleGroup type="single" size="sm" value={group} onValueChange={(v) => v && setGroup(v as 'case' | 'scorer')} aria-label="Group results" className="ml-auto">
                <ToggleGroupItem value="case" className="h-6 px-2 text-xs">
                  By case
                </ToggleGroupItem>
                <ToggleGroupItem value="scorer" className="h-6 px-2 text-xs">
                  By scorer
                </ToggleGroupItem>
              </ToggleGroup>
            </div>
            {r.regressions > 0 && (
              <p className="text-xs text-muted-foreground" data-testid="eval-regression-note">
                {regressionText(r.regressions, r.previous_run_id)}. Failed counts errors too.
              </p>
            )}
            {r.error && <p className="text-xs text-destructive">The run stopped: {r.error.message}</p>}
            {r.cases.length === 0 ? <p className="text-xs">No case has finished yet.</p> : group === 'case' ? <ByCase cases={r.cases} runId={r.run_id} /> : <ByScorer cases={r.cases} />}
          </TraceCollapsibleContent>
        </TraceCollapsible>
      </Trace>
    </section>
  )
}

/** What a golden case asks for besides its prompt: checks, modes, the case it runs after. */
function goldenNotes(g: GoldenCase): string[] {
  const out: string[] = []
  if (g.checks?.router_rules?.length) out.push(`router rules ${g.checks.router_rules.join(', ')}`)
  if (g.checks?.retrieved) out.push(`retrieves ${Object.entries(g.checks.retrieved).map(([store, text]) => `"${text}" from ${store}`).join(', ')}`)
  if (g.modes?.length) out.push(`${g.modes.join(' and ')} mode only`)
  return out
}

/** The session pack's golden set (GET /v1/evals/golden?pack=), each case runnable alone. */
function GoldenSetView({ blocked, onStart }: { blocked: string | null; onStart: () => void }) {
  const api = useApp((s) => s.api)
  const pack = useApp((s) => s.pack)
  const packs = useApp((s) => s.packs)
  const liveEval = useApp((s) => s.liveEval)
  const startEval = useApp((s) => s.startEval)
  const golden = useLoad(api ? `golden:${pack ?? ''}` : null, () => api!.golden())
  if (golden.status === 'error') return <p className="text-xs text-destructive">Could not read the golden set: {golden.error}</p>
  if (!golden.data) return null
  const set = golden.data
  return (
    <Trace data-testid="golden-set" data-pack={set.pack}>
      <TraceCollapsible>
        <TraceCollapsibleTrigger>
          <TraceLine>
            <TraceIcon>
              <ListChecks />
            </TraceIcon>
            <TraceTitle>
              Golden set of {packTitle(packs, set.pack)}: {plural(set.cases.length, 'case')}
            </TraceTitle>
            <TraceDescription className="font-mono">{set.path}</TraceDescription>
          </TraceLine>
        </TraceCollapsibleTrigger>
        <TraceCollapsibleContent>
          <ul className="flex flex-col gap-2" aria-label="Golden cases">
            {set.cases.map((g) => {
              const notes = goldenNotes(g)
              return (
                <li key={g.id} className="flex flex-col gap-0.5 text-xs" data-testid="golden-case" data-case={g.id} data-after={g.after ?? ''}>
                  <div className="flex items-start gap-2">
                    <span className="min-w-0 flex-1">
                      <span className="text-foreground">{g.title || g.id}</span> <code className="font-mono text-muted-foreground">{g.id}</code>
                    </span>
                    <Hint label={blocked ?? (g.after ? afterNote(g.after) : `Run ${g.id} alone`)} side="top">
                      <Button
                        size="xs"
                        variant="ghost"
                        className="shrink-0 text-muted-foreground"
                        aria-label={`Run ${g.id} alone`}
                        aria-disabled={blocked || liveEval ? true : undefined}
                        onClick={() => {
                          if (blocked || liveEval) return
                          onStart()
                          void startEval({ pack: set.pack, suite: 'golden', cases: [g.id] })
                        }}
                        data-testid="golden-run-case"
                      >
                        <Play aria-hidden />
                      </Button>
                    </Hint>
                  </div>
                  {notes.length > 0 && <span className="text-muted-foreground">{notes.join(' · ')}</span>}
                  {g.after && (
                    <span className="text-muted-foreground" data-testid="golden-after">
                      {afterNote(g.after)}
                    </span>
                  )}
                </li>
              )
            })}
          </ul>
        </TraceCollapsibleContent>
      </TraceCollapsible>
    </Trace>
  )
}

/** The Eval node: Run, the runs list, and the selected run as Test Results. */
export function EvalPanel() {
  const api = useApp((s) => s.api)
  const capabilities = useApp((s) => s.capabilities)
  // Public mode keeps the panel (the runs list is read-only); Run is disabled with its reason.
  const on = capabilities?.features.evals === true || capabilities?.mode === 'public'
  const version = useApp((s) => s.inspectorVersion)
  const liveEval = useApp((s) => s.liveEval)
  const opsRun = useApp((s) => s.opsRun)
  const packs = useApp((s) => s.packs)
  const { pack: artifactsPack } = useArtifacts()
  const runs = useLoad(api && on ? `runs:${version}` : null, () => api!.evalRuns({ limit: 10 }))
  const [chosen, setChosen] = useState<string | null>(null)
  if (!on) return <p className="text-xs text-muted-foreground">This API does not run evals yet (no features.evals in its capabilities).</p>
  const list = runs.data ?? []
  // A run started here is shown as it goes; otherwise the newest, unless the reader picked one.
  const selected = liveEval?.runId ?? opsRun?.runId ?? (chosen && list.some((r) => r.run_id === chosen) ? chosen : list[0]?.run_id) ?? null
  // A run another browser or the CLI started, heard on the tenant's ops stream.
  const progress = liveEval ?? opsRun
  return (
    <div className="flex flex-col gap-4" data-testid="eval-panel">
      <RunForm defaultPack={list[0]?.pack ?? artifactsPack ?? ''} onStart={() => setChosen(null)} />
      <GoldenSetView blocked={evalBlockReason(capabilities)} onStart={() => setChosen(null)} />
      {progress && (
        <Trace role="status" aria-live="polite">
          <TraceLine status="running" data-testid="eval-progress">
            <TraceIcon>
              <LoaderCircle className="animate-spin motion-reduce:animate-none" />
            </TraceIcon>
            <TraceTitle>
              {!liveEval && opsRun?.purpose === 'release_gate'
                ? `Running the release gate: the ${progress.suite} suite on ${packTitle(packs, progress.pack)}`
                : `Running the ${progress.suite} suite on ${packTitle(packs, progress.pack)}${!liveEval ? ' (started elsewhere)' : ''}`}
            </TraceTitle>
            <TraceDescription>
              {progress.done}
              {progress.total ? ` of ${progress.total}` : ''} cases done
            </TraceDescription>
          </TraceLine>
        </Trace>
      )}
      {selected && <RunDetail runId={selected} />}
      <section className="flex flex-col gap-1.5" aria-label="Recent runs">
        <h4 className="text-xs font-medium text-muted-foreground">Recent runs</h4>
        {runs.status === 'error' && <p className="text-xs text-destructive">Could not list runs: {runs.error}</p>}
        {runs.data && list.length === 0 && <p className="text-xs text-muted-foreground">No runs yet. Run the golden suite to get a baseline.</p>}
        <Trace role="list" data-testid="eval-runs">
          {list.map((r) => (
            <div key={r.run_id} role="listitem">
              <button
                type="button"
                className="flex w-fit max-w-full min-w-0 rounded-sm text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/30"
                onClick={() => setChosen(r.run_id)}
                aria-current={r.run_id === selected ? 'true' : undefined}
                data-testid="eval-run-row"
                data-run={r.run_id}
              >
                <TraceLine status={r.status === 'running' ? 'running' : undefined} className={r.run_id === selected ? 'text-foreground' : undefined}>
                  <TraceIcon>{r.status === 'running' ? <LoaderCircle /> : r.totals.failed ? <X /> : <Check />}</TraceIcon>
                  <TraceTitle>
                    {runName(r)}: {r.totals.passed} passed, {r.totals.failed} failed
                  </TraceTitle>
                  {r.purpose === 'release_gate' && (
                    <Badge variant="outline" className="h-5 shrink-0 font-normal" data-testid="eval-run-gate">
                      release gate
                    </Badge>
                  )}
                  {r.regressions > 0 && (
                    <Badge variant="destructive" className="h-5 shrink-0 font-normal">
                      {plural(r.regressions, 'regression')}
                    </Badge>
                  )}
                  <TraceDescription>{when(r.started_at)}</TraceDescription>
                </TraceLine>
              </button>
            </div>
          ))}
        </Trace>
      </section>
    </div>
  )
}
