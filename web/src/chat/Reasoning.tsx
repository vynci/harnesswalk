// Slots for P4, from the AI Elements Reasoning and Plan patterns. The event stream has no
// thinking or plan yet, so the chat renders them empty (a hidden slot, like Sources without
// citations); a later phase only fills the data.
import { useState } from 'react'
import { Circle, CircleCheck, CircleDot, ListChecks, LoaderCircle } from 'lucide-react'
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

export interface ReasoningView {
  text: string
  running: boolean
  durationMs?: number
}

/** "Thinking" while it streams (open), "Thought" once done (closed unless the reader opens it). */
export function Reasoning({ reasoning }: { reasoning?: ReasoningView | null }) {
  const [open, setOpen] = useState<boolean | undefined>(undefined)
  if (!reasoning) return <div data-slot="reasoning" hidden />
  const { running, text, durationMs } = reasoning
  return (
    <Trace data-slot="reasoning" data-testid="reasoning">
      <TraceCollapsible open={open ?? running} onOpenChange={setOpen}>
        <TraceCollapsibleTrigger>
          <TraceLine status={running ? 'running' : undefined}>
            <TraceIcon>{running ? <LoaderCircle className="animate-spin motion-reduce:animate-none" /> : <CircleCheck />}</TraceIcon>
            <TraceTitle>{running ? 'Thinking' : 'Thought'}</TraceTitle>
            {!running && durationMs !== undefined && <TraceDescription>{formatMs(durationMs)}</TraceDescription>}
          </TraceLine>
        </TraceCollapsibleTrigger>
        <TraceCollapsibleContent className="whitespace-pre-wrap">{text}</TraceCollapsibleContent>
      </TraceCollapsible>
    </Trace>
  )
}

export interface PlanView {
  title: string
  steps: { label: string; state: 'done' | 'running' | 'pending' }[]
}

/** Steps known up front: "Plan: X", "2 of 5 done", one line per step. */
export function Plan({ plan }: { plan?: PlanView | null }) {
  if (!plan) return <div data-slot="plan" hidden />
  const done = plan.steps.filter((s) => s.state === 'done').length
  return (
    <Trace data-slot="plan" data-testid="plan">
      <TraceCollapsible>
        <TraceCollapsibleTrigger>
          <TraceLine status={done < plan.steps.length && plan.steps.some((s) => s.state === 'running') ? 'running' : undefined}>
            <TraceIcon>
              <ListChecks />
            </TraceIcon>
            <TraceTitle>Plan: {plan.title}</TraceTitle>
            <TraceDescription>
              {done} of {plan.steps.length} done
            </TraceDescription>
          </TraceLine>
        </TraceCollapsibleTrigger>
        <TraceCollapsibleContent>
          <Trace>
            {plan.steps.map((s, i) => (
              <TraceLine key={i} status={s.state === 'done' ? undefined : s.state}>
                <TraceIcon>{s.state === 'done' ? <CircleCheck /> : s.state === 'running' ? <CircleDot /> : <Circle />}</TraceIcon>
                <TraceTitle>{s.label}</TraceTitle>
              </TraceLine>
            ))}
          </Trace>
        </TraceCollapsibleContent>
      </TraceCollapsible>
    </Trace>
  )
}
