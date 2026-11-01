import { expect, test, type Page } from '@playwright/test'

// Phase 5 on the mock build: the lessons drawer, the inspector's Lessons tab, code walk links,
// the Check yourself questionnaire and the disable-and-try flow, over the real chapter files.
const URL = '/?mockStepMs=10'
const CHAPTER = '02-input_guardrails'
const PROMPT = 'My email is ana.diaz@example.com. Where is order A-1001?'

const node = (page: Page, id: string) => page.getByTestId(`node-${id}`)

async function openFromDrawer(page: Page, slug: string) {
  await page.getByTestId('lessons-button').click()
  const drawer = page.getByTestId('lessons-drawer')
  await expect(drawer).toBeVisible()
  await drawer.locator(`[data-testid=lesson-row][data-slug="${slug}"]`).click()
  await expect(drawer).toBeHidden()
}

const row = (page: Page, slug: string) => page.getByTestId('lessons-drawer').locator(`[data-testid=lesson-row][data-slug="${slug}"]`)

test.beforeEach(async ({ page }) => {
  await page.goto(URL)
  await expect(page.getByTestId('mode-badge')).toContainText('dev')
  await expect(node(page, 'gateway')).toBeVisible()
})

test('the drawer lists every chapter in order and opens one on the Lessons tab', async ({ page }) => {
  const trigger = page.getByTestId('lessons-button')
  await trigger.click()
  const drawer = page.getByRole('dialog', { name: 'Lessons' })
  await expect(drawer).toBeVisible()
  const rows = drawer.getByTestId('lesson-row')
  await expect(rows).toHaveCount(21)
  const slugs = await rows.evaluateAll((els) => els.map((e) => e.getAttribute('data-slug')))
  expect(slugs.map((s) => Number(s!.slice(0, 2)))).toEqual(Array.from({ length: 21 }, (_, i) => i + 1))
  await expect(rows.nth(15)).toContainText('inside')
  await expect(row(page, CHAPTER)).toContainText('Input guardrails')
  await expect(row(page, CHAPTER)).toContainText('Size limits, PII redaction')
  await expect(row(page, CHAPTER)).toContainText('Not started')
  await expect(rows.nth(2)).toContainText('Not started')
  // Esc closes the drawer and focus goes back to its trigger.
  await page.keyboard.press('Escape')
  await expect(drawer).toBeHidden()
  await expect(trigger).toBeFocused()

  await openFromDrawer(page, CHAPTER)
  const inspector = page.getByTestId('inspector')
  await expect(inspector.getByTestId('inspector-title')).toHaveText('Input guardrails')
  await expect(inspector.getByRole('tab', { name: 'Lessons' })).toHaveAttribute('aria-selected', 'true')
  await expect(inspector.getByTestId('lesson-title')).toHaveText('Input guardrails')
  await expect(inspector.getByTestId('lesson-title')).toBeFocused()
  await expect(inspector.getByRole('heading', { name: 'What breaks without it' })).toHaveAttribute('id', 'what-breaks-without-it')
  // An https link opens in a new tab and says so.
  const owasp = inspector.getByRole('link', { name: /OWASP Top 10/ })
  await expect(owasp).toHaveAttribute('target', '_blank')
  await expect(owasp).toContainText('(opens in a new tab)')
  await expect(inspector.getByRole('link', { name: /spec\/contracts\/guardrail\.md/ })).toHaveAttribute('href', /github\.com\/.*\/blob\/main\/spec\/contracts\/guardrail\.md$/)

  // Read now; Next chapter in the drawer goes to chapter 03.
  await page.getByTestId('lessons-button').click()
  await expect(row(page, CHAPTER)).toContainText('Read')
  await expect(row(page, CHAPTER)).toHaveAttribute('aria-current', 'true')
  await page.getByTestId('drawer-next').click()
  await expect(inspector.getByTestId('inspector-title')).toHaveText('Context builder')
  await expect(inspector.getByTestId('lesson-title')).toHaveText('Context builder')
  await inspector.getByTestId('lesson-prev').click()
  await expect(inspector.getByTestId('lesson-title')).toHaveText('Input guardrails')
})

test('a group chapter switches to the expert view and selects its first node', async ({ page }) => {
  await page.getByTestId('lessons-button').click()
  const slug = await page.getByTestId('lesson-row').nth(15).getAttribute('data-slug')
  await page.getByTestId('lesson-row').nth(15).click()
  await expect(page.getByRole('switch', { name: 'Expert view' })).toHaveAttribute('aria-checked', 'true')
  const inspector = page.getByTestId('inspector')
  await expect(inspector.getByRole('tab', { name: 'Lessons' })).toHaveAttribute('aria-selected', 'true')
  await expect(inspector).toContainText(/inside Memory/)
  expect(slug).toMatch(/^16-/)
  await expect(inspector.getByTestId('lesson-tab')).toHaveAttribute('data-status', 'ok')
  await expect(inspector.getByTestId('lesson-title')).toContainText('Inside memory')
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
  await page.getByRole('switch', { name: 'Expert view' }).click()
  await node(page, 'semantic').click()
  const inspector = page.getByTestId('inspector')
  await inspector.getByRole('tab', { name: 'Lessons' }).click()
  await expect(inspector.getByTestId('lesson-tab')).toHaveAttribute('data-slug', '16-memory-inside')
  const section = inspector.locator('h3#semantic')
  await expect(section).toBeInViewport()
  await expect(inspector.locator('h3#procedural')).not.toBeInViewport()
})

