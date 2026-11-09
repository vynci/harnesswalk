// The Release node's panel: the active release and whether the working set moved past it,
// Create release, the releases list, and a release's changes from the one before as Trace
// Commit lines opening to a Code Block diff. Rollback asks first (Confirmation: Cancel, then
// Roll back).
import { useState } from 'react'
import { GitCommitHorizontal, History, LoaderCircle, Package, PackagePlus, Play, ShieldCheck, ShieldX, X } from 'lucide-react'
import type { Diagnosis, GateFailure, ReleaseGate } from '@/api/types'
import { ConfirmItem } from '@/components/confirm-item'
import { DiffView } from '@/components/diff-view'
import { Hint } from '@/components/hint'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
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
import { diffStats, parseUnifiedDiff } from '@/lib/diff'
import { cn } from '@/lib/utils'
import { artifactLabel, releaseBlockReason } from '@/state/ops'
import { packTitle } from '@/state/packs'
import { useApp } from '@/state/store'
import { DiagnoseAction, DiagnosisView } from './Diagnosis'
import { gateText } from './p5View'
import { TunePanel } from './TunePanel'
import { useLoad } from './useOps'

const short = (h: string | null | undefined) => (h ? h.slice(0, 10) : '·')

function when(iso: string): string {
  const d = new Date(iso)
  return Number.isFinite(d.getTime()) ? d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : iso
}

/** The gate a release passed through, as a chip: passed, failed, skipped or forced. */
export function GateBadge({ gate }: { gate: ReleaseGate | null | undefined }) {
  if (!gate) return null
  const forced = gate.forced === true
  const text = forced ? 'forced' : `gate ${gate.status}`
  return (
    <Hint label={gateText(gate)} side="top">
      <Badge
        variant={forced || gate.status === 'failed' ? 'destructive' : gate.status === 'passed' ? 'secondary' : 'outline'}
        className={cn('h-5 shrink-0 font-normal', !forced && gate.status === 'passed' && 'text-state-done')}
        tabIndex={0}
        data-testid="release-gate"
        data-status={forced ? 'forced' : gate.status}
      >
        {forced || gate.status === 'failed' ? <ShieldX aria-hidden /> : <ShieldCheck aria-hidden />}
        {text}
      </Badge>
    </Hint>
  )
}

function CreateRelease({ blocked, dirty, busy, onCreate }: { blocked: string | null; dirty: boolean; busy: boolean; onCreate: (note: string | undefined) => Promise<boolean> }) {
  const [note, setNote] = useState('')
  const create = async () => {
    if (blocked || busy) return
    if (await onCreate(note.trim() || undefined)) setNote('')
  }
  return (
    <form
      className="flex items-center gap-2"
      aria-label="Create a release"
      onSubmit={(e) => {
        e.preventDefault()
        void create()
      }}
    >
      <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Release note (optional)" aria-label="Release note" className="h-7 flex-1 text-xs" data-testid="release-note" />
      <Hint label={blocked ?? (dirty ? 'Run the eval gate on the working set, then snapshot it as a new release; turns use it from now on' : 'Nothing changed since the active release, but you can still snapshot it')}>
        <Button type="submit" size="sm" aria-disabled={blocked || busy ? true : undefined} data-testid="release-create">
          <PackagePlus aria-hidden /> {busy ? 'Creating…' : 'Create release'}
        </Button>
      </Hint>
    </form>
  )
}

/** While POST /v1/releases waits on its gate: the gate's eval run, heard on the ops stream. */
function GateProgress() {
  const opsRun = useApp((s) => s.opsRun)
  const packs = useApp((s) => s.packs)
  const run = opsRun?.purpose === 'release_gate' ? opsRun : null
  return (
    <Trace role="status" aria-live="polite">
      <TraceLine status="running" data-testid="gate-progress">
        <TraceIcon>
          <LoaderCircle className="animate-spin motion-reduce:animate-none" />
        </TraceIcon>
        <TraceTitle>{run ? `Running the eval gate: the ${run.suite} suite on ${packTitle(packs, run.pack)}` : 'Running the eval gate'}</TraceTitle>
        <TraceDescription>{run ? `${run.done}${run.total ? ` of ${run.total}` : ''} cases done` : 'the golden set first'}</TraceDescription>
      </TraceLine>
    </Trace>
  )
}

