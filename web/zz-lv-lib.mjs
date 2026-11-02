import { chromium } from '@playwright/test'
import { writeFileSync, mkdirSync, rmSync } from 'node:fs'
export const S2 = '/private/tmp/claude-501/-Users-vynci-vynci/2ff758ba-ab90-4e84-97c9-06347b9f0da4/scratchpad/launch-video'
export const BASE = 'http://127.0.0.1:8952/'
export const W = 1920, H = 1080

const INIT = `
(() => {
  const mk = () => {
    if (document.getElementById('__cur')) return
    const c = document.createElement('div')
    c.id = '__cur'
    c.innerHTML = '<svg width="22" height="28" viewBox="0 0 22 28"><path d="M2 2 L2 22 L7.5 17 L11 25.5 L14.5 24 L11 15.8 L18.5 15.8 Z" fill="#111" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>'
    Object.assign(c.style, { position: 'fixed', left: '-50px', top: '-50px', width: '22px', height: '28px', pointerEvents: 'none', zIndex: '2147483647', transition: 'transform 120ms ease' })
    document.documentElement.appendChild(c)
    window.addEventListener('mousemove', (e) => { c.style.left = (e.clientX - 2) + 'px'; c.style.top = (e.clientY - 2) + 'px' }, true)
    window.addEventListener('mousedown', () => { c.style.transform = 'scale(0.82)' }, true)
    window.addEventListener('mouseup', () => { c.style.transform = 'scale(1)' }, true)
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mk); else mk()

  const mkLabel = () => {
    if (document.getElementById('__lab')) return
    const l = document.createElement('div')
    l.id = '__lab'
    l.innerHTML = '<span id="__labt"></span>'
    Object.assign(l.style, { position: 'fixed', left: '32px', bottom: '36px', zIndex: '2147483646', pointerEvents: 'none',
      background: 'rgba(17,17,17,0.9)', color: '#fff', font: '600 22px/1.25 Inter, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif',
      letterSpacing: '-0.01em', padding: '14px 24px 14px 22px', borderRadius: '14px', borderLeft: '6px solid #0ea47a',
      boxShadow: '0 8px 28px rgba(0,0,0,0.22)', opacity: '0', transition: 'opacity 300ms ease', whiteSpace: 'nowrap' })
    document.documentElement.appendChild(l)
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mkLabel); else mkLabel()
  window.__z = { s: 1, ox: 0, oy: 0 }
})()
`

export async function open(name) {
  const dir = `${S2}/raw/${name}`
  rmSync(dir, { recursive: true, force: true }); mkdirSync(dir, { recursive: true })
  const browser = await chromium.launch()
  const ctx = await browser.newContext({ viewport: { width: W, height: H }, colorScheme: 'light', recordVideo: { dir, size: { width: W, height: H } } })
  await ctx.addInitScript(INIT)
  const page = await ctx.newPage()
  const t0 = Date.now()
  const marks = {}
  const mark = (k) => { marks[k] = (Date.now() - t0) / 1000; console.log('mark', k, marks[k]) }
  const finish = async () => {
    const v = page.video()
    await ctx.close(); await browser.close()
    const video = await v.path()
    writeFileSync(`${S2}/raw/${name}.json`, JSON.stringify({ video, marks }))
    console.log('video', video, marks)
  }
  return { browser, ctx, page, mark, finish }
}

export async function prep(page) {
  await page.goto(BASE)
  await page.getByTestId('node-gateway').waitFor()
  await page.evaluate(() => {
    document.documentElement.style.overflow = 'hidden'
    document.body.style.overflow = 'hidden'
  })
  await page.waitForTimeout(800)
}

/** Unzoomed (page) box of the union of the given locators. */
export async function boxOf(page, locs) {
  const boxes = []
  for (const l of locs) {
    const b = await l.boundingBox()
    if (!b) throw new Error('no box for ' + l)
    boxes.push(b)
  }
  const z = await page.evaluate(() => window.__z)
  const inv = (x, o) => o + (x - o) / z.s
  const x0 = Math.min(...boxes.map((b) => inv(b.x, z.ox)))
  const y0 = Math.min(...boxes.map((b) => inv(b.y, z.oy)))
  const x1 = Math.max(...boxes.map((b) => inv(b.x + b.width, z.ox)))
  const y1 = Math.max(...boxes.map((b) => inv(b.y + b.height, z.oy)))
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }
}

