import { expect, type Locator, type Page } from '@playwright/test'

// Shared steps for the mock suite.

/**
 * Open the session chip's popover (the header's top right) and return it: the session, its
 * totals, the sandbox's limits (public mode) and the release line (`release-status`).
 */
export async function openSessionChip(page: Page): Promise<Locator> {
  const pop = page.getByTestId('session-popover')
  if (!(await pop.isVisible())) await page.getByTestId('session-menu').click()
  await expect(pop).toBeVisible()
  return pop
}

/**
 * Close the session chip's popover, if it is open. The chip's tooltip, shown while the pointer
 * rests on it, is the top layer and takes the first Escape, so press again until it closes.
 */
export async function closeSessionChip(page: Page) {
  const pop = page.getByTestId('session-popover')
  await expect(async () => {
    if (await pop.isVisible()) await page.keyboard.press('Escape')
    await expect(pop).toBeHidden({ timeout: 500 })
  }).toPass()
}

/**
 * Open a turn's report and return its card. With no index, the status bar's readout opens the
 * report of the turn shown in a popover; with an index (1 for the session's first turn), that
 * turn's row on the chat timeline opens. Both hold the facts (turn-exit, turn-duration,
 * turn-tool-calls, turn-events, turn-tokens, turn-cost), the budget-row bars and the release.
 */
export async function openTurnReport(page: Page, n?: number): Promise<Locator> {
  if (n === undefined) {
    const pop = page.getByTestId('turn-report-popover')
    if (!(await pop.isVisible())) await page.getByTestId('budget-trigger').click()
    await expect(pop).toBeVisible()
    return pop
  }
  const row = page.locator(`[data-testid="turn-report"][data-turn="${n}"]`)
  if ((await row.getAttribute('data-open')) !== 'true') await row.getByTestId('turn-report-toggle').click()
  await expect(row).toHaveAttribute('data-open', 'true')
  const card = row.getByTestId('turn-report-card')
  await expect(card).toBeVisible()
  return card
}

/** Close the status bar's turn report popover, if it is open (Escape, past the readout's tooltip). */
export async function closeTurnReport(page: Page) {
  const pop = page.getByTestId('turn-report-popover')
  await expect(async () => {
    if (await pop.isVisible()) await page.keyboard.press('Escape')
    await expect(pop).toBeHidden({ timeout: 500 })
  }).toPass()
}
