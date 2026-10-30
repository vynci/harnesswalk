// The Release node's panel: the active release and whether the working set moved past it,
// Create release, the releases list, and a release's changes from the one before as Trace
// Commit lines opening to a Code Block diff. Rollback asks first (Confirmation: Cancel, then
// Roll back).
import { useState } from 'react'
import { GitCommitHorizontal, History, Package, PackagePlus } from 'lucide-react'
import type { ReleaseSummary } from '@/api/types'
import { ConfirmItem } from '@/components/confirm-item'
import { DiffView } from '@/components/diff-view'
import { Hint } from '@/components/hint'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
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
import { artifactLabel, releaseBlockReason } from '@/state/ops'
import { useApp } from '@/state/store'
import { useLoad } from './useOps'

const short = (h: string | null | undefined) => (h ? h.slice(0, 10) : '·')

function when(iso: string): string {
  const d = new Date(iso)
  return Number.isFinite(d.getTime()) ? d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : iso
}

function CreateRelease({ blocked, dirty }: { blocked: string | null; dirty: boolean }) {
  const createRelease = useApp((s) => s.createRelease)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const create = async () => {
    if (blocked || busy) return
    setBusy(true)
    const out = await createRelease(note.trim() || undefined)
    setBusy(false)
    if (out) setNote('')
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
      <Hint label={blocked ?? (dirty ? 'Snapshot the working set as a new release; turns use it from now on' : 'Nothing changed since the active release, but you can still snapshot it')}>
        <Button type="submit" size="sm" aria-disabled={blocked || busy ? true : undefined} data-testid="release-create">
          <PackagePlus aria-hidden /> {busy ? 'Creating…' : 'Create release'}
        </Button>
      </Hint>
    </form>
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
  const prevHash = (r as ReleaseSummary & { prev_hash?: string | null }).prev_hash
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
      <p className="font-mono text-xs break-all text-muted-foreground">
        hash {short(r.hash)}
        {prevHash !== undefined ? (prevHash ? ` · after ${short(prevHash)}` : ' · first in the chain') : ''}
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
  const releases = useLoad(api && on ? `releases:${version}` : null, () => api!.releases())
  const [chosen, setChosen] = useState<string | null>(null)
  if (!on) return <p className="text-xs text-muted-foreground">This API does not version releases yet (no features.release in its capabilities).</p>
  const blocked = releaseBlockReason(capabilities)
  const list = releases.data ?? []
  const active = list.find((r) => r.active) ?? null
  const dirty = capabilities?.release?.working_set_dirty === true
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
          {dirty ? 'The working set has unreleased changes. In dev the harness serves them already.' : 'The working set matches the active release.'}
        </p>
      </div>
      <CreateRelease blocked={blocked} dirty={dirty} />
      {blocked && <p className="text-xs text-muted-foreground">{blocked}</p>}
      <section className="flex flex-col gap-1.5" aria-label="Releases">
        <h4 className="text-xs font-medium text-muted-foreground">Releases, newest first</h4>
        {releases.status === 'error' && <p className="text-xs text-destructive">Could not list releases: {releases.error}</p>}
        <Trace role="list" data-testid="release-list">
          {list.map((r) => (
            <button
              key={r.release_id}
              type="button"
              role="listitem"
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
                <TraceDescription>{when(r.created_at)}</TraceDescription>
              </TraceLine>
            </button>
          ))}
        </Trace>
      </section>
      {selected && <ReleaseDetailView key={selected} releaseId={selected} blocked={blocked} onRolledBack={() => setChosen(null)} />}
    </div>
  )
}
