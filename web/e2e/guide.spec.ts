import { expect, test, type Page } from '@playwright/test'

// The Guide on the mock build (once the lessons drawer and the inspector's Lessons tab): the
// chapter list, a chapter beside the inspector, Open in Guide, code walk links, the Check
// yourself questionnaire and the disable-and-try flow, over the real chapter files.
const URL = '/?mockStepMs=10'
const CHAPTER = '02-input_guardrails'
const PROMPT = 'My email is ana.diaz@example.com. Where is order A-1001?'

const node = (page: Page, id: string) => page.getByTestId(`node-${id}`)

const guide = (page: Page) => page.getByTestId('guide-drawer')

async function openFromGuide(page: Page, slug: string) {
  await page.getByTestId('guide-button').click()
  await expect(guide(page)).toBeVisible()
  await guide(page).locator(`[data-testid=lesson-row][data-slug="${slug}"]`).click()
  await expect(guide(page).getByTestId('guide-chapter')).toHaveAttribute('data-slug', slug)
}

/** Back to the chapter list (opening the Guide first when it is closed). */
async function contents(page: Page) {
  if (!(await guide(page).isVisible())) await page.getByTestId('guide-button').click()
  const all = guide(page).getByTestId('guide-contents-button')
  if (await all.isVisible()) await all.click()
  await expect(guide(page).getByTestId('guide-contents')).toBeVisible()
}

const row = (page: Page, slug: string) => guide(page).locator(`[data-testid=lesson-row][data-slug="${slug}"]`)

test.beforeEach(async ({ page }) => {
  await page.goto(URL)
  await expect(page.getByTestId('mode-badge')).toContainText('dev')
  await expect(node(page, 'gateway')).toBeVisible()
})

test('the Guide lists every chapter in order and opens one beside the inspector', async ({ page }) => {
  const trigger = page.getByTestId('guide-button')
  await expect(trigger).toHaveText('Guide')
  await trigger.click()
  const drawer = page.getByRole('complementary', { name: 'Guide' })
  await expect(drawer).toBeVisible()
  await expect(trigger).toHaveAttribute('aria-expanded', 'true')
  const rows = drawer.getByTestId('lesson-row')
  await expect(rows).toHaveCount(21)
  const slugs = await rows.evaluateAll((els) => els.map((e) => e.getAttribute('data-slug')))
  expect(slugs.map((s) => Number(s!.slice(0, 2)))).toEqual(Array.from({ length: 21 }, (_, i) => i + 1))
  await expect(rows.nth(15)).toContainText('inside')
  await expect(row(page, CHAPTER)).toContainText('Input guardrails')
  await expect(row(page, CHAPTER)).toContainText('Size limits, PII redaction')
  await expect(row(page, CHAPTER)).toContainText('Not started')
  await expect(rows.nth(2)).toContainText('Not started')
  // Esc closes the Guide and focus goes back to its button.
  await drawer.getByTestId('guide-close').focus()
  await page.keyboard.press('Escape')
  await expect(drawer).toBeHidden()
  await expect(trigger).toBeFocused()

  await openFromGuide(page, CHAPTER)
  const inspector = page.getByTestId('inspector')
  const chapter = guide(page).getByTestId('guide-chapter')
  // The chapter's node is open in the inspector too, and neither covers the other.
  await expect(inspector.getByTestId('inspector-title')).toHaveText('Input guardrails')
  await expect(inspector.getByRole('tab', { name: 'Lessons' })).toHaveCount(0)
  const [a, b] = [(await inspector.boundingBox())!, (await guide(page).boundingBox())!]
  const apart = a.x + a.width <= b.x + 1 || b.x + b.width <= a.x + 1 || a.y + a.height <= b.y + 1 || b.y + b.height <= a.y + 1
  expect(apart).toBe(true)
  await expect(chapter.getByTestId('lesson-title')).toHaveText('Input guardrails')
  await expect(chapter.getByTestId('lesson-title')).toBeFocused()
  await expect(chapter.getByRole('heading', { name: 'What breaks without it' })).toHaveAttribute('id', 'what-breaks-without-it')
  // An https link opens in a new tab and says so.
  const owasp = chapter.getByRole('link', { name: /OWASP Top 10/ })
  await expect(owasp).toHaveAttribute('target', '_blank')
  await expect(owasp).toContainText('(opens in a new tab)')
  await expect(chapter.getByRole('link', { name: /spec\/contracts\/guardrail\.md/ })).toHaveAttribute('href', /github\.com\/.*\/blob\/main\/spec\/contracts\/guardrail\.md$/)

  // Read now; Next chapter in the list goes to chapter 03.
  await contents(page)
  await expect(row(page, CHAPTER)).toContainText('Read')
  await expect(row(page, CHAPTER)).toHaveAttribute('aria-current', 'true')
  await page.getByTestId('drawer-next').click()
  await expect(inspector.getByTestId('inspector-title')).toHaveText('Context builder')
  await expect(chapter.getByTestId('lesson-title')).toHaveText('Context builder')
  await chapter.getByTestId('lesson-prev').click()
  await expect(chapter.getByTestId('lesson-title')).toHaveText('Input guardrails')
  await expect(inspector.getByTestId('inspector-title')).toHaveText('Input guardrails')
})

