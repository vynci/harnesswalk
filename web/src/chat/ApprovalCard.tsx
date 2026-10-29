import { useEffect, useId, useState } from 'react'
import { ShieldAlert, TriangleAlert } from 'lucide-react'
import { Code } from '@/components/code'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
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
    <Card
      className="gap-3 border-2 border-state-waiting py-3 shadow-sm"
      role="region"
      aria-labelledby={headingId}
      data-testid="approval-card"
      data-decided={decided ? 'true' : 'false'}
    >
      <CardHeader className="flex items-center gap-2 px-3">
        <ShieldAlert className="size-4 text-state-waiting" aria-hidden />
        <CardTitle id={headingId} className="text-sm">
          {decided ? 'Approval decided' : 'Approval needed'}
        </CardTitle>
        <Badge variant="outline" className="ml-auto font-mono text-[11px]" data-testid="approval-action-class">
          {approval.action_class || 'unknown'}
        </Badge>
      </CardHeader>
      <CardContent className="flex flex-col gap-2 px-3">
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
          <dt className="text-muted-foreground">Tool</dt>
          <dd className="font-mono" data-testid="approval-tool">
            {approval.tool}
          </dd>
          {!decided && (
            <>
              <dt className="text-muted-foreground">Expires</dt>
              <dd data-testid="approval-countdown" aria-live="off" className="tabular-nums">
                {left === null ? 'no expiry given' : formatCountdown(left)}
              </dd>
            </>
          )}
        </dl>
        {approval.reason && (
          // A pending approval is announced once; a decided one is history.
          <Alert role={decided ? 'note' : 'alert'} className="border-state-waiting/40 px-3 py-2">
            <TriangleAlert className="text-state-waiting" />
            <AlertTitle className="text-xs">Why it needs a person</AlertTitle>
            <AlertDescription className="text-xs">{approval.reason}</AlertDescription>
          </Alert>
        )}
        {approval.args_preview !== undefined && (
          <Code
            code={JSON.stringify(approval.args_preview, null, 2)}
            language="json"
            size="sm"
            wrap
            label="Arguments preview"
            contentClassName="max-h-40 text-[11px]"
          />
        )}
      </CardContent>
      <CardFooter className="px-3">
        {decided ? (
          <p className="text-xs" data-testid="approval-decision">
            <span className="font-medium">{approval.decision}</span>
            {approval.decided_by ? ` by ${approval.decided_by}` : ''}
          </p>
        ) : (
          <div className="flex items-center gap-2">
            <Button size="sm" onClick={() => void decide('approve')} disabled={disabled} aria-label={`Approve ${approval.tool}`}>
              {sending === 'approve' ? 'Approving…' : 'Approve'}
            </Button>
            <Button size="sm" variant="outline" onClick={() => void decide('deny')} disabled={disabled} aria-label={`Deny ${approval.tool}`}>
              {sending === 'deny' ? 'Denying…' : 'Deny'}
            </Button>
            {!interactive && <span className="text-xs text-muted-foreground">read-only in replay</span>}
            {interactive && expired && <span className="text-xs text-muted-foreground">expired</span>}
          </div>
        )}
      </CardFooter>
    </Card>
  )
}