/** Zoom the page (transform on <body>, the cursor overlay stays unscaled) so the box is centred. */
export async function zoomToBox(page, box, scale, pad = 16, label = '') {
  const fit = Math.min(scale, W / (box.w + 2 * pad), H / (box.h + 2 * pad))
  const s = Math.max(1, fit)
  if (s < scale) console.log(`zoom ${label}: scale ${scale} reduced to ${s.toFixed(2)} to fit ${Math.round(box.w)}x${Math.round(box.h)}`)
  const cx = box.x + box.w / 2, cy = box.y + box.h / 2
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v))
  const left = clamp(cx - W / (2 * s), 0, W - W / s)
  const top = clamp(cy - H / (2 * s), 0, H - H / s)
  const ox = s > 1 ? (left * s) / (s - 1) : W / 2
  const oy = s > 1 ? (top * s) / (s - 1) : H / 2
  await page.evaluate(({ s, ox, oy }) => {
    const b = document.body
    b.style.transition = 'transform 700ms ease, transform-origin 700ms ease'
    b.style.transformOrigin = `${ox}px ${oy}px`
    b.style.transform = `scale(${s})`
    window.__z = { s, ox, oy }
  }, { s, ox, oy })
  console.log(`zoom ${label} s=${s.toFixed(2)} box=${JSON.stringify(box)}`)
  await page.waitForTimeout(800)
  return s
}

export async function zoomTo(page, locs, scale, pad, label) {
  return zoomToBox(page, await boxOf(page, Array.isArray(locs) ? locs : [locs]), scale, pad, label)
}

export async function zoomOut(page) {
  await page.evaluate(() => {
    const b = document.body
    b.style.transition = 'transform 700ms ease'
    b.style.transform = 'scale(1)'
    window.__z = { s: 1, ox: window.__z.ox, oy: window.__z.oy }
  })
  await page.waitForTimeout(800)
}

let mouse = { x: 960, y: 540 }
export async function moveTo(page, loc, steps = 25) {
  const b = await loc.boundingBox()
  const x = b.x + b.width / 2, y = b.y + b.height / 2
  await page.mouse.move(x, y, { steps })
  mouse = { x, y }
  await page.waitForTimeout(70)
}
export async function clickIt(page, loc, steps = 25) {
  await moveTo(page, loc, steps)
  await loc.click()
  await blur(page)
}
/** Drop focus so a Radix tooltip that opens on focus (a trigger regaining focus) never shows. */
export const blur = (page) => page.evaluate(() => { const a = document.activeElement; if (a && a !== document.body && a.id !== 'composer-input' && a.getAttribute('data-testid') !== 'composer-input') a.blur() })
export async function parkMouse(page, x, y, steps = 20) { await page.mouse.move(x, y, { steps }) }
export const wait = (page, ms) => page.waitForTimeout(ms)

/** Wait for the next step-mode pause (seq above `last`) or the end of the turn. */
export async function nextPause(page, last) {
  const r = await page.waitForFunction((l) => {
    const s = document.querySelector('[data-testid=turn-status]')?.getAttribute('data-phase')
    if (s === 'done') return 'done'
    const e = document.querySelector('[data-testid=step-controls]')
    return e && Number(e.getAttribute('data-seq')) > l ? e.getAttribute('data-seq') + '|' + e.getAttribute('data-node') : false
  }, last, { timeout: 30000 })
  const v = await r.jsonValue()
  if (v === 'done') return { done: true }
  const [seq, node] = v.split('|')
  console.log('paused at', node)
  return { seq: Number(seq), node }
}

/** Smoothly scroll the nearest scrollable ancestor of the locator by dy over ms. */
export async function smoothScroll(loc, dy, ms) {
  await loc.evaluate((el, { dy, ms }) => new Promise((res) => {
    let p = el
    while (p && !(p.scrollHeight > p.clientHeight + 4 && getComputedStyle(p).overflowY !== 'visible')) p = p.parentElement
    if (!p) return res()
    const y0 = p.scrollTop, t0 = performance.now()
    const ease = (t) => (t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2)
    const step = (now) => {
      const t = Math.min(1, (now - t0) / ms)
      p.scrollTop = y0 + dy * ease(t)
      if (t < 1) requestAnimationFrame(step); else res()
    }
    requestAnimationFrame(step)
  }), { dy, ms })
}

/** Smoothly scroll an element (found by testid-free locator) by dx/dy over ms. */
export async function smoothScrollEl(loc, dx, dy, ms) {
  await loc.evaluate((p, { dx, dy, ms }) => new Promise((res) => {
    const x0 = p.scrollLeft, y0 = p.scrollTop, t0 = performance.now()
    const ease = (t) => (t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2)
    const step = (now) => {
      const t = Math.min(1, (now - t0) / ms)
      p.scrollLeft = x0 + dx * ease(t); p.scrollTop = y0 + dy * ease(t)
      if (t < 1) requestAnimationFrame(step); else res()
    }
    requestAnimationFrame(step)
  }), { dx, dy, ms })
}

/** Fade the bottom-left label to new text (300 ms out, 300 ms in). Empty text hides it. */
export async function label(page, text) {
  await page.evaluate(async (text) => {
    const l = document.getElementById('__lab'), t = document.getElementById('__labt')
    if (l.style.opacity !== '0') { l.style.opacity = '0'; await new Promise((r) => setTimeout(r, 300)) }
    t.textContent = text
    if (text) l.style.opacity = '1'
  }, text)
  console.log('label', JSON.stringify(text))
}
