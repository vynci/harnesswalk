import { describe, expect, it } from 'vitest'

// The radius scale (index.css, "Radius"): every rounded-* class in the app is a step of the
// scale (xs, sm, md, lg, xl), full, none or the bare `rounded` (4px, xs), on any corner or side.
// The rhea names 2xl, 3xl and 4xl are aliases kept so a stray class stays on the scale; only
// the inspector still carries 2xl (the Payload tab), until it is renamed to lg there.
const sources = import.meta.glob(['../**/*.{ts,tsx}', '!../**/*.test.{ts,tsx}', '!../test/**'], {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

const STEP = /^rounded(?:-(?:[trbl]|[se]|ss|se|es|ee|tl|tr|br|bl))?(?:-(?:xs|sm|md|lg|xl|full|none))?!?$/
// Deliberate exceptions, with why.
const ALLOWED: Record<string, string> = {
  'components/ui/tooltip.tsx:rounded-[2px]': 'the 10px rotated square of the tooltip arrow: a tip, not a surface',
  'components/ui/scroll-area.tsx:rounded-[inherit]': 'the viewport takes its scroll area radius',
}

describe('radius scale', () => {
  it('every rounded class is a step of the scale', () => {
    const off: string[] = []
    for (const [path, text] of Object.entries(sources)) {
      const file = path.replace(/^\.\.\//, '')
      for (const m of text.matchAll(/(?<![\w-])rounded(?:-[\w[\]().,%-]+)?!?/g)) {
        // A variant prefix (`group-data-[x]:rounded-sm`) is not part of the match.
        const cls = m[0]
        if (STEP.test(cls) || ALLOWED[`${file}:${cls}`]) continue
        if (cls.endsWith('-2xl') && file.startsWith('inspector/')) continue
        off.push(`${file}: ${cls}`)
      }
    }
    expect(off).toEqual([])
  })
})
