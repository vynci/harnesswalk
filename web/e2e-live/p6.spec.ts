import { expect, test, type Page } from '@playwright/test'
import { Harness, node, send, status } from './helpers'

// Phase 6 against the real harness in public mode (fake provider, a test secret): a turn on
// fake-small, its markdown reply rendered (the fixture in e2e-live/fixtures), and the
// private-only controls disabled with their reason. Until the P6 API lands, main's public mode
// has no `capabilities.public` and refuses a `model` on a turn; the UI then shows no picker and
// names no model, and the checks that need the P6 API are skipped by the capabilities shape.

const PORT = Number(process.env.HARNESSWALK_E2E_PORT ?? 8765)
// Not a real secret: the signing key of the test server's anonymous-tenant cookie.
const SECRET = 'e2e-p6-test-secret-not-for-production-0000000000'

let h: Harness

test.beforeAll(async () => {
  h = new Harness(PORT + 5, 'web/e2e-live/profile-public.yaml', {
    mode: 'public',
    fixture: 'markdown_reply',
    // No hosted model is ever called: no site key, whatever the caller's shell has.
    env: { HARNESSWALK_SECRET: SECRET, ANTHROPIC_API_KEY: '' },
  })
  await h.start()
})

test.afterAll(() => h?.stop())

interface Caps {
  mode: string
  public?: { models?: { alias: string; available: boolean }[]; session_expires_at?: string | null }
}

async function open(page: Page): Promise<Caps> {
  await page.goto(h.url)
  await expect(page.getByTestId('mode-badge')).toContainText('public')
  await expect(node(page, 'gateway')).toBeVisible()
  return (await (await page.request.get(`${h.url}/v1/capabilities`)).json()) as Caps
}

test('a turn on fake-small: the request names the model, and the markdown reply renders', async ({ page }) => {
  const caps = await open(page)
  const p6 = Array.isArray(caps.public?.models)
  if (p6) {
    // The P6 API: the picker lists the site models; pick the fake one.
    const picker = page.getByTestId('model-picker')
    await picker.click()
    await page.locator('[data-testid=model-option][data-model="fake-small"]').click()
    await expect(picker).toHaveAttribute('data-model', 'fake-small')
  } else {
    await expect(page.getByTestId('model-picker')).toHaveCount(0)
  }
  const request = page.waitForRequest((r) => r.url().endsWith('/v1/turns') && r.method() === 'POST')
  await send(page, 'Show me my orders as a table.')
  const body = (await request).postDataJSON() as { model?: string }
  expect(body.model).toBe(p6 ? 'fake-small' : undefined)
  await expect(status(page)).toHaveAttribute('data-phase', 'done')

  const reply = page.getByTestId('assistant-message').last()
  await expect(reply.getByRole('heading', { name: 'Order summary' })).toBeVisible()
  const table = reply.getByTestId('markdown-table').getByRole('table')
  await expect(table.getByRole('columnheader')).toHaveText(['Order', 'Status', 'Total'])
  await expect(table.getByRole('row').nth(2).getByRole('cell')).toHaveText(['A-1002', 'processing', '$18.50'])
  await expect(reply.getByTestId('markdown-code')).toContainText('"status": "shipped"')
  await expect(reply.getByRole('link', { name: /returns policy/ })).toHaveAttribute('target', '_blank')
  // No raw markdown left over.
  await expect(reply).not.toContainText('| --- |')
  await expect(reply).not.toContainText('**A-1001**')
})

test('the sandbox countdown and the limits card follow capabilities.public', async ({ page }) => {
  const caps = await open(page)
  test.skip(!caps.public?.session_expires_at, 'needs the P6 API (capabilities.public)')
  await expect(page.getByTestId('session-countdown')).toContainText(/left, then everything here is deleted/)
  await page.getByTestId('budget-trigger').hover()
  await expect(page.getByTestId('limits-card').getByTestId('limit-remaining')).toHaveText(/^\d+ \/ \d+$/)
})

test('private-only controls stay visible and disabled with their reason; read-only views stay live', async ({ page }) => {
  await open(page)
  const attach = page.getByTestId('attach-button')
  await expect(attach).toHaveAttribute('aria-disabled', 'true')
  await attach.hover()
  await expect(page.getByRole('tooltip')).toHaveText('Uploads need a private workspace.')

  await node(page, 'input_guardrails').click()
  const inspector = page.getByTestId('inspector')
  const off = inspector.getByTestId('disable-node-off')
  await off.hover()
  await expect(page.getByRole('tooltip')).toHaveText('Turning nodes off needs a private workspace. Coming later.')

  await node(page, 'policy').click()
  await inspector.getByRole('tab', { name: 'Config' }).click()
  const edit = inspector.getByTestId('artifact-edit')
  await expect(edit).toHaveAttribute('aria-disabled', 'true')
  await edit.hover()
  await expect(page.getByRole('tooltip')).toHaveText('Editing needs a private workspace. Coming later.')

  await node(page, 'eval').click()
  const run = inspector.getByTestId('eval-run-button')
  await expect(run).toHaveAttribute('aria-disabled', 'true')
  await run.hover()
  await expect(page.getByRole('tooltip')).toHaveText('Eval runs need a private workspace. Coming later.')

  // The releases list is read-only and live; creating one is not.
  await node(page, 'release').click()
  await expect(inspector.getByTestId('release-create')).toHaveAttribute('aria-disabled', 'true')
  await expect(inspector.getByText('r1').first()).toBeVisible()
})
