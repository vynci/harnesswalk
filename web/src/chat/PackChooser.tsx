// The empty chat's pack chooser: a lead line, the pack picker as an inline dropdown (the same
// menu as the header's, in the same order, the session's pack checked) and the current pack's
// feature under it. Picking another starts a new session on it (choosePack), so the suggested
// prompts under the composer follow it.
import type { PackSummary } from '@/api/types'
import { PackPicker } from '@/app/PackPicker'
import { useApp } from '@/state/store'

export function PackChooser({ packs }: { packs: PackSummary[] }) {
  const pack = useApp((s) => s.pack)
  const current = packs.find((p) => p.id === pack)
  return (
    <div className="flex w-full flex-col items-center gap-2" data-testid="empty-pack-chooser">
      <p className="text-sm text-muted-foreground">Pick a dataset to walk through:</p>
      <PackPicker variant="inline" />
      {current?.feature && (
        <p className="text-xs text-muted-foreground" data-testid="empty-pack-feature">
          {current.feature}
          {current.domain ? ` · ${current.domain}` : ''}
        </p>
      )}
    </div>
  )
}
