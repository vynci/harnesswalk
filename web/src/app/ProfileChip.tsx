// The profile chip beside the pack picker (0.2.0, spec/harness.md section 10): the harness
// profile the session's pack runs with, its id and its rung on the ladder. The nodes it switches
// off are dimmed in the diagram, never hidden; the tooltip says how many and what the shape is.
import { Layers } from 'lucide-react'
import { Hint } from '@/components/hint'
import { Badge } from '@/components/ui/badge'
import { useProfile } from '@/state/hooks'
import { useApp } from '@/state/store'

export function ProfileChip() {
  const profile = useProfile()
  const rungs = useApp((s) => s.profiles?.length ?? 0)
  if (!profile) return null
  const off = profile.off.length
  const label = `Harness profile ${profile.title}, rung ${profile.rung} of ${rungs}. ${profile.summary} ${
    off === 0 ? 'Every node is on.' : `${off} node${off === 1 ? ' is' : 's are'} off, dimmed in the diagram.`
  }`
  return (
    <Hint label={label}>
      <Badge variant="outline" className="h-6 shrink-0 gap-1 font-normal text-muted-foreground tabular-nums" tabIndex={0} aria-label={label} data-testid="profile-chip" data-profile={profile.id} data-rung={profile.rung}>
        <Layers aria-hidden />
        <span className="text-foreground">{profile.id}</span>
        <span>
          {profile.rung}/{rungs}
        </span>
      </Badge>
    </Hint>
  )
}
