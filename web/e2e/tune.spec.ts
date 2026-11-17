import { expect, test, type Page } from '@playwright/test'

// 0.2.0 on the mock build: the Tune tab of the Release panel (datasets, runs with the loss curve,
// the model gate's table, the canary with Promote refused and Rollback), the curate node's link
// to it, and the public site's recorded replay with every action off.
const DEV = '/?mockStepMs=10'
const PUBLIC = '/?mockStepMs=10&mode=public'

async function openTune(page: Page, url: string) {
  await page.goto(url)
  await expect(page.getByTestId('node-gateway')).toBeVisible()
  await page.getByRole('switch', { name: 'Advanced view' }).setChecked(true)
  await page.getByTestId('node-tune').click()
  const inspector = page.getByTestId('inspector')
  await expect(inspector.getByTestId('tune-panel')).toBeVisible()
  return inspector
}

test('the Tune tab shows the datasets, a run, the gate and the canary', async ({ page }) => {
  const inspector = await openTune(page, DEV)
  await expect(inspector.getByRole('tab', { name: 'Tune' }).first()).toHaveAttribute('aria-selected', 'true')
  await expect(inspector.getByTestId('tune-dataset-detail')).toHaveAttribute('data-name', 'approvals')
  await expect(inspector.getByTestId('tune-dataset-counts')).toContainText('142')

  await inspector.getByTestId('tune-section-runs').click()
  await expect(inspector.getByTestId('tune-loss-chart')).toHaveAttribute('data-points', '34')

  await inspector.getByTestId('tune-section-compare').click()
  await inspector.getByTestId('tune-comparison-row').filter({ hasText: 'cmp_1' }).click()
  const row = inspector.getByTestId('tune-compare-row').filter({ hasText: 'ecommerce' })
  await expect(row.locator('[data-arm=base]')).toHaveText('13/24')
  await expect(row.locator('[data-arm=candidate]')).toContainText('24/24')
  await expect(inspector.getByTestId('tune-verdict')).toContainText('Gate passed')

  await inspector.getByTestId('tune-section-canary').click()
  await inspector.getByTestId('tune-promote-candidate').fill('model/forgets_fake')
  await inspector.getByTestId('tune-promote').click()
  await expect(inspector.getByTestId('tune-refusal')).toHaveAttribute('data-code', 'gate_failed')

  await inspector.getByTestId('tune-promote-candidate').fill('model/approvals_fake')
  await inspector.getByTestId('tune-promote-fraction').fill('0.5')
  await inspector.getByTestId('tune-promote').click()
  await expect(inspector.getByTestId('tune-canary-state')).toHaveAttribute('data-fraction', '0.5')
  await inspector.getByTestId('tune-promote-full').click()
  await expect(inspector.getByTestId('tune-refusal')).toHaveAttribute('data-code', 'canary_failed')
  await inspector.getByTestId('tune-rollback').click()
  await inspector.getByTestId('tune-rollback-confirm').getByRole('button', { name: 'Roll back' }).click()
  await expect(inspector.getByTestId('tune-canary-state')).toHaveAttribute('data-fraction', '0')
})

test('the curate node points to the datasets in the Tune tab', async ({ page }) => {
  await page.goto(DEV)
  await expect(page.getByTestId('node-gateway')).toBeVisible()
  await page.getByRole('switch', { name: 'Advanced view' }).setChecked(true)
  await page.getByTestId('node-curate').click()
  const inspector = page.getByTestId('inspector')
  await inspector.getByTestId('curate-open-tune').click()
  await expect(page.getByTestId('inspector-title')).toHaveText('Tune')
  await expect(inspector.getByTestId('tune-datasets')).toBeVisible()
})

test('a public site replays the recorded loop with every action off', async ({ page }) => {
  const inspector = await openTune(page, PUBLIC)
  await expect(inspector.getByTestId('tune-replay-note')).toContainText('never curates, trains')
  await expect(inspector.getByTestId('tune-curate-start')).toHaveCount(0)
  await inspector.getByTestId('tune-section-canary').click()
  await expect(inspector.getByTestId('tune-canary-arms')).toBeVisible()
  await expect(inspector.getByTestId('tune-promote')).toHaveCount(0)
  await expect(inspector.getByTestId('tune-rollback')).toHaveCount(0)
})
