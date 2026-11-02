import { expect, test, type Page } from '@playwright/test'

// The knowledge document reader on the mock build: a fixed-height dialog whose body scrolls
// inside it, the Document view (markdown rendered with its chunk boundaries, a PDF in the
// browser's viewer or its fallback) and the Chunks view, opened from a citation chip and from
// the Knowledge inspector's file tree.
const URL = '/?mockStepMs=10'
const node = (page: Page, id: string) => page.getByTestId(`node-${id}`)

async function pickPack(page: Page, id: string) {
  await page.getByTestId('pack-picker').click()
  await page.locator(`[data-testid=pack-option][data-pack="${id}"]`).click()
  await expect(page.getByTestId('pack-picker')).toHaveAttribute('data-pack', id)
}

/** The finance pack's cited answer: one chip naming chunk 4 (Fees and carry) of the HCP-01 fact sheet. */
async function citedAnswer(page: Page) {
  await pickPack(page, 'finance')
  await page.getByTestId('fixture-select').click()
  await page.getByRole('option', { name: 'cite_mgmt_fee_hcp01', exact: true }).click()
  await page.getByTestId('try-prompt').first().click()
  await expect(page.getByTestId('turn-status')).toHaveAttribute('data-phase', 'done')
  const chip = page.getByTestId('assistant-message').last().getByTestId('citation-chip')
  await expect(chip).toHaveAttribute('data-doc', 'fund_sheet_hcp01')
  await expect(chip).toHaveAttribute('data-chunk', '4')
  return chip
}

test.beforeEach(async ({ page }) => {
  await page.goto(URL)
  await expect(page.getByTestId('mode-badge')).toContainText('dev')
  await expect(node(page, 'gateway')).toBeVisible()
})

test('a citation opens the markdown document as written, then its chunks, scrolling inside the dialog', async ({ page }) => {
  await (await citedAnswer(page)).click()
  const reader = page.getByTestId('doc-reader')
  const body = reader.getByTestId('doc-reader-body')
  await expect(reader.getByRole('heading', { level: 2, name: 'Hollowmere Growth Partners III: fund fact sheet' })).toBeVisible()
  await expect(reader.getByTestId('doc-chunk-count')).toHaveText('· 8 chunks')
  await expect(reader.getByTestId('doc-path')).toHaveText('fund_sheet_hcp01.md')
  await expect(reader.getByTestId('doc-pack')).toHaveText('finance')

  // Document: the markdown rendered, a rule naming the chunk at each boundary, the cited section in view.
  await expect(reader.getByTestId('doc-view')).toHaveAttribute('data-view', 'document')
  await expect(body).toHaveAttribute('data-view', 'document')
  await expect(body.locator('h1')).toHaveText('Hollowmere Growth Partners III: fund fact sheet')
  await expect(body.locator('h2')).toHaveCount(7)
  await expect(body.getByTestId('doc-boundary')).toHaveCount(8)
  const section = body.locator('[data-cited="true"]')
  await expect(section).toHaveAttribute('data-chunks', '4')
  await expect(section.locator('h2')).toHaveText('Fees and carry')
  await expect(section).toBeInViewport()
  // The rules can be switched off.
  await reader.getByTestId('doc-rules').click()
  await expect(body.getByTestId('doc-boundary')).toHaveCount(0)

  // Chunks: today's view, the cited chunk marked.
  await reader.getByRole('radio', { name: 'Chunks' }).click()
  await expect(body).toHaveAttribute('data-view', 'chunks')
  await expect(body.getByTestId('doc-chunk')).toHaveCount(8)
  await expect(body.locator('[data-cited="true"]')).toHaveAttribute('data-position', '4')
  await expect(body.locator('[data-cited="true"]')).toBeInViewport()

  // The body scrolls inside the dialog; the dialog and its close button stay on screen.
  const size = await body.evaluate((el) => ({ scroll: el.scrollHeight, client: el.clientHeight }))
  expect(size.scroll).toBeGreaterThan(size.client)
  const box = (await reader.boundingBox())!
  expect(box.y).toBeGreaterThanOrEqual(0)
  expect(box.y + box.height).toBeLessThanOrEqual(page.viewportSize()!.height)
  await body.evaluate((el) => el.scrollTo(0, el.scrollHeight))
  await expect(body.getByTestId('doc-chunk').last()).toBeInViewport()
  await expect(reader.getByRole('button', { name: 'Close' })).toBeInViewport()
  await expect(reader.getByTestId('doc-reader-header')).toBeInViewport()
  await reader.getByRole('button', { name: 'Close' }).click()
  await expect(reader).toBeHidden()
})

test('a PDF opens in the browser viewer on its file, with the fallback where there is none', async ({ page, request }) => {
  await pickPack(page, 'finance')
  await page.getByRole('switch', { name: 'Advanced view' }).setChecked(true)
  await node(page, 'knowledge').click()
  const inspector = page.getByTestId('inspector')
  await inspector.getByRole('tab', { name: 'Files' }).click()
  await inspector.locator('[data-testid=file-tree-file][data-path="docs/glossary_of_fund_terms.pdf"]').click()
  await inspector.getByTestId('pack-file-reader').click()
  const reader = page.getByTestId('doc-reader')
  await expect(reader.getByTestId('doc-view')).toHaveAttribute('data-view', 'document')
  const pdf = reader.getByTestId('doc-pdf')
  await expect(pdf).toHaveAttribute('type', 'application/pdf')
  const src = (await pdf.getAttribute('data'))!
  const res = await request.get(src)
  expect(res.status()).toBe(200)
  expect(res.headers()['content-type']).toContain('application/pdf')
  // Headless Chromium has no inline PDF viewer: the object shows its fallback, never a download.
  const fallback = reader.getByTestId('doc-pdf-fallback')
  if (await page.evaluate(() => (navigator as unknown as { pdfViewerEnabled?: boolean }).pdfViewerEnabled === false)) {
    await expect(fallback).toContainText('Your browser cannot display PDFs inline.')
    await expect(fallback.getByRole('link', { name: 'Open in new tab' })).toHaveAttribute('href', src)
    await expect(fallback.getByRole('link', { name: 'Download' })).toHaveAttribute('download', 'glossary_of_fund_terms.pdf')
  }
  // The PDF fills the body, which does not scroll itself.
  const body = reader.getByTestId('doc-reader-body')
  const sizes = await body.evaluate((el) => ({ body: el.clientHeight, pdf: el.querySelector('object')!.clientHeight }))
  expect(sizes.pdf).toBeGreaterThan(sizes.body - 4)
  await reader.getByRole('radio', { name: 'Chunks' }).click()
  await expect(body.getByTestId('doc-chunk').first()).toContainText('Glossary of fund terms')
})

test('on a phone the reader fits the screen and its body scrolls', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(URL)
  await expect(page.getByTestId('mode-badge')).toContainText('dev')
  await (await citedAnswer(page)).click()
  const reader = page.getByTestId('doc-reader')
  await expect(reader.getByTestId('doc-reader-body')).toHaveAttribute('data-view', 'document')
  const box = (await reader.boundingBox())!
  expect(box.x).toBeGreaterThanOrEqual(0)
  expect(box.x + box.width).toBeLessThanOrEqual(390)
  expect(box.y + box.height).toBeLessThanOrEqual(844)
  const body = reader.getByTestId('doc-reader-body')
  expect(await body.evaluate((el) => el.scrollHeight > el.clientHeight && el.scrollWidth <= el.clientWidth)).toBe(true)
  await expect(reader.getByRole('button', { name: 'Close' })).toBeInViewport()
})
