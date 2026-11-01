/// <reference types="node" />
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

// WCAG contrast of the diagram's colour tokens, read from index.css, in both themes and for both
// plane palettes (candidate A in :root and .dark, candidate B in the [data-planes='b'] block):
// 4.5:1 for text, 3:1 for the state icons and rings, the kind icons and the edge strokes. Nodes
// are neutral cards on their own warm surface (--node-surface) on their plane's fill, so node
// colours are checked on that surface. The dashed plane border is decoration (the fill, the label
// and the Legend carry the plane), so it has no contrast floor, only the family rule below.

type RGB = [number, number, number]

// Vitest stubs CSS imports, so read the stylesheet itself.
const css = readFileSync(new URL('../index.css', import.meta.url), 'utf8')

function block(selector: string): Map<string, string> {
  const start = css.indexOf(`${selector} {`)
  const body = css.slice(start, css.indexOf('}', start))
  const out = new Map<string, string>()
  for (const m of body.matchAll(/--([\w-]+):\s*([^;]+);/g)) out.set(m[1]!, m[2]!.trim())
  return out
}

function parse(value: string): RGB {
  const hex = value.match(/^#([0-9a-f]{6})$/i)
  if (hex) return [0, 2, 4].map((i) => parseInt(hex[1]!.slice(i, i + 2), 16) / 255) as RGB
  // oklch(L C H) to sRGB through OKLab (Björn Ottosson's matrices); achromatic when C is 0.
  const ok = value.match(/^oklch\(([\d.]+) ([\d.]+) ([\d.]+)\)$/)
  if (ok) {
    const [L, C, H] = ok.slice(1).map(Number) as [number, number, number]
    const a = C * Math.cos((H * Math.PI) / 180)
    const b = C * Math.sin((H * Math.PI) / 180)
    const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3
    const linear = [
      4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
      -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
      -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
    ]
    return linear.map((x) => {
      const v = Math.min(1, Math.max(0, x))
      return v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055
    }) as RGB
  }
  throw new Error(`cannot parse colour ${value}`)
}

const lin = (c: number) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4)
const luminance = ([r, g, b]: RGB) => 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
export function contrast(a: RGB, b: RGB): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number]
  return (hi + 0.05) / (lo + 0.05)
}
const mix = (a: RGB, b: RGB, t: number): RGB => [0, 1, 2].map((i) => a[i]! * t + b[i]! * (1 - t)) as RGB

const light = block(':root')
const dark = new Map([...light, ...block('.dark')])
const lightB = new Map([...light, ...block(":root[data-planes='b']")])
const darkB = new Map([...dark, ...block(":root.dark[data-planes='b']")])
const PLANES = ['control', 'context', 'execution', 'ops']
const STATES = ['active', 'waiting', 'done', 'error', 'idle', 'skipped']
// Only these states draw a ring on the node (index.css .hw-node).
const RINGS = ['active', 'waiting', 'error']
const KINDS = ['code', 'config', 'prompt', 'store', 'queue', 'model']
const EDGES = ['data', 'control', 'telemetry', 'release']

