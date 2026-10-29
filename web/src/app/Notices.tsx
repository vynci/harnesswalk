import { X } from 'lucide-react'
import { useApp } from '@/state/store'

export function Notices() {
  const notices = useApp((s) => s.notices)
  const dismiss = useApp((s) => s.dismissNotice)
  if (notices.length === 0) return null
  return (
    <div className="fixed bottom-4 left-4 z-50 flex max-w-sm flex-col gap-2">
      {notices.map((n) => (
        <div
          key={n.id}
          // An error interrupts a screen reader; information waits its turn.
          role={n.level === 'error' ? 'alert' : 'status'}
          className={`flex items-start gap-2 rounded-md border bg-card px-3 py-2 text-sm shadow-md ${n.level === 'error' ? 'border-destructive/50' : ''}`}
        >
          <span className="flex-1">{n.text}</span>
          <button type="button" onClick={() => dismiss(n.id)} aria-label="Dismiss" className="text-muted-foreground hover:text-foreground">
            <X className="size-4" aria-hidden />
          </button>
        </div>
      ))}
    </div>
  )
}