/**
 * The gate said no (409 gate_failed): nothing was released. The failing cases, each with its
 * replay and diagnosis, and Release anyway in dev mode (audited as release_forced).
 */
function GateFailedView({ failure, busy, onForce, onDismiss }: { failure: GateFailure; busy: boolean; onForce: () => void; onDismiss: () => void }) {
  const dev = useApp((s) => s.capabilities?.mode === 'dev')
  const enterReplay = useApp((s) => s.enterReplay)
  const gate = failure.gate
  return (
    <section className="flex flex-col gap-3 rounded-lg p-3 ring-1 ring-foreground/10" role="alert" aria-label="The eval gate refused the release" data-testid="gate-failed">
      <div className="flex items-start gap-2">
        <ShieldX className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden />
        <div className="flex min-w-0 flex-col gap-0.5">
          <h4 className="text-sm font-medium">The eval gate refused the release</h4>
          <p className="text-xs text-muted-foreground" data-testid="gate-failed-message">
            {failure.message} Nothing was released; turns keep the active release.
          </p>
          <p className="text-xs text-muted-foreground/70 tabular-nums">{gateText(gate)}</p>
        </div>
      </div>
      <ul className="flex flex-col gap-2" aria-label="Failing cases">
        {failure.cases.map((c) => {
          const failing = Object.entries(c.scores ?? {}).filter(([, v]) => v.pass === false)
          return (
            <li key={c.case_id} className="flex flex-col gap-1.5 text-xs" data-testid="gate-case" data-case={c.case_id}>
              <div className="flex flex-wrap items-center gap-2">
                <X className="size-4 text-destructive" aria-hidden />
                <span className="font-medium text-foreground">{c.title || c.case_id}</span>
                <code className="font-mono text-muted-foreground">{c.case_id}</code>
                {c.events_url && (
                  <Button
                    size="xs"
                    variant="outline"
                    className="ml-auto"
                    onClick={() => void enterReplay(`case:${c.run_id ?? ''}:${c.case_id}`, { eventsUrl: c.events_url!, label: `Eval case ${c.case_id}` })}
                    data-testid="gate-case-replay"
                  >
                    <Play aria-hidden /> Replay
                  </Button>
                )}
              </div>
              {failing.length > 0 && (
                <ul className="flex flex-col gap-0.5 pl-6 text-muted-foreground">
                  {failing.map(([name, v]) => (
                    <li key={name} className="break-words">
                      <span className="font-mono text-destructive">{name}</span> {v.detail}
                    </li>
                  ))}
                </ul>
              )}
              {c.error && <p className="pl-6 break-words text-destructive">Error {c.error.code}: {c.error.message}</p>}
              <div className="pl-6">
                {c.diagnosis ? (
                  <DiagnoseFull diagnosis={c.diagnosis} runId={c.run_id} caseId={c.case_id} />
                ) : c.run_id ? (
                  <DiagnoseAction stored={null} target={{ run_id: c.run_id, case_id: c.case_id }} />
                ) : null}
              </div>
            </li>
          )
        })}
      </ul>
      <div className="flex flex-wrap items-center gap-2 border-t pt-3">
        {dev ? (
          <p className="min-w-0 flex-1 text-xs text-muted-foreground" data-testid="gate-force-note">
            Release anyway skips the gate. Dev mode only, and audited: the audit log records it as release_forced.
          </p>
        ) : (
          <p className="min-w-0 flex-1 text-xs text-muted-foreground">Fix the failing cases and release again: outside dev mode a release needs the gate to pass.</p>
        )}
        <Button size="sm" variant="outline" onClick={onDismiss} disabled={busy}>
          Close
        </Button>
        {dev && (
          <Button size="sm" variant="destructive" onClick={onForce} disabled={busy} data-testid="release-force">
            <PackagePlus aria-hidden /> {busy ? 'Releasing…' : 'Release anyway'}
          </Button>
        )}
      </div>
    </section>
  )
}

