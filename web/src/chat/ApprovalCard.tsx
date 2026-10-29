import { useEffect, useId, useState } from 'react'
import { ShieldAlert } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import type { PendingApproval } from '@/state/reducer'
import { formatCountdown } from './format'

function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [active])
  return now
}

export function ApprovalCard({
  approval,
  interactive,
  onDecide,
}: {
  approval: PendingApproval
  interactive: boolean
  onDecide: (d: 'approve' | 'deny') => Promise<void>
}) {
  const [sending, setSending] = useState<'approve' | 'deny' | null>(null)
  const headingId = useId()
  const decided = approval.decision !== undefined
  const now = useNow(!decided)
  const expiresAt = approval.expires_at ? Date.parse(approval.expires_at) : NaN
  const left = Number.isFinite(expiresAt) ? expiresAt - now : null
  const expired = left !== null && left <= 0
  const disabled = !interactive || decided || expired || sending !== null

  const decide = async (d: 'approve' | 'deny') => {
    setSending(d)
    try {
      await onDecide(d)
    } finally {
      setSending(null)
    }
  }

  return (
    <section
      className="rounded-lg border-2 border-[var(--state-waiting)] bg-card p-3 shadow-sm"
      aria-labelledby={headingId}
      data-testid="approval-card"
    >
      <header className="flex items-center gap-2">
        <ShieldAlert className="size-4 text-state-waiting" aria-hidden />
        <h3 id={headingId} className="text-sm font-semibold">{decided ? 'Approval decided' : 'Approval needed'}</h3>
        <Badge variant="outline" className="ml-auto font-mono text-[11px]" data-testid="approval-action-class">
          {approval.action_class || 'unknown'}
        </Badge>
      </header>
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        <dt className="text-muted-foreground">Tool</dt>
        <dd className="font-mono" data-testid="approval-tool">
          {approval.tool}
        </dd>
        {approval.reason && (
          <>
            <dt className="text-muted-foreground">Why</dt>
            <dd>{approval.reason}</dd>
          </>
        )}
        {!decided && (
          <>
            <dt className="text-muted-foreground">Expires</dt>
            <dd data-testid="approval-countdown" aria-live="off">
              {left === null ? 'no expiry given' : formatCountdown(left)}
            </dd>
          </>
        )}
      </dl>
      {approval.args_preview !== undefined && (
        <pre className="mt-2 max-h-40 overflow-auto rounded bg-muted p-2 text-[11px] leading-4" aria-label="Arguments preview">
          {JSON.stringify(approval.args_preview, null, 2)}
        </pre>
      )}
      {decided ? (
        <p className="mt-2 text-xs" data-testid="approval-decision">
          <span className="font-medium">{approval.decision}</span>
          {approval.decided_by ? ` by ${approval.decided_by}` : ''}
        </p>
      ) : (
        <div className="mt-3 flex gap-2">
          <Button size="sm" onClick={() => void decide('approve')} disabled={disabled} aria-label={`Approve ${approval.tool}`}>
            {sending === 'approve' ? 'Approving…' : 'Approve'}
          </Button>
          <Button size="sm" variant="outline" onClick={() => void decide('deny')} disabled={disabled} aria-label={`Deny ${approval.tool}`}>
            {sending === 'deny' ? 'Denying…' : 'Deny'}
          </Button>
          {!interactive && <span className="self-center text-xs text-muted-foreground">read-only in replay</span>}
        </div>
      )}
    </section>
  )
}
