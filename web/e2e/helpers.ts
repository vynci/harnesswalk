import { expect, type Locator, type Page } from '@playwright/test'

// Shared steps for the mock suite.

/**
 * Open the session chip's popover (the header's top right) and return it. The status bar keeps
 * only the phase, the exit when it is not `answered`, the duration and the budget readout; the
 * turn's other facts (tokens, cost, tool calls, events, the release) are in its This turn card.
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