describe.each([
  ['light, palette A', light],
  ['dark, palette A', dark],
  ['light, palette B', lightB],
  ['dark, palette B', darkB],
])('%s', (_name, tokens) => {
  const c = (name: string) => parse(tokens.get(name)!)

  it('plane headers and node text read at 4.5:1', () => {
    for (const p of PLANES) expect(contrast(c(`plane-${p}-ink`), c(`plane-${p}-fill`)), `${p} header`).toBeGreaterThanOrEqual(4.5)
    expect(contrast(c('node-surface-foreground'), c('node-surface')), 'node title').toBeGreaterThanOrEqual(4.5)
    expect(contrast(c('muted-foreground'), c('node-surface')), 'node stat').toBeGreaterThanOrEqual(4.5)
  })

  it('store badges read at 4.5:1: unlit on the node surface, lit on every plane (.hw-store-badge)', () => {
    expect(contrast(c('muted-foreground'), c('node-surface')), 'unlit badge').toBeGreaterThanOrEqual(4.5)
    for (const p of PLANES) expect(contrast(c(`plane-${p}-fill`), c(`plane-${p}-ink`)), `${p} lit badge`).toBeGreaterThanOrEqual(4.5)
  })

  it('nodes sit on their own warm surface token, not the card colour other cards use', () => {
    expect(tokens.get('node-surface')).toMatch(/^oklch\([\d.]+ 0\.0\d+ 85\)$/)
    expect(tokens.get('node-surface')).not.toBe(tokens.get('card'))
  })

  it('state icons and rings stand out at 3:1 on the node surface', () => {
    for (const s of STATES) expect(contrast(c(`state-${s}`), c('node-surface')), `${s} on the node`).toBeGreaterThanOrEqual(3)
  })

  it('the state rings stand out at 3:1 on every plane fill around the card', () => {
    for (const p of PLANES) for (const s of RINGS) expect(contrast(c(`state-${s}`), c(`plane-${p}-fill`)), `${s} ring on ${p}`).toBeGreaterThanOrEqual(3)
  })

  it('kind icons stand out at 3:1 on their chip on the node surface', () => {
    for (const k of KINDS) {
      const chip = mix(c(`kind-${k}`), c('node-surface'), 0.13)
      expect(contrast(c(`kind-${k}`), chip), k).toBeGreaterThanOrEqual(3)
    }
  })

  it('edge strokes in use stand out at 3:1 on the canvas and the planes', () => {
    const strokes = [...EDGES.map((e) => `edge-${e}`), ...PLANES.map((p) => `plane-${p}`)]
    for (const s of strokes) {
      expect(contrast(c(s), c('background')), `${s} on canvas`).toBeGreaterThanOrEqual(3)
      for (const p of PLANES) expect(contrast(c(s), c(`plane-${p}-fill`)), `${s} on ${p}`).toBeGreaterThanOrEqual(3)
    }
  })

  it('the four planes are one family: each role at one lightness and chroma, one hue per plane', () => {
    const lch = (name: string) => tokens.get(name)!.match(/^oklch\(([\d.]+) ([\d.]+) ([\d.]+)\)$/)!.slice(1).map(Number)
    for (const role of ['', '-fill', '-border', '-ink']) {
      const [first, ...rest] = PLANES.map((p) => lch(`plane-${p}${role}`))
      for (const other of rest) expect(other.slice(0, 2), `plane${role}`).toEqual(first!.slice(0, 2))
    }
    for (const p of PLANES) {
      const hues = ['', '-fill', '-border', '-ink'].map((role) => lch(`plane-${p}${role}`)[2])
      expect(new Set(hues).size, `${p} roles share a hue`).toBe(1)
    }
  })

  it('a plane fill sits under the node surface, so the cards stay lifted off it', () => {
    const L = (name: string) => Number(tokens.get(name)!.match(/^oklch\(([\d.]+)/)![1])
    for (const p of PLANES) expect(L(`plane-${p}-fill`), p).toBeLessThan(L('node-surface'))
  })
})

describe('brand palette (P6)', () => {
  const c = (tokens: Map<string, string>, name: string) => parse(tokens.get(name)!)

  it('the light theme takes the owner palette as given', () => {
    expect(light.get('primary')).toBe('#0ea47a')
    expect(light.get('primary-hover')).toBe('#0a7557')
    expect(light.get('ring')).toBe('#12d39d')
    expect(light.get('border')).toBe('#e6e6e6')
    expect(light.get('foreground')).toBe('#111111')
    expect(light.get('background')).toBe('#ffffff')
    expect(dark.get('background')).toBe('#111111')
    expect(dark.get('primary')).toBe('#0a7557')
  })

  it('primary fills keep their text at 3:1 (button labels and icons), and the dark primary at 4.5:1', () => {
    // #0ea47a with white is 3.2:1: fine for icons and the large-text rule, short of 4.5:1 for
    // small labels. The report names the trade-off; the dark primary passes 4.5:1.
    expect(contrast(c(light, 'primary-foreground'), c(light, 'primary'))).toBeGreaterThanOrEqual(3)
    expect(contrast(c(light, 'primary-foreground'), c(light, 'primary-hover'))).toBeGreaterThanOrEqual(4.5)
    expect(contrast(c(dark, 'primary-foreground'), c(dark, 'primary'))).toBeGreaterThanOrEqual(4.5)
  })

  it('the text-safe green reads at 4.5:1 on white, the accent is a fill (under 3:1 on white)', () => {
    expect(contrast(c(light, 'brand-strong'), c(light, 'background'))).toBeGreaterThanOrEqual(4.5)
    expect(contrast(c(light, 'brand-accent'), c(light, 'background'))).toBeLessThan(3)
    expect(contrast(c(light, 'accent-foreground'), c(light, 'accent'))).toBeGreaterThanOrEqual(4.5)
    expect(contrast(c(dark, 'accent-foreground'), c(dark, 'accent'))).toBeGreaterThanOrEqual(4.5)
    expect(contrast(c(light, 'muted-foreground'), c(light, 'muted'))).toBeGreaterThanOrEqual(4.5)
  })

  it('the header mark is the brand green at 3:1 on the background in both themes', () => {
    expect(contrast(c(light, 'brand'), c(light, 'background'))).toBeGreaterThanOrEqual(3)
    expect(contrast(c(dark, 'brand'), c(dark, 'background'))).toBeGreaterThanOrEqual(3)
  })
})
