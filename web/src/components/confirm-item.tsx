import { useId, useState, type ReactNode } from 'react'
import { Button } from '@/components/ui/button'
import { Item, ItemActions, ItemContent, ItemDescription, ItemMedia, ItemTitle } from '@/components/ui/item'

/**
 * The AI Elements Confirmation as an outline Item: a question, what happens, then the deny
 * action (outline) and the confirm action (default), in that order. Used for rollbacks.
 */
export function ConfirmItem({
  icon,
  title,
  description,
  denyLabel = 'Cancel',
  confirmLabel,
  onDeny,
  onConfirm,
  ...props
}: {
  icon: ReactNode
  title: ReactNode
  description?: ReactNode
  denyLabel?: string
  confirmLabel: string
  onDeny: () => void
  onConfirm: () => Promise<unknown>
} & Omit<React.ComponentProps<typeof Item>, 'title' | 'onConfirm'>) {
  const [busy, setBusy] = useState(false)
  const titleId = useId()
  return (
    <Item variant="outline" size="sm" role="region" aria-labelledby={titleId} {...props}>
      <ItemMedia variant="icon">{icon}</ItemMedia>
      <ItemContent className="min-w-0">
        <ItemTitle id={titleId} className="line-clamp-none">
          {title}
        </ItemTitle>
        {description && <ItemDescription className="line-clamp-none text-xs">{description}</ItemDescription>}
      </ItemContent>
      <ItemActions>
        {/* Focus lands on the safe choice: Enter does not roll anything back by accident. */}
        <Button size="sm" variant="outline" onClick={onDeny} disabled={busy} autoFocus>
          {denyLabel}
        </Button>
        <Button
          size="sm"
          disabled={busy}
          onClick={async () => {
            setBusy(true)
            try {
              await onConfirm()
            } finally {
              setBusy(false)
            }
          }}
        >
          {busy ? `${confirmLabel}…` : confirmLabel}
        </Button>
      </ItemActions>
    </Item>
  )
}
