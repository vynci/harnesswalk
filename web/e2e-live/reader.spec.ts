import { expect, test, type Page } from '@playwright/test'
import { node } from './helpers'

// The document reader against the real harness: the healthcare pack's documents open on their
// original files, served by GET /v1/packs/{pack}/files/docs/{name}?raw=1.

async function pickPack(page: Page, id: string) {
  const picker = page.getByTestId('pack-picker')
  if ((await picker.getAttribute('data-pack')) === id) return
  await expect(async () => {
    if ((await page.getByTestId('pack-menu').count()) === 0) await picker.click()
    await expect(page.getByTestId('pack-menu')).toBeVisible({ timeout: 2000 })
    await page.locator(`[data-testid=pack-option][data-pack="${id}"]`).click({ timeout: 2000 })
  }).toPass({ timeout: 20_000 })
  await expect(picker).toHaveAttribute('data-pack', id, { timeout: 30_000 })
}

/** Open one healthcare document from the Knowledge inspector's document list. */
async function openFromInspector(page: Page, docId: string) {
  const inspector = page.getByTestId('inspector')
  await inspector.locator(`[data-testid=doc-row][data-doc="${docId}"]`).getByRole('button', { name: /Open/ }).click()
  return page.getByTestId('doc-reader')
}

test.beforeEach(async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('mode-badge')).toContainText('dev')
  await expect(node(page, 'gateway')).toBeVisible()
  await pickPack(page, 'healthcare')
  await page.getByRole('switch', { name: 'Advanced view' }).setChecked(true)
  await node(page, 'knowledge').click()
  await page.getByTestId('inspector').getByRole('tab', { name: 'Records' }).click()
  await expect(page.getByTestId('inspector').getByTestId('doc-row')).toHaveCount(14)
})

test('a PDF from the knowledge inspector opens on its file, which the API serves as a PDF', async ({ page, request }) => {
  const reader = await openFromInspector(page, 'appointment_cancellation_policy')
  await expect(reader.getByTestId('doc-path')).toHaveText('appointment_cancellation_policy.pdf')
  await expect(reader.getByTestId('doc-pack')).toHaveText('healthcare')
  await expect(reader.getByTestId('doc-view')).toHaveAttribute('data-view', 'document')
  const pdf = reader.getByTestId('doc-pdf')
  await expect(pdf).toHaveAttribute('type', 'application/pdf')
  const src = (await pdf.getAttribute('data'))!
  expect(src).toBe('/v1/packs/healthcare/files/docs/appointment_cancellation_policy.pdf?raw=1')
  const res = await request.get(src)
  expect(res.status()).toBe(200)
  expect(res.headers()['content-type']).toBe('application/pdf')
  expect(res.headers()['content-disposition']).toMatch(/^inline/)
  expect((await res.body()).subarray(0, 5).toString()).toBe('%PDF-')
  // The chunks are still there: one section per page.
  await reader.getByRole('radio', { name: 'Chunks' }).click()
  await expect(reader.getByTestId('doc-chunk').first()).toContainText('page 1')
})

test('a markdown document opens as written, with a rule per chunk; the API serves it as markdown', async ({ page, request }) => {
  const reader = await openFromInspector(page, 'patient_faq')
  const body = reader.getByTestId('doc-reader-body')
  await expect(body).toHaveAttribute('data-view', 'document')
  await expect(body.locator('h1')).toHaveCount(1)
  const chunks = Number((await reader.getByTestId('doc-chunk-count').textContent())!.replace(/\D/g, ''))
  expect(chunks).toBeGreaterThan(1)
  // Every chunk is named by one rule (a section cut in windows names several).
  const named = await body.getByTestId('doc-boundary').evaluateAll((els) => els.map((el) => el.getAttribute('aria-label') ?? '').join(' '))
  expect(named.match(/#\d+/g)).toHaveLength(chunks)
  const file = (await request.get('/v1/knowledge/docs/patient_faq?pack=healthcare').then((r) => r.json())) as { file: { url: string } }
  const res = await request.get(file.file.url)
  expect(res.status()).toBe(200)
  expect(res.headers()['content-type']).toBe('text/markdown; charset=utf-8')
  expect(await res.text()).toMatch(/^# /m)
})
