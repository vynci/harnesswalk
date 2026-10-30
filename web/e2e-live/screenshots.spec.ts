import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { expect, test, type Page } from '@playwright/test'
import { settleTurns } from './helpers'

// The README screenshots, taken from the real API in light mode. Skipped unless
// HARNESSWALK_SCREENSHOTS=1 (make screenshots), so the e2e run never rewrites them.
const out = (name: string) => fileURLToPath(new URL(`../../docs/screenshots/${name}.png`, import.meta.url))

test.skip(process.env.HARNESSWALK_SCREENSHOTS !== '1', 'set HARNESSWALK_SCREENSHOTS=1 to write docs/screenshots')

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    try {
      localStorage.setItem('harnesswalk.theme', JSON.stringify('light'))
    } catch {
      // Storage is optional.
    }
  })
  await page.goto('/')
  await expect(page.getByTestId('node-gateway')).toBeVisible()
})

async function turn(page: Page, fixture: string, text: string) {
  await page.getByTestId('fixture-select').click()
  await page.getByRole('option', { name: fixture, exact: true }).click()
  await page.getByTestId('composer-input').fill(text)
  await page.getByTestId('send-button').click()
}

async function settle(page: Page) {
  // Toasts are transient: close them so the picture shows the page, not the last notice.
  for (const close of await page.getByRole('button', { name: 'Close toast' }).all()) await close.click({ timeout: 1000 }).catch(() => {})
  await page.mouse.move(0, 0)
  await page.waitForTimeout(400) // let the refit and edge animation come to rest
}

test('a finished turn', async ({ page, browser }) => {
  // The Trailhead question: all five memory badges lit, and a reply that cites knowledge.
  await turn(page, 'trailhead_return', 'Can I return order A-1003, the Trailhead backpack?')
  await expect(page.getByTestId('turn-status')).toHaveAttribute('data-phase', 'done')
  await expect(page.getByTestId('badge-knowledge')).toHaveAttribute('data-lit', 'true')
  await settle(page)
  await page.screenshot({ path: out('turn-done') })

  // citations.png: a citation chip opened the document reader at the cited chunk.
  await page.getByTestId('citation-chip').first().click()
  await expect(page.getByTestId('doc-reader').locator('[data-cited="true"]')).toBeVisible()
  await page.waitForTimeout(400)
  await page.screenshot({ path: out('citations') })
  await page.keyboard.press('Escape')

  // edges-before-after.png: the owner's screenshot of the old routing (edges-before.png, kept in
  // the repo) beside the same diagram now, at twice the pixel density for a fair comparison.
  const hi = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2, colorScheme: 'light' })
  await hi.addInitScript(() => localStorage.setItem('harnesswalk.theme', JSON.stringify('light')))
  await hi.goto('/')
  await expect(hi.getByTestId('node-gateway')).toBeVisible()
  await turn(hi, 'tool_call', 'Where is order A-1001?')
  await expect(hi.getByTestId('turn-status')).toHaveAttribute('data-phase', 'done')
  await settle(hi)
  const after = await hi.locator('.react-flow').screenshot()
  await hi.close()
  const before = readFileSync(out('edges-before'))
  const img = (b: Buffer) => `data:image/png;base64,${b.toString('base64')}`
  const sheet = await browser.newPage({ viewport: { width: 1800, height: 800 } })
  await sheet.setContent(`<!doctype html><html><body style="margin:0;background:#fff;font:600 18px system-ui,sans-serif;color:#222">
    <div style="display:flex;gap:24px;padding:20px;align-items:flex-start">
      <figure style="margin:0;flex:1"><figcaption style="margin-bottom:10px">Before: owner feedback, 2026-10-29</figcaption>
        <img src="${img(before)}" style="width:100%;border:1px solid #ddd;border-radius:8px"></figure>
      <figure style="margin:0;flex:1"><figcaption style="margin-bottom:10px">After: hinted routes, kind and state colours, telemetry bus</figcaption>
        <img src="${img(after)}" style="width:100%;border:1px solid #ddd;border-radius:8px"></figure>
    </div></body></html>`)
  await sheet.screenshot({ path: out('edges-before-after'), fullPage: true })
  await sheet.close()
})

test('a finished turn in the expert view', async ({ page }) => {
  await page.getByRole('switch', { name: 'Expert view' }).setChecked(true)
  await turn(page, 'tool_call', 'Where is order A-1001?')
  await expect(page.getByTestId('turn-status')).toHaveAttribute('data-phase', 'done')
  await settle(page)
  await page.screenshot({ path: out('turn-done-expert') })
})