/** The 409's short diagnosis (layer and suggestion), with the evidence a click away. */
function DiagnoseFull({ diagnosis, runId, caseId }: { diagnosis: Pick<Diagnosis, 'layer' | 'suggestion'>; runId?: string; caseId: string }) {
  const [more, setMore] = useState(false)
  if (more && runId) return <DiagnoseAction stored={null} target={{ run_id: runId, case_id: caseId }} label="Show the evidence" />
  return (
    <div className="flex flex-col gap-1.5">
      <DiagnosisView diagnosis={diagnosis} />
      {runId && !more && (
        <Button size="xs" variant="ghost" className="self-start text-muted-foreground" onClick={() => setMore(true)} data-testid="gate-case-evidence">
          The evidence
        </Button>
      )}
    </div>
  )
}

function ReleaseDetailView({ releaseId, blocked, onRolledBack }: { releaseId: string; blocked: string | null; onRolledBack: () => void }) {
  const api = useApp((s) => s.api)
  const version = useApp((s) => s.inspectorVersion)
  const rollback = useApp((s) => s.rollback)
  const detail = useLoad(api ? `release:${releaseId}:${version}` : null, () => api!.release(releaseId))
  const [confirming, setConfirming] = useState(false)
  if (detail.status === 'error') return <p className="text-xs text-destructive">Could not load {releaseId}: {detail.error}</p>
  if (!detail.data) return <p className="text-xs text-muted-foreground">Loading {releaseId}…</p>
  const r = detail.data
  const first = !r.prev_hash || /^0+$/.test(r.prev_hash)
  return (
    <section className="flex flex-col gap-3" aria-label={`Release ${r.release_id}`} data-testid="release-detail" data-release={r.release_id}>
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="text-sm font-medium">{r.release_id}</h4>
        {r.active && (
          <Badge variant="secondary" className="font-normal">
            active
          </Badge>
        )}
        <span className="text-xs text-muted-foreground tabular-nums">{when(r.created_at)}</span>
        {!r.active && !confirming && (
          <Hint label={blocked ?? `Make ${r.release_id} active again and reset the working set to it`}>
            <Button
              size="xs"
              variant="outline"
              className="ml-auto"
              aria-disabled={blocked ? true : undefined}
              onClick={() => !blocked && setConfirming(true)}
              data-testid="release-rollback"
            >
              <History aria-hidden /> Roll back
            </Button>
          </Hint>
        )}
      </div>
      {r.note && <p className="text-sm">{r.note}</p>}
      {r.gate && (
        <p className={cn('text-xs', r.gate.forced ? 'text-destructive' : 'text-muted-foreground')} data-testid="release-gate-text">
          {gateText(r.gate)}.
          {r.gate.forced ? ' This release skipped the eval gate; the audit log has its release_forced record.' : ''}
        </p>
      )}
      <p className="font-mono text-xs break-all text-muted-foreground">
        hash {short(r.hash)}
        {first ? ' · first in the chain' : ` · after ${short(r.prev_hash)}`}
        {r.created_by ? ` · by ${r.created_by}` : ''}
      </p>
      {confirming && (
        <ConfirmItem
          icon={<History className="text-state-waiting" aria-hidden />}
          title={`Roll back to ${r.release_id}?`}
          description={`Turns use ${r.release_id} from now on, and the working set gets new versions with ${r.release_id}'s content. History stays: nothing is deleted.`}
          confirmLabel="Roll back"
          onDeny={() => setConfirming(false)}
          onConfirm={async () => {
            if (await rollback(r.release_id)) {
              setConfirming(false)
              onRolledBack()
            }
          }}
          data-testid="rollback-confirm"
        />
      )}
      <section className="flex flex-col gap-1.5" aria-label="Changes from the release before">
        <h4 className="text-xs font-medium text-muted-foreground">Changes from the release before</h4>
        {r.diff_from_previous.length === 0 ? (
          <p className="text-xs text-muted-foreground">No artifact changed{r.artifacts.length ? '' : ', and it pins no artifact'}.</p>
        ) : (
          <Trace role="list" data-testid="release-commits">
            {r.diff_from_previous.map((d) => {
              const lines = parseUnifiedDiff(d.diff)
              const stats = diffStats(lines)
              const label = artifactLabel(d.kind, d.name)
              return (
                <TraceCollapsible key={`${d.kind}/${d.name}`} role="listitem">
                  <TraceCollapsibleTrigger>
                    <TraceLine data-testid="release-commit" data-artifact={`${d.kind}/${d.name}`}>
                      <TraceIcon>
                        <GitCommitHorizontal />
                      </TraceIcon>
                      <TraceTitle>
                        {label} {d.from_version === null ? `added at v${d.to_version}` : d.to_version === null ? `removed after v${d.from_version}` : `v${d.from_version} → v${d.to_version}`}
                      </TraceTitle>
                      <TraceDescription>
                        +{stats.added} −{stats.removed}
                      </TraceDescription>
                    </TraceLine>
                  </TraceCollapsibleTrigger>
                  <TraceCollapsibleContent>
                    <DiffView lines={lines} label={`Diff of ${label}`} />
                  </TraceCollapsibleContent>
                </TraceCollapsible>
              )
            })}
          </Trace>
        )}
      </section>
      {r.artifacts.length > 0 && (
        <section className="flex flex-col gap-1.5" aria-label="Pinned artifacts">
          <h4 className="text-xs font-medium text-muted-foreground">Pinned artifacts</h4>
          <ul className="flex flex-wrap gap-1.5">
            {r.artifacts.map((a) => (
              <li key={`${a.kind}/${a.name}`}>
                <Badge variant="outline" className="font-mono font-normal" title={a.hash}>
                  {a.kind}/{a.name} v{a.version}
                </Badge>
              </li>
            ))}
          </ul>
        </section>
      )}
    </section>
  )
}

