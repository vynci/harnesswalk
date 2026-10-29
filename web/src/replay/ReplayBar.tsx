import { useEffect } from 'react'
import { ChevronLeft, ChevronRight, Pause, Play, X } from 'lucide-react'
import { Hint } from '@/components/hint'
import { Button } from '@/components/ui/button'
import { ButtonGroup } from '@/components/ui/button-group'
import { Card } from '@/components/ui/card'
import { Kbd } from '@/components/ui/kbd'
import { Slider } from '@/components/ui/slider'
import { useApp } from '@/state/store'

const PLAY_MS = 260

export function ReplayBar() {
  const replay = useApp((s) => s.replay)
  const setCursor = useApp((s) => s.setCursor)
  const stepReplay = useApp((s) => s.stepReplay)
  const setPlaying = useApp((s) => s.setPlaying)
  const exitReplay = useApp((s) => s.exitReplay)

  useEffect(() => {
    if (!replay?.playing) return
    if (replay.cursor >= replay.events.length - 1) {
      setPlaying(false)
      return
    }
    const id = setTimeout(() => stepReplay(1), PLAY_MS)
    return () => clearTimeout(id)
  }, [replay?.playing, replay?.cursor, replay?.events.length, stepReplay, setPlaying])

  if (!replay) return null
  const ev = replay.events[replay.cursor]
  const last = replay.events.length - 1
  const what = ev ? [ev.type, ev.node_id ?? ev.edge_id].filter(Boolean).join(' ') : ''

  return (
    <Card
      className="absolute inset-x-3 bottom-3 z-20 mx-auto max-w-3xl flex-row flex-wrap items-center gap-2 rounded-lg bg-card/95 px-3 py-2 shadow-md backdrop-blur"
      role="group"
      aria-label="Replay controls"
      data-testid="replay-bar"
      onKeyDown={(e) => {
        if (e.key === 'Escape') exitReplay()
      }}
    >
      <span className="text-xs font-semibold">Replay</span>
      <ButtonGroup aria-label="Step through the turn">
        <Hint label="Previous event" side="top">
          <Button size="icon-sm" variant="outline" className="size-7" onClick={() => stepReplay(-1)} disabled={replay.cursor <= 0} aria-label="Previous event" data-testid="replay-prev">
            <ChevronLeft />
          </Button>
        </Hint>
        <Hint label={replay.playing ? 'Pause' : 'Play'} side="top">
          <Button
            size="icon-sm"
            variant="outline"
            className="size-7"
            onClick={() => setPlaying(!replay.playing)}
            aria-label={replay.playing ? 'Pause replay' : 'Play replay'}
            data-testid="replay-play"
          >
            {replay.playing ? <Pause /> : <Play />}
          </Button>
        </Hint>
        <Hint label="Next event" side="top">
          <Button size="icon-sm" variant="outline" className="size-7" onClick={() => stepReplay(1)} disabled={replay.cursor >= last} aria-label="Next event" data-testid="replay-next">
            <ChevronRight />
          </Button>
        </Hint>
      </ButtonGroup>
      <Slider
        className="min-w-40 flex-1"
        min={0}
        max={last}
        step={1}
        value={[replay.cursor]}
        onValueChange={(v) => setCursor(v[0] ?? 0)}
        aria-label="Replay position"
        data-testid="replay-slider"
      />
      <span className="w-48 truncate font-mono text-[11px] text-muted-foreground" data-testid="replay-position" title={what}>
        {replay.cursor + 1}/{replay.events.length} {what}
      </span>
      <Hint
        label={
          <>
            Exit replay <Kbd>Esc</Kbd>
          </>
        }
        side="top"
      >
        <Button size="icon" variant="ghost" className="size-7" onClick={exitReplay} aria-label="Exit replay" aria-keyshortcuts="Escape" data-testid="replay-exit">
          <X />
        </Button>
      </Hint>
    </Card>
  )
}