test('the approval card', async ({ page }) => {
  await turn(page, 'approval_approve', 'Please cancel order A-1002.')
  await expect(page.getByTestId('approval-card').last()).toContainText('Run cancel_order?')
  await expect(page.getByTestId('node-approval')).toHaveAttribute('data-state', 'waiting')
  await settle(page)
  await page.screenshot({ path: out('approval-card') })
})

test('the chat panel', async ({ page }) => {
  // chat.png: the AI Elements look: muted user bubble, a tool line opened to its details, the
  // reply without a bubble, the Replay action and the composer with the plus and arrow-up send.
  await turn(page, 'tool_call', 'Where is order A-1001?')
  await expect(page.getByTestId('turn-status')).toHaveAttribute('data-phase', 'done')
  await page.getByTestId('tool-line').last().click()
  await expect(page.getByTestId('tool-details')).toBeVisible()
  await settle(page)
  await page.getByRole('region', { name: 'Chat' }).screenshot({ path: out('chat') })
})

test('the inspector code tab', async ({ page }) => {
  await turn(page, 'tool_call', 'Where is order A-1001?')
  await expect(page.getByTestId('turn-status')).toHaveAttribute('data-phase', 'done')
  await page.getByTestId('node-loop').click()
  const inspector = page.getByTestId('inspector')
  await inspector.getByRole('tab', { name: 'Code' }).click()
  await expect(inspector.getByTestId('code-view')).toContainText('class Loop')
  // shiki loads on first use; wait for the coloured tokens.
  await expect(inspector.getByTestId('code-view').locator('[style*="--shiki-light"]').first()).toBeAttached()
  // Jump past the module docstring to the class itself.
  await inspector.getByRole('navigation', { name: 'Symbols' }).getByRole('button').filter({ hasText: /^Loop:\d+$/ }).click()
  await page.waitForTimeout(600)
  await settle(page)
  await page.screenshot({ path: out('inspector-code') })
})

test('the eval run and the release diff', async ({ page, request }) => {
  // eval-run.png and release-diff.png: the P4 panels, once the API offers them.
  const caps = (await (await request.get('/v1/capabilities')).json()) as { features: Record<string, boolean | undefined> }
  test.skip(!(caps.features.evals && caps.features.release && caps.features.artifact_edit), 'the API has no P4 features yet')
  test.setTimeout(120_000)
  await settleTurns(request) // the approval card above leaves a turn waiting; edits wait for none
  const inspector = page.getByTestId('inspector')
  const releases = (await (await request.get('/v1/releases')).json()) as { releases?: { release_id: string; active: boolean }[] }
  const base = releases.releases?.find((r) => r.active)?.release_id

  await page.getByTestId('node-eval').click()
  await inspector.getByTestId('eval-run-button').click()
  await expect(inspector.getByTestId('eval-run-detail')).toHaveAttribute('data-status', 'done', { timeout: 60_000 })

  await page.getByRole('switch', { name: 'Expert view' }).setChecked(true)
  await page.getByTestId('node-procedural').click()
  await inspector.getByRole('tab', { name: 'Prompt' }).click()
  // The README's gate walk: the returns skill's window changes, so returns_window regresses.
  const editor = inspector.getByTestId('artifact-editor').and(page.locator('[data-name="returns"]'))
  await editor.getByTestId('artifact-edit').click()
  const textarea = editor.getByTestId('artifact-textarea')
  await textarea.fill((await textarea.inputValue()).replace('within 30 days of delivery', 'within 14 days of delivery'))
  await editor.getByTestId('artifact-note').fill('returns: 14 day window')
  await editor.getByTestId('artifact-save').click()
  await expect(editor.getByTestId('artifact-saved')).toBeVisible()

  await page.getByRole('switch', { name: 'Expert view' }).setChecked(false)
  await page.getByTestId('node-eval').click()
  await inspector.getByTestId('eval-run-button').click()
  const detail = inspector.getByTestId('eval-run-detail')
  await expect(detail).toHaveAttribute('data-status', 'done', { timeout: 60_000 })
  await detail.locator('[data-testid="eval-case"][data-regression="true"]').first().click()
  await settle(page)
  await page.screenshot({ path: out('eval-run') })

  await page.getByTestId('node-release').click()
  await inspector.getByTestId('release-note').fill('returns: 14 day window')
  await inspector.getByTestId('release-create').click()
  await expect(page.getByTestId('ops-marker-text').last()).toHaveText(/^Release r\d+ active$/)
  await inspector.getByTestId('release-commit').filter({ hasText: 'Skill returns' }).click()
  await settle(page)
  await page.screenshot({ path: out('release-diff') })

  // Leave the harness as it was for any test after this one.
  if (base) await request.post(`/v1/releases/${base}/rollback`, { data: {} })
})
