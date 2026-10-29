const ITEMS: { state: string; label: string }[] = [
  { state: 'active', label: 'active' },
  { state: 'waiting', label: 'waiting' },
  { state: 'done', label: 'done' },
  { state: 'error', label: 'error' },
  { state: 'skipped', label: 'skipped' },
]

export function Legend() {
  return (
    <div className="pointer-events-none absolute right-3 bottom-8 z-10 hidden gap-2 rounded-md bg-card/90 px-2 py-1 text-[11px] text-muted-foreground shadow-sm ring-1 ring-border md:flex" aria-hidden>
      {ITEMS.map((i) => (
        <span key={i.state} className="flex items-center gap-1">
          <span className="inline-block size-2 rounded-full" style={{ background: `var(--state-${i.state})` }} />
          {i.label}
        </span>
      ))}
      <span className="flex items-center gap-1">
        <span className="inline-block h-2 w-3 rounded-sm border border-dashed border-current opacity-60" />
        not yet built
      </span>
    </div>
  )
}
