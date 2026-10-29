import type { NodeInfo } from '@/api/types'
import { formatMs, nodeStat } from '@/diagram/stats'
import type { NodeRun } from '@/state/reducer'
import { DataView, KeyValues } from './DataView'
import { isRecord } from './shapes'
import { ContextBudget } from './StoreViews'

export function OverviewTab({ run, info }: { run?: NodeRun; info?: NodeInfo }) {
  return (
    <div className="flex flex-col gap-4">
      <section className="flex flex-col gap-1.5" aria-label="This turn">
        <h4 className="text-xs font-semibold text-muted-foreground">This turn</h4>
        {!run || run.state === 'idle' ? (
          <p className="text-xs text-muted-foreground">Not visited in this turn.</p>
        ) : (
          <KeyValues
            value={{
              state: run.state,
              stat: nodeStat(run) ?? '·',
              entered: `${run.enters}×`,
              'last duration': formatMs(run.lastDurationMs) ?? '·',
              'total duration': formatMs(run.totalDurationMs) ?? '·',
              ...(run.cost ? { cost: `${run.cost.input_tokens} in / ${run.cost.output_tokens} out, $${run.cost.usd.toFixed(4)}` } : {}),
              ...(run.error ? { error: `${run.error.code}: ${run.error.message}` } : {}),
            }}
          />
        )}
      </section>
      {run?.enterSummary && Object.keys(run.enterSummary).length > 0 && (
        <section className="flex flex-col gap-1.5">
          <h4 className="text-xs font-semibold text-muted-foreground">On enter</h4>
          <KeyValues value={run.enterSummary} />
        </section>
      )}
      {run?.lastSummary && isRecord(run.lastSummary.slices) && <ContextBudget summary={run.lastSummary} />}
      {run?.lastSummary && (
        <section className="flex flex-col gap-1.5" data-testid="overview-summary">
          <h4 className="text-xs font-semibold text-muted-foreground">Last summary</h4>
          <KeyValues value={run.lastSummary} />
        </section>
      )}
      {info?.recent && info.recent.length > 0 && (
        <section className="flex flex-col gap-1.5">
          <h4 className="text-xs font-semibold text-muted-foreground">Recent turns</h4>
          <DataView value={info.recent.map((r) => ({ turn: r.turn_id, status: r.status, ...r.summary }))} name="recent" />
        </section>
      )}
    </div>
  )
}
