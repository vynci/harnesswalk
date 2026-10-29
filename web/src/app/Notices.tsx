import { useEffect, useRef } from 'react'
import { toast } from 'sonner'
import { Toaster } from '@/components/ui/sonner'
import { useApp } from '@/state/store'

/**
 * Transient notices as Sonner toasts: whatever the store's `notify` queues, plus the event
 * stream dropping and coming back. Sonner's region is polite; an error carries role="alert"
 * inside the toast so a screen reader still hears it at once.
 */
export function Notices() {
  const theme = useApp((s) => s.theme)
  const notices = useApp((s) => s.notices)
  const dismiss = useApp((s) => s.dismissNotice)
  const connection = useApp((s) => s.connection)
  const was = useRef(connection)

  useEffect(() => {
    for (const n of notices) {
      if (n.level === 'error') toast.error(<span role="alert">{n.text}</span>, { id: `notice-${n.id}`, duration: 6000 })
      else toast.info(n.text, { id: `notice-${n.id}`, duration: 6000 })
      dismiss(n.id)
    }
  }, [notices, dismiss])

  useEffect(() => {
    const before = was.current
    was.current = connection
    if (connection === 'reconnecting' && before !== 'reconnecting') {
      toast.warning('Connection to the event stream dropped. Reconnecting…', { id: 'connection', duration: Infinity })
    } else if (connection === 'open' && before === 'reconnecting') {
      toast.success('Reconnected. No events were lost.', { id: 'connection', duration: 3000 })
    } else if (connection === 'idle' && before === 'reconnecting') {
      toast.dismiss('connection')
    }
  }, [connection])

  return <Toaster theme={theme} position="top-right" closeButton offset={{ top: 56 }} />
}