/** The Release node: what is served, Create release, the releases and their diffs. */
export function ReleasePanel() {
  const api = useApp((s) => s.api)
  const capabilities = useApp((s) => s.capabilities)
  const version = useApp((s) => s.inspectorVersion)
  const on = capabilities?.features.release === true
  const releases = useLoad(api && on ? `releases:${version}` : null, () => api!.releaseList())
  const [chosen, setChosen] = useState<string | null>(null)
  const createRelease = useApp((s) => s.createRelease)
  const pack = useApp((s) => s.pack)
  const [creating, setCreating] = useState(false)
  const [forcing, setForcing] = useState(false)
  // The gate's refusal, kept with the note it was asked with, and the release made anyway.
  const [failure, setFailure] = useState<{ pack: string | null; note?: string; result: GateFailure } | null>(null)
  const [forcedRelease, setForced] = useState<{ pack: string | null; id: string } | null>(null)
  const forced = forcedRelease && forcedRelease.pack === pack ? forcedRelease.id : null
  const create = async (note: string | undefined): Promise<boolean> => {
    setCreating(true)
    setFailure(null)
    setForced(null)
    const out = await createRelease(note)
    setCreating(false)
    if (out?.gateFailed) setFailure({ pack, note, result: out.gateFailed })
    if (out?.release) setChosen(out.release.release_id)
    return !!out?.release
  }
  const force = async () => {
    if (!failure) return
    setCreating(true)
    setForcing(true)
    const out = await createRelease(failure.note, { force: true })
    setCreating(false)
    setForcing(false)
    if (out?.release) {
      setFailure(null)
      setForced({ pack, id: out.release.release_id })
      setChosen(out.release.release_id)
    }
  }
  if (!on) return <p className="text-xs text-muted-foreground">This API does not version releases yet (no features.release in its capabilities).</p>
  const blocked = releaseBlockReason(capabilities)
  const list = releases.data?.releases ?? []
  const verify = releases.data?.verify
  const active = list.find((r) => r.active) ?? null
  const dirty = capabilities?.release?.working_set_dirty === true
  const servesWorkingSet = (releases.data?.use_working_set ?? capabilities?.release?.use_working_set) === true
  const selected = (chosen && list.some((r) => r.release_id === chosen) ? chosen : (active?.release_id ?? list[0]?.release_id)) ?? null
  return (
    <div className="flex flex-col gap-4" data-testid="release-panel">
      <div className="flex flex-col gap-1">
        <p className="text-sm" data-testid="release-serving">
          {active ? (
            <>
              Turns use release <span className="font-medium">{active.release_id}</span>
            </>
          ) : (
            'No release is active yet.'
          )}
        </p>
        <p className={dirty ? 'text-xs text-state-waiting' : 'text-xs text-muted-foreground'} data-testid="release-dirty" data-dirty={dirty ? 'true' : 'false'}>
          {dirty
            ? servesWorkingSet
              ? 'The working set has unreleased changes, and this harness serves the working set, so turns use them already.'
              : 'The working set has unreleased changes. Turns use them once you create a release.'
            : 'The working set matches the active release.'}
        </p>
        {verify && (
          <p className={verify.ok ? 'text-xs text-muted-foreground' : 'text-xs text-destructive'} data-testid="release-verify" data-ok={verify.ok ? 'true' : 'false'}>
            {verify.ok
              ? `Hash chain verified over ${verify.releases} release${verify.releases === 1 ? '' : 's'}.`
              : verify.broken_at
                ? `Hash chain broken at ${verify.broken_at}${verify.artifact ? ` (${verify.artifact})` : ''}.`
                : 'Hash chain verified.'}
          </p>
        )}
        {verify && typeof verify.activations === 'number' && (
          <p
            className={verify.activation_broken_at ? 'text-xs text-destructive' : 'text-xs text-muted-foreground'}
            data-testid="release-activations"
            data-ok={verify.activation_broken_at ? 'false' : 'true'}
          >
            {verify.activation_broken_at
              ? `Activation log broken at row ${verify.activation_broken_at} of ${verify.activations}.`
              : `Activation log chained over ${verify.activations} activation${verify.activations === 1 ? '' : 's'}: which release became active, when and by whom.`}
          </p>
        )}
      </div>
      <CreateRelease blocked={blocked} dirty={dirty} busy={creating} onCreate={create} />
      {blocked && <p className="text-xs text-muted-foreground">{blocked}</p>}
      {creating && !forcing && <GateProgress />}
      {failure && failure.pack === pack && <GateFailedView failure={failure.result} busy={creating} onForce={() => void force()} onDismiss={() => setFailure(null)} />}
      {forced && (
        <p className="text-xs text-muted-foreground" role="status" data-testid="release-forced-note">
          Release {forced} skipped the eval gate (forced). The audit log records it as release_forced, with the pack and the release id.
        </p>
      )}
      <section className="flex flex-col gap-1.5" aria-label="Releases">
        <h4 className="text-xs font-medium text-muted-foreground">Releases, newest first</h4>
        {releases.status === 'error' && <p className="text-xs text-destructive">Could not list releases: {releases.error}</p>}
        <Trace role="list" data-testid="release-list">
          {list.map((r) => (
            <div key={r.release_id} role="listitem">
              <button
                type="button"
                className="flex w-fit max-w-full min-w-0 rounded-sm text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/30"
                onClick={() => setChosen(r.release_id)}
                aria-current={r.release_id === selected ? 'true' : undefined}
                data-testid="release-row"
                data-release={r.release_id}
                data-active={r.active ? 'true' : 'false'}
              >
                <TraceLine className={r.release_id === selected ? 'text-foreground' : undefined}>
                  <TraceIcon>
                    <Package />
                  </TraceIcon>
                  <TraceTitle>
                    {r.release_id}
                    {r.note ? `: ${r.note}` : ''}
                  </TraceTitle>
                  {r.active && (
                    <Badge variant="secondary" className="h-5 shrink-0 font-normal">
                      active
                    </Badge>
                  )}
                  <GateBadge gate={r.gate} />
                  <TraceDescription>{when(r.created_at)}</TraceDescription>
                </TraceLine>
              </button>
            </div>
          ))}
        </Trace>
      </section>
      {selected && <ReleaseDetailView key={selected} releaseId={selected} blocked={blocked} onRolledBack={() => setChosen(null)} />}
    </div>
  )
}

/**
 * The Release node's area: its releases, and 0.2.0's Tune tab (datasets, runs, the model gate,
 * the canary). The node that writes models opens on Tune; the others on Releases.
 */
export function ReleaseArea({ tuneFirst = false }: { tuneFirst?: boolean }) {
  const [tab, setTab] = useState(tuneFirst ? 'tuning' : 'releases')
  return (
    <Tabs value={tab} onValueChange={setTab} className="gap-3" data-testid="release-area">
      <TabsList className="self-start">
        <TabsTrigger value="releases" data-testid="release-area-releases">
          Versions
        </TabsTrigger>
        <TabsTrigger value="tuning" data-testid="release-area-tune">
          Tune
        </TabsTrigger>
      </TabsList>
      <TabsContent value="releases">
        <ReleasePanel />
      </TabsContent>
      <TabsContent value="tuning">
        <TunePanel />
      </TabsContent>
    </Tabs>
  )
}
