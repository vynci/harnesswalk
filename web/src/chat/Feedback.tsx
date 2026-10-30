// Good and Bad as MessageFooter actions (the AI Elements Message pattern): ghost icon buttons
// with `aria-pressed`, the pressed one filled. A press records the rating at once, then a
// popover offers an optional note, sent with the same rating. Online judge scores sit beside
// them as muted chips.
import { useId, useState } from 'react'
import { ThumbsDown, ThumbsUp } from 'lucide-react'
import type { FeedbackRating, TurnFeedback } from '@/api/types'
import { Hint } from '@/components/hint'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Popover, PopoverAnchor, PopoverContent, PopoverDescription, PopoverHeader, PopoverTitle } from '@/components/ui/popover'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'
import { useApp } from '@/state/store'

const LABEL: Record<FeedbackRating, string> = { good: 'Good response', bad: 'Bad response' }

function score(v: number): string {
  return Number.isInteger(v) ? String(v) : v.toFixed(2).replace(/0+$/, '').replace(/\.$/, '')
}

export function ScoreChips({ scores }: { scores: Record<string, number> | null | undefined }) {
  const entries = Object.entries(scores ?? {}).filter(([, v]) => typeof v === 'number')
  if (entries.length === 0) return null
  return (
    <span className="flex flex-wrap items-center gap-1" data-testid="turn-scores">
      {entries.map(([name, v]) => (
        <Hint key={name} label={`Online judge: ${name.replace(/_/g, ' ')} ${score(v)} of 1, on a sampled turn`} side="top">
          <Badge variant="secondary" className="font-normal text-muted-foreground tabular-nums" tabIndex={0} data-score={name}>
            {name.replace(/_/g, ' ')} {score(v)}
          </Badge>
        </Hint>
      ))}
    </span>
  )
}

export function FeedbackActions({ turnId, feedback }: { turnId: string; feedback: TurnFeedback | null | undefined }) {
  const sendFeedback = useApp((s) => s.sendFeedback)
  const [noteFor, setNoteFor] = useState<FeedbackRating | null>(null)
  const [note, setNote] = useState('')
  const [sending, setSending] = useState(false)
  const noteId = useId()
  const rating = feedback?.rating ?? null

  const press = async (r: FeedbackRating) => {
    setSending(true)
    const ok = await sendFeedback(turnId, r)
    setSending(false)
    if (ok) {
      setNote(feedback?.rating === r ? (feedback.note ?? '') : '')
      setNoteFor(r)
    }
  }
  const sendNote = async () => {
    if (!noteFor) return
    setSending(true)
    const ok = await sendFeedback(turnId, noteFor, note.trim() || undefined)
    setSending(false)
    if (ok) setNoteFor(null)
  }

  return (
    <Popover open={noteFor !== null} onOpenChange={(open) => !open && setNoteFor(null)}>
      <PopoverAnchor asChild>
        <span className="flex items-center" role="group" aria-label="Rate this reply" data-testid="feedback" data-rating={rating ?? ''}>
          {(['good', 'bad'] as const).map((r) => {
            const Icon = r === 'good' ? ThumbsUp : ThumbsDown
            const pressed = rating === r
            return (
              <Hint key={r} label={LABEL[r]} side="top">
                <Button
                  variant="ghost"
                  size="icon-sm"
                  className={cn('text-muted-foreground', pressed && 'text-foreground')}
                  aria-label={LABEL[r]}
                  aria-pressed={pressed}
                  disabled={sending}
                  onClick={() => void press(r)}
                  data-testid={`feedback-${r}`}
                >
                  <Icon className={cn(pressed && 'fill-current')} />
                </Button>
              </Hint>
            )
          })}
        </span>
      </PopoverAnchor>
      <PopoverContent side="top" align="start" className="w-72" data-testid="feedback-note">
        <PopoverHeader>
          <PopoverTitle className="text-sm">{noteFor === 'bad' ? 'What went wrong?' : 'What went well?'}</PopoverTitle>
          <PopoverDescription className="text-xs">Recorded as {noteFor}. A note is optional; online eval reads it.</PopoverDescription>
        </PopoverHeader>
        <form
          className="flex flex-col gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            void sendNote()
          }}
        >
          <label htmlFor={noteId} className="sr-only">
            Note
          </label>
          <Textarea id={noteId} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Add a note (optional)" className="min-h-20 text-sm" autoFocus />
          <div className="flex justify-end gap-2">
            <Button type="button" size="sm" variant="outline" onClick={() => setNoteFor(null)}>
              Skip
            </Button>
            <Button type="submit" size="sm" disabled={sending || !note.trim()}>
              Send note
            </Button>
          </div>
        </form>
      </PopoverContent>
    </Popover>
  )
}
