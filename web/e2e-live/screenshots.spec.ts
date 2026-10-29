import { fileURLToPath } from 'node:url'
import { expect, test, type Page } from '@playwright/test'

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
  await page.mouse.move(0, 0)
  await page.waitForTimeout(400) // let the refit and edge animation come to rest
}

test('a finished turn', async ({ page }) => {
  await turn(page, 'tool_call', 'Where is order A-1001?')
  await expect(page.getByTestId('turn-status')).toHaveAttribute('data-phase', 'done')
  await settle(page)
  await page.screenshot({ path: out('turn-done') })
})

test('the approval card', async ({ page }) => {
  await turn(page, 'approval_approve', 'Please cancel order A-1002.')
  await expect(page.getByTestId('approval-card').last()).toContainText('Approval needed')
  await expect(page.getByTestId('node-approval')).toHaveAttribute('data-state', 'waiting')
  await settle(page)
  await page.screenshot({ path: out('approval-card') })
})

test('the inspector code tab', async ({ page }) => {
  await turn(page, 'tool_call', 'Where is order A-1001?')
  await expect(page.getByTestId('turn-status')).toHaveAttribute('data-phase', 'done')
  await page.getByTestId('node-loop').click()
  const inspector = page.getByTestId('inspector')
  await inspector.getByRole('tab', { name: 'Code' }).click()
  await expect(inspector.getByTestId('code-view')).toContainText('class Loop')
  await settle(page)
  await page.screenshot({ path: out('inspector-code') })
})