test('a node on the canvas, then Open in Guide, shows its chapter; the Guide then follows the canvas', async ({ page }) => {
  await node(page, 'policy').click()
  const inspector = page.getByTestId('inspector')
  await expect(inspector.getByRole('tab').first()).toHaveText('Overview')
  expect(await inspector.getByRole('tab').allTextContents()).not.toContain('Lessons')
  await expect(guide(page)).toHaveCount(0)
  await inspector.getByTestId('open-in-guide').click()
  const chapter = guide(page).getByTestId('guide-chapter')
  await expect(chapter).toHaveAttribute('data-slug', /^08-/)
  await expect(chapter.getByTestId('lesson-title')).toBeFocused()
  await expect(chapter.getByRole('heading', { name: 'Check yourself' })).toHaveCount(0)
  // The Guide and the inspector are both open: picking another node moves both.
  await node(page, 'gateway').click()
  await expect(inspector.getByTestId('inspector-title')).toHaveText('Gateway')
  await expect(chapter).toHaveAttribute('data-slug', /^01-/)
  // Closing the inspector keeps the chapter; All chapters goes back to the list.
  await inspector.getByRole('button', { name: 'Close inspector' }).click()
  await expect(inspector).toHaveCount(0)
  await expect(chapter).toHaveAttribute('data-slug', /^01-/)
  await guide(page).getByTestId('guide-contents-button').click()
  await expect(guide(page).getByTestId('guide-contents')).toBeVisible()
})

test('on a narrow screen the Guide opens as a sheet with its own close', async ({ page }) => {
  await page.setViewportSize({ width: 800, height: 800 })
  await page.getByTestId('guide-button').click()
  const sheet = page.getByRole('dialog', { name: 'Guide' })
  await expect(sheet).toBeVisible()
  await sheet.locator(`[data-testid=lesson-row][data-slug="${CHAPTER}"]`).click()
  await expect(sheet.getByTestId('guide-chapter')).toHaveAttribute('data-slug', CHAPTER)
  await sheet.getByRole('button', { name: 'Close' }).click()
  await expect(sheet).toBeHidden()
  await expect(page.getByTestId('guide-button')).toBeFocused()
})
test('a group chapter switches to the advanced view and selects its first node', async ({ page }) => {
  await page.getByTestId('guide-button').click()
  const slug = await page.getByTestId('lesson-row').nth(15).getAttribute('data-slug')
  await page.getByTestId('lesson-row').nth(15).click()
  await expect(page.getByRole('switch', { name: 'Advanced view' })).toHaveAttribute('aria-checked', 'true')
  const inspector = page.getByTestId('inspector')
  await expect(inspector).toContainText(/inside Memory/)
  expect(slug).toMatch(/^16-/)
  const chapter = guide(page).getByTestId('guide-chapter')
  await expect(chapter).toHaveAttribute('data-status', 'ok')
  await expect(chapter.getByTestId('lesson-title')).toContainText('Inside memory')
})

test('an expert node opens its group chapter at its own section', async ({ page }) => {
  // Swap chapter 16 for a stand-in with one long section per store, so scrolling is certain.
  await page.evaluate(async () => {
    type Mock = { handle: (p: string) => Promise<Response>; diagram: { nodes: { id: string; label: string; parent?: string }[] }; lessons: { chapters: unknown[] } }
    const mock = (globalThis as unknown as { __harnesswalkMock: Mock }).__harnesswalkMock
    const at = (mock.lessons.chapters as { slug: string }[]).findIndex((c) => c.slug === '16-memory-inside')
    if (at >= 0) mock.lessons.chapters.splice(at, 1)
    const kids = mock.diagram.nodes.filter((n) => n.parent === 'memory')
    const filler = Array.from({ length: 12 }, (_, i) => `Paragraph ${i + 1} of this section, long enough to need scrolling.`).join('\n\n')
    const markdown = `# Inside memory\n\n> The five stores.\n\n## The stores\n\n${kids.map((k) => `### ${k.label}\n\n${filler}`).join('\n\n')}\n`
    mock.lessons.chapters.push({ slug: '16-memory-inside', n: 16, title: 'Inside memory', hook: 'The five stores.', node_id: 'memory', covers: kids.map((k) => k.id), group: true, words: 900, url: '/v1/lessons/16-memory-inside', markdown, try: null, code_links: [] })
  })
  await page.getByRole('switch', { name: 'Advanced view' }).click()
  await node(page, 'semantic').click()
  await page.getByTestId('inspector').getByTestId('open-in-guide').click()
  const chapter = guide(page).getByTestId('guide-chapter')
  await expect(chapter).toHaveAttribute('data-slug', '16-memory-inside')
  const section = chapter.locator('h3#semantic')
  await expect(section).toBeInViewport()
  await expect(chapter.locator('h3#procedural')).not.toBeInViewport()
})

