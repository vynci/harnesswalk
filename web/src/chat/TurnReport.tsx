// A turn's divider on the chat timeline, as its report: "Turn 2 · 04:39 PM", the one-line
// summary ("answered · 1.65 s · 2349 tok · $0.0050") and a disclosure that opens the full report
// card (facts, bars, the policy's sentence, the release it ran on). Closed by default; the latest
// turn's opens when it ends if the reader left the one before it open (panels.ts).
import { ChevronDown } from 'lucide-react'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { TurnReportCard } from '@/diagram/BudgetDetails'
import { sessionReports, usePanels } from '@/state/panels'
import { useApp } from '@/state/store'
import { reportLine, type TurnReport } from '@/state/turnReport'

export function TurnReportRow({
  turnId,
  index,
  started,
  report,
  latest,
}: {
  turnId: string
  index: number
  started: Date | null
  report: TurnReport
  latest: boolean
}) {
  const sessionId = useApp((s) => s.sessionId)
  const open = usePanels((s) => sessionReports(s, sessionId).open.includes(turnId))
  const setReportOpen = usePanels((s) => s.setReportOpen)
  const time = started && Number.isFinite(started.getTime()) ? started.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : null
  const line = reportLine(report)
  return (
    <Collapsible
      open={open}
      onOpenChange={(next) => setReportOpen(sessionId, turnId, next, latest)}
      className="flex flex-col"
      data-testid="turn-report"
      data-turn={index + 1}
      data-turn-id={turnId}
      data-open={open ? 'true' : 'false'}
      data-source={report.fromSummary ? 'summary' : 'events'}
    >
      <CollapsibleTrigger asChild>
        <button
          type="button"
          className="group/report -mx-1 flex min-w-0 cursor-pointer items-center gap-2 rounded-md px-1 py-0.5 text-left text-xs text-muted-foreground outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/30"
          data-testid="turn-report-toggle"
        >
          <span className="shrink-0 font-medium">
            Turn {index + 1}
            {time ? ` · ${time}` : ''}
          </span>
          <span className="h-px min-w-3 flex-1 bg-border" aria-hidden />
          <span className="min-w-0 truncate font-mono tabular-nums" data-testid="turn-report-summary" title={line}>
            {line}
          </span>
          <span className="sr-only">, {open ? 'hide' : 'show'} the report</span>
          <ChevronDown className="size-3.5 shrink-0 transition-transform group-data-[state=open]/report:rotate-180 motion-reduce:transition-none" aria-hidden />
        </button>
      </CollapsibleTrigger>
      <CollapsibleContent className="pt-2">
        <div className="rounded-lg border bg-card p-3 text-card-foreground">
          <TurnReportCard report={report} label={`Turn ${index + 1} report`} testId="turn-report-card" />
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}
