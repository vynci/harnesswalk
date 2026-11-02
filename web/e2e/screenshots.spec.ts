import { expect, test, type Page } from '@playwright/test'
import { closeSessionChip, openSessionChip } from './helpers'

// Screenshots of the P4 panels from the mock build, for review: the eval run and the release
// diff. Skipped unless HARNESSWALK_MOCK_SHOTS names a directory, so the e2e run writes nothing.
// The README screenshots (docs/screenshots, eval-run.png and release-diff.png) are taken from
// the real API by e2e-live/screenshots.spec.ts.
const dir = process.env.HARNESSWALK_MOCK_SHOTS
test.skip(!dir, 'set HARNESSWALK_MOCK_SHOTS=<dir> to write review screenshots')
test.use({ viewport: { width: 1440, height: 900 } })

const shot = (page: Page, name: string) => page.screenshot({ path: `${dir}/${name}.png` })

async function settle(page: Page) {
  await page.mouse.move(0, 0)
  await page.waitForTimeout(500)
}

async function editSkill(page: Page) {
  await page.getByRole('switch', { name: 'Advanced view' }).setChecked(true)
  await page.getByTestId('node-procedural').click()
  const inspector = page.getByTestId('inspector')
  await inspector.getByRole('tab', { name: 'Prompt' }).click()
  const editor = inspector.getByTestId('artifact-editor').and(page.locator('[data-name="order_status"]'))
  await editor.getByTestId('artifact-edit').click()
  const textarea = editor.getByTestId('artifact-textarea')
  await textarea.fill((await textarea.inputValue()).replaceAll('lookup_order', 'find_order'))
  return editor
}

for (const theme of ['light', 'dark'] as const) {
  test(`P4 panels, ${theme}`, async ({ page }) => {
    await page.addInitScript((t) => localStorage.setItem('harnesswalk.theme', JSON.stringify(t)), theme)
    await page.goto('/?mockStepMs=10')
    await expect(page.getByTestId('node-gateway')).toBeVisible()

    const editor = await editSkill(page)
    await editor.getByRole('button', { name: /Review the change/ }).click()
    await settle(page)
    await shot(page, `artifact-edit-${theme}`)
    await editor.getByTestId('artifact-save').click()
    await expect(editor.getByTestId('artifact-saved')).toBeVisible()
    await settle(page)
    await shot(page, `artifact-saved-${theme}`)

    await page.getByRole('switch', { name: 'Advanced view' }).setChecked(false)
    await page.getByTestId('node-eval').click()
    const inspector = page.getByTestId('inspector')
    await inspector.getByTestId('eval-run-button').click()
    await expect(inspector.getByTestId('eval-run-detail')).toHaveAttribute('data-status', 'done')
    await inspector.getByRole('button', { name: /Order status: shipped/ }).click()
    await settle(page)
    await shot(page, `eval-run-${theme}`)

    await page.getByTestId('node-release').click()
    await inspector.getByTestId('release-note').fill('find_order experiment')
    await inspector.getByTestId('release-create').click()
    // The gate refuses the regressed working set; release it anyway (dev mode, audited).
    await inspector.getByTestId('release-force').click()
    await expect(inspector.getByTestId('release-detail')).toHaveAttribute('data-release', 'r2')
    await inspector.getByTestId('release-commit').click()
    await settle(page)
    await shot(page, `release-diff-${theme}`)
    await inspector.locator('[data-testid="release-row"][data-release="r1"]').click()
    await inspector.getByTestId('release-rollback').click()
    await settle(page)
    await shot(page, `release-rollback-confirm-${theme}`)
    await inspector.getByTestId('rollback-confirm').getByRole('button', { name: 'Roll back' }).click()
    await expect((await openSessionChip(page)).getByTestId('release-status')).toHaveText('Release r1')
    await closeSessionChip(page)

    await page.getByTestId('node-trace').click()
    await inspector.getByTestId('trace-turn').first().click()
    await expect(inspector.getByTestId('trace-receipt')).toBeVisible()
    await settle(page)
    await shot(page, `trace-${theme}`)

    await page.getByTestId('chat-turn').nth(1).getByTestId('feedback-bad').click()
    await settle(page)
    await page.getByRole('region', { name: 'Chat' }).screenshot({ path: `${dir}/chat-ops-${theme}.png` })
  })
}

// The header and the node cards mid-turn: one capture paused in step mode (the waiting node's
// dashed ring), the legend dialog over it, and one while a node runs (the active ring), so the
// current node reads at a glance.
for (const theme of ['light', 'dark'] as const) {
  test(`mid-turn nodes, ${theme}`, async ({ page }) => {
    await page.addInitScript((t) => localStorage.setItem('harnesswalk.theme', JSON.stringify(t)), theme)
    await page.goto('/?mockStepMs=1500')
    await expect(page.getByTestId('node-gateway')).toBeVisible()

    await page.getByRole('switch', { name: /Step mode/ }).click()
    await page.getByTestId('composer-input').fill('Where is order A-1001?')
    await page.getByTestId('send-button').click()
    const controls = page.getByTestId('step-controls')
    await expect(controls).toContainText('Gateway')
    for (const next of ['Input guardrails', 'Context builder']) {
      await controls.getByRole('button', { name: 'Next node' }).click()
      await expect(controls).toContainText(next)
    }
    await settle(page)
    await shot(page, `step-mode-${theme}`)
    // The legend Dialog, opened from the canvas controls.
    await page.getByRole('group', { name: 'Diagram controls' }).getByRole('button', { name: 'Legend' }).click()
    await expect(page.getByRole('dialog', { name: 'Legend' })).toBeVisible()
    await settle(page)
    await shot(page, `legend-${theme}`)
    await page.keyboard.press('Escape')

    await page.getByRole('switch', { name: /Step mode/ }).click()
    await controls.getByRole('button', { name: 'Continue to the end' }).click()
    await expect(page.locator('[data-testid^="node-"][data-state="active"]').first()).toBeVisible()
    await page.mouse.move(0, 0)
    await shot(page, `active-node-${theme}`)
  })
}