test('Open in Guide from the inspector, for a written and an unwritten chapter', async ({ page }) => {
  await node(page, 'input_guardrails').click()
  const inspector = page.getByTestId('inspector')
  await inspector.getByTestId('open-in-guide').click()
  const chapter = guide(page).getByTestId('guide-chapter')
  await expect(chapter).toHaveAttribute('data-slug', CHAPTER)
  await expect(chapter.getByRole('heading', { name: 'Check yourself' })).toHaveCount(0)
  // The Guide follows the reader between nodes; a chapter the mock does not have (here
  // chapter 03, removed for the test) says it is not written yet.
  await page.evaluate(() => {
    const mock = (globalThis as unknown as { __harnesswalkMock: { lessons: { chapters: { slug: string }[] } } }).__harnesswalkMock
    const at = mock.lessons.chapters.findIndex((c) => c.slug === '03-context_builder')
    if (at >= 0) mock.lessons.chapters.splice(at, 1)
  })
  await node(page, 'context_builder').click()
  await expect(inspector.getByTestId('inspector-title')).toHaveText('Context builder')
  await expect(guide(page).getByTestId('lesson-missing')).toBeVisible()
})

test('a code walk link opens the Code tab at the symbol, and another file with a way back', async ({ page }) => {
  await openFromGuide(page, CHAPTER)
  const inspector = page.getByTestId('inspector')
  const chapter = guide(page).getByTestId('guide-chapter')
  await chapter.locator('[data-testid=lesson-code-link][data-symbol="InputGuardrails.check"]').click()
  await expect(inspector.getByRole('tab', { name: 'Code' })).toHaveAttribute('aria-selected', 'true')
  const target = inspector.locator('[data-slot=code-block-line][data-target]')
  await expect(target).toContainText('def check')
  await expect(target).toBeInViewport()
  await expect(inspector).toContainText('harnesswalk/control/input_guardrails.py')

  await chapter.locator('[data-testid=lesson-code-link][data-symbol="PiiDetector.find"]').click()
  await expect(inspector.getByTestId('code-peek-path')).toContainText('harnesswalk/control/pii.py')
  await expect(inspector.getByTestId('code-peek-path')).toContainText('from chapter 02')
  await expect(target).toContainText('def find')
  await expect(target).toBeInViewport()
  await inspector.getByTestId('code-back').click()
  await expect(inspector.getByTestId('code-peek-path')).toHaveCount(0)
  await expect(inspector).toContainText('harnesswalk/control/input_guardrails.py')
})

test('disable and try: the node is off, the fixture and prompt are set, and the turn skips it', async ({ page }) => {
  await openFromGuide(page, CHAPTER)
  const inspector = page.getByTestId('inspector')
  const chapter = guide(page).getByTestId('guide-chapter')
  const button = chapter.getByTestId('lesson-try-button')
  await expect(button).toHaveText('Disable Input guardrails and try it')
  await button.click()
  await expect(inspector.getByRole('switch', { name: 'Disable Input guardrails on the next turn' })).toHaveAttribute('aria-checked', 'true')
  await expect(page.getByRole('banner')).toContainText('1 off next turn')
  await expect(page.getByTestId('fixture-select')).toContainText('tool_call')
  const composer = page.getByTestId('composer-input')
  await expect(composer).toHaveValue(PROMPT)
  await expect(composer).toBeFocused()
  await expect(chapter.getByTestId('lesson-try')).toContainText('Ready: press send in the chat.')
  await page.getByTestId('send-button').click()
  await expect(page.getByTestId('turn-status')).toHaveAttribute('data-phase', 'done')
  await expect(node(page, 'input_guardrails')).toHaveAttribute('data-state', 'skipped')
  await expect(node(page, 'tools')).toHaveAttribute('data-state', 'done')
})
