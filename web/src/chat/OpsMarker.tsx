// A save to the working set or a release becoming active, as the AI Elements Checkpoint: a
// separator Marker with the label and a Restore button. Restore asks first (Confirmation), then
// rolls back to the release that was active before, or saves the artifact's previous version
// again (history stays append-only).
import { useState } from 'react'
import { History, Package, PencilLine } from 'lucide-react'
import { ConfirmItem } from '@/components/confirm-item'
import { Hint } from '@/components/hint'
import { Button } from '@/components/ui/button'
import { Marker, MarkerContent, MarkerIcon } from '@/components/ui/marker'
import { artifactLabel, editBlockReason, releaseBlockReason, type OpsMark } from '@/state/ops'
import { selectTurnRunning, useApp } from '@/state/store'

export function OpsMarker({ mark, interactive }: { mark: OpsMark; interactive: boolean }) {
  const capabilities = useApp((s) => s.capabilities)
  const running = useApp(selectTurnRunning)
  const rollback = useApp((s) => s.rollback)
  const restoreArtifact = useApp((s) => s.restoreArtifact)
  const [confirming, setConfirming] = useState(false)
  const r = mark.restore
  const blocked = !r ? null : r.type === 'rollback' ? releaseBlockReason(capabilities) : editBlockReason(capabilities, running)
  const target = !r ? '' : r.type === 'rollback' ? `release ${r.releaseId}` : `${artifactLabel(r.kind, r.name)} v${r.version}`
  const Icon = mark.kind === 'saved' ? PencilLine : mark.kind === 'rolled_back' ? History : Package
  return (
    <div className="flex flex-col gap-2" data-testid="ops-marker" data-kind={mark.kind}>
      <Marker variant="separator" role="status">
        <MarkerIcon>
          <Icon />
        </MarkerIcon>
        <MarkerContent className="flex items-center gap-2">
          <span data-testid="ops-marker-text">{mark.text}</span>
          {r && interactive && !confirming && (
            <Hint label={blocked ?? `Restore ${target}`} side="top">
              <Button size="xs" variant="outline" aria-disabled={blocked ? true : undefined} onClick={() => !blocked && setConfirming(true)} aria-label={`Restore ${target}`} data-testid="ops-marker-restore">
                Restore
              </Button>
            </Hint>
          )}
        </MarkerContent>
      </Marker>
      {r && confirming && (
        <ConfirmItem
          icon={<History className="text-state-waiting" aria-hidden />}
          title={r.type === 'rollback' ? `Roll back to ${r.releaseId}?` : `Restore ${target}?`}
          description={
            r.type === 'rollback'
              ? `Turns use ${r.releaseId} from the next message, and the working set gets its content back as new versions.`
              : `Saves ${target}'s content as a new working-set version. Nothing is deleted.`
          }
          confirmLabel={r.type === 'rollback' ? 'Roll back' : 'Restore'}
          onDeny={() => setConfirming(false)}
          onConfirm={async () => {
            const ok = r.type === 'rollback' ? await rollback(r.releaseId) : await restoreArtifact(r.kind, r.name, r.version)
            if (ok) setConfirming(false)
          }}
          data-testid="ops-marker-confirm"
        />
      )}
    </div>
  )
}
