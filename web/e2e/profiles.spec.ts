import { expect, test, type Page } from '@playwright/test'

// 0.2.0 harness profiles on the mock build: the profile chip beside the pack picker, the nodes
// a pack's profile switches off dimmed (never hidden) with their edges, the tooltip naming the
// profile, and a Guide chapter whose node is off greyed with the rung that has it.
const URL = '/?mockStepMs=10'
const node = (page: Page, id: string) => page.getByTestId(`node-${id}`)

async function pickPack(page: Page, id: string) {
  await page.getByTestId('pack-picker').click()
  await page.locator(`[data-testid=pack-option][data-pack="${id}"]`).click()
  await expect(page.getByTestId('pack-picker')).toHaveAttribute('data-pack', id)
}

test.beforeEach(async ({ page }) => {
  await page.goto(URL)
  await expect(page.getByTestId('mode-badge')).toContainText('dev')
  await expect(node(page, 'gateway')).toBeVisible()
})

test('the starter pack runs on operated: every node lit, the chip says 6/6', async ({ page }) => {
  const chip = page.getByTestId('profile-chip')
  await expect(chip).toHaveAttribute('data-profile', 'operated')
  await expect(chip).toHaveText(/operated\s*6\/6/)
  await expect(page.locator('[data-profile-off="true"]')).toHaveCount(0)
  await page.getByTestId('pack-picker').click()
  await expect(page.locator('[data-testid=pack-option][data-pack="healthcare"]').getByTestId('pack-profile')).toHaveText(/grounded profile/)
  await page.keyboard.press('Escape')
})

test('a pack on a lower rung dims what its profile switches off, and hides nothing', async ({ page }) => {
  const before = await page.locator('[data-testid^="node-"]').count()
  await pickPack(page, 'healthcare')
  const chip = page.getByTestId('profile-chip')
  await expect(chip).toHaveAttribute('data-profile', 'grounded')
  await expect(chip).toHaveAttribute('data-rung', '3')
  // grounded switches off governance, durable memory and the ops plane's runtime side.
  for (const id of ['policy', 'approval', 'audit', 'memory_writer', 'eval', 'release']) {
    await expect(node(page, id)).toHaveAttribute('data-profile-off', 'true')
    await expect(node(page, id)).toBeVisible()
    await expect(node(page, id)).toHaveAttribute('title', 'Off in profile grounded')
    await expect(page.getByTestId(`profile-off-${id}`)).toHaveText('off')
  }
  for (const id of ['gateway', 'input_guardrails', 'output_guardrails', 'loop', 'tools', 'trace']) {
    await expect(node(page, id)).not.toHaveAttribute('data-profile-off', 'true')
  }
  await expect(page.locator('[data-testid^="node-"]')).toHaveCount(before)
  // An edge into a switched-off node is dimmed with it; one between two lit nodes is not.
  await expect(page.getByTestId('edge-policy_to_audit')).toHaveAttribute('data-dim', 'true')
  await expect(page.getByTestId('edge-loop_to_model_layer')).not.toHaveAttribute('data-dim', 'true')

  await pickPack(page, '_starter')
  await expect(chip).toHaveAttribute('data-profile', 'operated')
  await expect(page.locator('[data-profile-off="true"]')).toHaveCount(0)
})

test('the Guide greys a chapter whose node is off and names the rung that has it', async ({ page }) => {
  await pickPack(page, 'healthcare')
  await page.getByTestId('guide-button').click()
  const guide = page.getByTestId('guide-drawer')
  const policy = guide.locator('[data-testid=lesson-row][data-slug="08-policy"]')
  await expect(policy).toHaveAttribute('data-profile-off', 'true')
  await expect(policy.getByTestId('lesson-row-profile')).toHaveText('policy is off in profile grounded. Switch to a pack on the governed rung (5) or higher to try it.')
  const input = guide.locator('[data-testid=lesson-row][data-slug="02-input_guardrails"]')
  await expect(input).not.toHaveAttribute('data-profile-off', 'true')
  // Greyed, not locked: the chapter still opens, with the same note under its title.
  await policy.click()
  await expect(guide.getByTestId('lesson-profile-off')).toContainText('off in profile grounded')
})
