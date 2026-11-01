import { expect, test } from '@playwright/test'
import { node, pickFixture, send, turnDone } from './helpers'

// Payloads against the real harness (tracer.md, "Payloads"): the gateway's record of the message
// as received and after the input guardrails replaced the email, and the context the model saw.
// Fake provider, dev mode (the PII restore rule keeps the text as typed in dev).

const EMAIL = 'ana.lopez@example.com'

test.beforeEach(async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('mode-badge')).toContainText('dev')
  await expect(node(page, 'gateway')).toBeVisible()
})

test.afterEach(async ({ page }) => {
  await expect(page.getByText(/invalid events/)).toHaveCount(0)
})

test('the gateway payload shows the message as received and after redaction', async ({ page }) => {
  await pickFixture(page, 'pii_redacted')
  await send(page, `Please email the receipt for order A-1001 to ${EMAIL}.`)
  await turnDone(page)
  await node(page, 'gateway').click()
  const inspector = page.getByTestId('inspector')
  await expect(inspector.getByRole('tab').nth(1)).toHaveText('Payload')
  await inspector.getByRole('tab', { name: 'Payload' }).click()
  await expect(inspector.getByTestId('payload-side-by-side')).toBeVisible()
  await expect(inspector.getByTestId('payload-received')).toContainText(EMAIL)
  await expect(inspector.getByTestId('payload-redacted')).toContainText('[EMAIL_1]')
  await expect(inspector.getByTestId('payload-redacted')).not.toContainText(EMAIL)
  await expect(inspector.getByTestId('payload-replaced')).toContainText('1 value was replaced with placeholders (1 email)')
  await expect(inspector.getByTestId('payload-reply')).toBeVisible()

  // The context builder assembled the redacted text, never the email.
  await node(page, 'context_builder').click()
  await expect(inspector.getByRole('tab', { name: 'Payload' })).toBeVisible()
  await inspector.getByRole('tab', { name: 'Payload' }).click()
  const user = inspector.getByTestId('payload-section').last()
  await expect(user).toHaveAttribute('data-kind', 'user')
  await expect(user.getByTestId('section-text')).toContainText('[EMAIL_1]')
  await expect(inspector.getByTestId('payload-sections')).not.toContainText(EMAIL)
  await expect(inspector.getByTestId('payload-totals')).toContainText('tokens of a 16,000 budget')

  // The input guardrails list each check; pii says what it changed.
  await node(page, 'input_guardrails').click()
  await inspector.getByRole('tab', { name: 'Payload' }).click()
  const pii = inspector.locator('[data-testid=payload-check][data-check=pii]')
  await expect(pii.getByTestId('verdict-chip')).toHaveText('redact')
  await expect(pii).toContainText('replaced 1 email with placeholders')
})
