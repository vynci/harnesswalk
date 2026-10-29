import { useEffect, useId, useState } from 'react'
import { ShieldQuestion } from 'lucide-react'
import { Code } from '@/components/code'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Item, ItemActions, ItemContent, ItemDescription, ItemFooter, ItemMedia, ItemTitle } from '@/components/ui/item'
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

/**
 * An approval as the AI Elements Confirmation: an outline Item asking "Run X?" with Deny then
 * Approve, under the tool line that waits for it. Once decided it goes away: the tool line above
 * says how it was decided and by whom.
 */
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
  if (decided) return null
  const expiresAt = approval.expires_at ? Date.parse(approval.expires_at) : NaN
  const left = Number.isFinite(expiresAt) ? expiresAt - now : null
  const expired = left !== null && left <= 0
  const disabled = !interactive || expired || sending !== null

  const decide = async (d: 'approve' | 'deny') => {
    setSending(d)
    try {
      await onDecide(d)
    } finally {
      setSending(null)
    }
  }

  return (
    <Item variant="outline" size="sm" role="region" aria-labelledby={headingId} data-testid="approval-card" data-decided="false">
      <ItemMedia variant="icon">
        <ShieldQuestion className="text-state-waiting" aria-hidden />
      </ItemMedia>
      <ItemContent className="min-w-0">
        <ItemTitle id={headingId} className="line-clamp-none flex-wrap">
          <span>
            Run{' '}
            <code className="font-mono" data-testid="approval-tool">
              {approval.tool}
            </code>
            ?
          </span>
          <Badge variant="secondary" className="font-mono font-normal" data-testid="approval-action-class">
            {approval.action_class || 'unknown'}
          </Badge>
        </ItemTitle>
        {approval.reason && (
          // Announced once, while it waits.
          <ItemDescription role="alert" className="line-clamp-none">
            {approval.reason}
          </ItemDescription>
        )}
        <ItemDescription className="text-xs tabular-nums">
          {left === null ? (
            <span data-testid="approval-countdown">no expiry given</span>
          ) : expired ? (
            <span data-testid="approval-countdown">expired</span>
          ) : (
            <>
              Expires in{' '}
              <span data-testid="approval-countdown" aria-live="off">
                {formatCountdown(left)}
              </span>
            </>
          )}
          {!interactive && ' · read-only in replay'}
        </ItemDescription>
      </ItemContent>
      <ItemActions>
        <Button size="sm" variant="outline" onClick={() => void decide('deny')} disabled={disabled} aria-label={`Deny ${approval.tool}`}>
          {sending === 'deny' ? 'Denying…' : 'Deny'}
        </Button>
        <Button size="sm" onClick={() => void decide('approve')} disabled={disabled} aria-label={`Approve ${approval.tool}`}>
          {sending === 'approve' ? 'Approving…' : 'Approve'}
        </Button>
      </ItemActions>
      {approval.args_preview !== undefined && (
        <ItemFooter>
          <Code code={JSON.stringify(approval.args_preview, null, 2)} language="json" size="sm" wrap label="Arguments preview" contentClassName="max-h-40" />
        </ItemFooter>
      )}
    </Item>
  )
}
