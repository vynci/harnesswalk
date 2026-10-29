/// <reference types="node" />
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

// WCAG contrast of the diagram's colour tokens, read from index.css, in both themes:
// 4.5:1 for text, 3:1 for the state accent, the kind icons and the edge strokes.

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
  // The neutral palette is achromatic oklch: lightness L gives linear luminance L^3.
  const ok = value.match(/^oklch\(([\d.]+) 0 0\)$/)
  if (ok) {
    const lin = Number(ok[1]) ** 3
    const srgb = lin <= 0.0031308 ? 12.92 * lin : 1.055 * lin ** (1 / 2.4) - 0.055
    return [srgb, srgb, srgb]
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
const PLANES = ['control', 'context', 'execution', 'ops']
const STATES = ['active', 'waiting', 'done', 'error', 'idle', 'skipped']
const KINDS = ['code', 'config', 'prompt', 'store', 'queue', 'model']
const EDGES = ['data', 'control', 'telemetry', 'release']

describe.each([
  ['light', light],
  ['dark', dark],
])('%s theme', (_name, tokens) => {
  const c = (name: string) => parse(tokens.get(name)!)

  it('plane headers and node text read at 4.5:1', () => {
    for (const p of PLANES) {
      expect(contrast(c(`plane-${p}-ink`), c(`plane-${p}-group`)), `${p} header`).toBeGreaterThanOrEqual(4.5)
      expect(contrast(c('foreground'), c(`plane-${p}-fill`)), `${p} title`).toBeGreaterThanOrEqual(4.5)
      expect(contrast(c('muted-foreground'), c(`plane-${p}-fill`)), `${p} stat`).toBeGreaterThanOrEqual(4.5)
    }
  })

  it('state accents stand out at 3:1 on every plane', () => {
    for (const p of PLANES) for (const s of STATES) expect(contrast(c(`state-${s}`), c(`plane-${p}-fill`)), `${s} on ${p}`).toBeGreaterThanOrEqual(3)
  })

  it('kind icons stand out at 3:1 on their chip on every plane', () => {
    for (const p of PLANES)
      for (const k of KINDS) {
        const chip = mix(c(`kind-${k}`), c(`plane-${p}-fill`), 0.13)
        expect(contrast(c(`kind-${k}`), chip), `${k} on ${p}`).toBeGreaterThanOrEqual(3)
      }
  })

  it('edge strokes in use stand out at 3:1 on the canvas and the planes', () => {
    const strokes = [...EDGES.map((e) => `edge-${e}`), ...PLANES.map((p) => `plane-${p}`)]
    for (const s of strokes) {
      expect(contrast(c(s), c('background')), `${s} on canvas`).toBeGreaterThanOrEqual(3)
      for (const p of PLANES) expect(contrast(c(s), c(`plane-${p}-group`)), `${s} on ${p}`).toBeGreaterThanOrEqual(3)
    }
  })
})