test('the Lessons tab opens from the inspector, for a written and an unwritten chapter', async ({ page }) => {
  await node(page, 'input_guardrails').click()
  const inspector = page.getByTestId('inspector')
  await inspector.getByTestId('read-chapter').click()
  await expect(inspector.getByRole('tab', { name: 'Lessons' })).toHaveAttribute('aria-selected', 'true')
  await expect(inspector.getByTestId('lesson-tab')).toHaveAttribute('data-slug', CHAPTER)
  await expect(inspector.getByRole('heading', { name: 'Check yourself' })).toBeVisible()
  // The tab stays picked while the reader moves between nodes; a chapter the mock does not
  // have (here chapter 03, removed for the test) says it is not written yet.
  await page.evaluate(() => {
    const mock = (globalThis as unknown as { __harnesswalkMock: { lessons: { chapters: { slug: string }[] } } }).__harnesswalkMock
    const at = mock.lessons.chapters.findIndex((c) => c.slug === '03-context_builder')
    if (at >= 0) mock.lessons.chapters.splice(at, 1)
  })
  await node(page, 'context_builder').click()
  await expect(inspector.getByTestId('inspector-title')).toHaveText('Context builder')
  await expect(inspector.getByTestId('lesson-missing')).toBeVisible()
})

test('a code walk link opens the Code tab at the symbol, and another file with a way back', async ({ page }) => {
  await openFromDrawer(page, CHAPTER)
  const inspector = page.getByTestId('inspector')
  await inspector.locator('[data-testid=lesson-code-link][data-symbol="InputGuardrails.check"]').click()
  await expect(inspector.getByRole('tab', { name: 'Code' })).toHaveAttribute('aria-selected', 'true')
  const target = inspector.locator('[data-slot=code-block-line][data-target]')
  await expect(target).toContainText('def check')
  await expect(target).toBeInViewport()
  await expect(inspector).toContainText('harnesswalk/control/input_guardrails.py')

  await inspector.getByRole('tab', { name: 'Lessons' }).click()
  await inspector.locator('[data-testid=lesson-code-link][data-symbol="PiiDetector.find"]').click()
  await expect(inspector.getByTestId('code-peek-path')).toContainText('harnesswalk/control/pii.py')
  await expect(inspector.getByTestId('code-peek-path')).toContainText('from chapter 02')
  await expect(target).toContainText('def find')
  await expect(target).toBeInViewport()
  await inspector.getByTestId('code-back').click()
  await expect(inspector.getByTestId('code-peek-path')).toHaveCount(0)
  await expect(inspector).toContainText('harnesswalk/control/input_guardrails.py')
})

test('answering the three checks marks the chapter checked, and it is remembered', async ({ page }) => {
  await openFromDrawer(page, CHAPTER)
  const checks = page.getByTestId('check-yourself')
  // Only the active question is shown; the others are hidden and inert.
  const feedback = checks.locator('[data-testid=check-question][data-active] [data-testid=check-feedback]')
  await expect(checks.getByRole('progressbar')).toHaveText('Question 1 of 3')
  await expect(checks.getByTestId('check-count')).toHaveText('0 of 3 answered')
  await checks.getByRole('radio', { name: /advice the input can argue with/ }).click()
  await expect(feedback).toHaveAttribute('data-correct', 'true')
  await expect(feedback).toContainText('never sees the argument')
  await checks.getByRole('button', { name: 'Next question' }).click()
  await expect(checks.getByRole('progressbar')).toHaveText('Question 2 of 3')
  // Keyboard: the letters pick a choice.
  await checks.getByRole('radio', { name: /memory writer redacts the email anyway/ }).focus()
  await page.keyboard.press('a')
  await expect(feedback).toHaveAttribute('data-correct', 'false')
  await expect(feedback).toContainText('Not quite. The answer is C.')
  await checks.getByRole('button', { name: 'Next question' }).click()
  await checks.getByRole('radio', { name: /refused at the guardrail/ }).click()
  await expect(checks).toHaveAttribute('data-complete', 'true')
  await expect(checks.getByTestId('check-count')).toHaveText('All 3 answered, 2 right')

  await page.getByTestId('lessons-button').click()
  await expect(row(page, CHAPTER).getByTestId('lesson-progress')).toHaveAttribute('data-checked', 'true')
  await expect(row(page, CHAPTER)).toContainText('Read, checked')

  await page.reload()
  await expect(node(page, 'gateway')).toBeVisible()
  await page.getByTestId('lessons-button').click()
  await expect(row(page, CHAPTER)).toContainText('Read, checked')
  await row(page, CHAPTER).click()
  await expect(page.getByTestId('check-yourself')).toHaveAttribute('data-complete', 'true')
})

test('disable and try: the node is off, the fixture and prompt are set, and the turn skips it', async ({ page }) => {
  await openFromDrawer(page, CHAPTER)
  const inspector = page.getByTestId('inspector')
  const button = inspector.getByTestId('lesson-try-button')
  await expect(button).toHaveText('Disable Input guardrails and try it')
  await button.click()
  await expect(inspector.getByRole('switch', { name: 'Disable Input guardrails on the next turn' })).toHaveAttribute('aria-checked', 'true')
  await expect(page.getByRole('banner')).toContainText('1 off next turn')
  await expect(page.getByTestId('fixture-select')).toContainText('tool_call')
  const composer = page.getByTestId('composer-input')
  await expect(composer).toHaveValue(PROMPT)
  await expect(composer).toBeFocused()
  await expect(inspector.getByTestId('lesson-try')).toContainText('Ready: press send in the chat.')
  await page.getByTestId('send-button').click()
  await expect(page.getByTestId('turn-status')).toHaveAttribute('data-phase', 'done')
  await expect(node(page, 'input_guardrails')).toHaveAttribute('data-state', 'skipped')
  await expect(node(page, 'tools')).toHaveAttribute('data-state', 'done')
})
