import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import { lastTurnEvents, newSession, node, pickFixture, send, settleTurns, status } from './helpers'

// Phase 5 against the real harness (fake provider): the pack picker and what it switches, every
// pack's golden suite run from the Eval inspector in one server, the release gate refusing a
// regressed domain pack (with the failing case's diagnosis) and Release anyway, and every
// lesson's "what breaks" demonstration run from its chapter in the Guide, on its pack.

const FEE = 'What is the management fee for Hollowmere Growth Partners III?'

interface PackItem {
  id: string
  title: string
  fixtures: string[]
}

async function packs(request: APIRequestContext): Promise<PackItem[]> {
  return ((await (await request.get('/v1/packs')).json()) as { packs: PackItem[] }).packs
}

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

async function setView(page: Page, view: 'Simple' | 'Expert') {
  const expert = page.getByRole('switch', { name: 'Advanced view' })
  const want = view === 'Expert' ? 'true' : 'false'
  if ((await expert.getAttribute('aria-checked')) === want) return
  await expert.click()
  await expect(expert).toHaveAttribute('aria-checked', want)
}

/** Run the session pack's golden suite from the Eval inspector and return the finished run's detail. */
async function runGolden(page: Page) {
  await setView(page, 'Simple')
  await node(page, 'eval').click()
  const inspector = page.getByTestId('inspector')
  await expect(inspector.getByRole('tab', { name: 'Runs' })).toHaveAttribute('aria-selected', 'true')
  const detail = inspector.getByTestId('eval-run-detail')
  const before = (await detail.count()) ? await detail.getAttribute('data-run') : null
  await inspector.getByTestId('eval-run-button').click()
  await expect(detail).not.toHaveAttribute('data-run', before ?? '__none__')
  await expect(detail).toHaveAttribute('data-status', 'done', { timeout: 90_000 })
  return detail
}

/** Wait for the last turn to end, approving its approval card once if it pauses on one. */
async function finish(page: Page) {
  const phase = () => status(page).getAttribute('data-phase')
  const approve = page.getByTestId('approval-card').last().getByRole('button', { name: /^Approve / })
  await expect
    .poll(async () => {
      const p = await phase()
      if (p === 'done' || p === 'error') return 'over'
      return (await approve.count()) ? 'approval' : 'running'
    })
    .not.toBe('running')
  if ((await phase()) !== 'done' && (await phase()) !== 'error') await approve.click()
  await expect.poll(phase, { timeout: 30_000 }).toMatch(/^(done|error)$/)
  return phase()
}

/**
 * Wait until no eval run of the tenant is running: a run reads done a moment before the harness
 * releases its process-wide edit lock, so an edit right after can still get 409 turn_running.
 */
async function quiet(request: APIRequestContext) {
  await expect
    .poll(async () => ((await (await request.get('/v1/evals/runs?limit=5')).json()) as { runs: { status: string }[] }).runs.some((r) => r.status === 'running'))
    .toBe(false)
  await new Promise((r) => setTimeout(r, 1000))
}

test.beforeEach(async ({ page, request }) => {
  await settleTurns(request)
  await page.goto('/')
  await expect(page.getByTestId('mode-badge')).toContainText('dev')
  await expect(node(page, 'gateway')).toBeVisible()
})

test.afterEach(async ({ page }) => {
  await expect(page.getByText(/invalid events/)).toHaveCount(0)
})

test('the pack picker: finance changes the prompts, the fixtures, the turn’s pack and the File Tree, and survives a reload', async ({ page, request }) => {
  const all = await packs(request)
  const finance = all.find((p) => p.id === 'finance')!
  const starter = all.find((p) => p.id === '_starter')!
  await page.getByTestId('pack-picker').click()
  await expect(page.getByTestId('pack-option')).toHaveCount(all.length)
  await expect(page.locator('[data-testid=pack-option][data-pack="finance"]')).toContainText('Citations and the memory writer')
  await expect(page.locator('[data-testid=pack-option][data-pack="_starter"]')).toHaveAttribute('aria-checked', 'true')
  await page.locator('[data-testid=pack-option][data-pack="finance"]').click()
  await expect(page.getByTestId('pack-picker')).toHaveAttribute('data-pack', 'finance', { timeout: 30_000 })
  await expect(page.getByTestId('try-prompt').first()).toHaveText(FEE)

  // The fixture menu: finance's own, then the shared ones; the starter pack's are gone.
  await page.getByTestId('fixture-select').click()
  const listbox = page.getByRole('listbox')
  await expect(listbox.getByTestId('fixture-group-pack').getByRole('option')).toHaveCount(finance.fixtures.length)
  await expect(listbox.getByRole('option', { name: 'cite_mgmt_fee_hcp01', exact: true })).toBeVisible()
  await expect(listbox.getByRole('option', { name: 'approval_approve', exact: true })).toBeVisible()
  for (const own of starter.fixtures.filter((f) => !finance.fixtures.includes(f)).slice(0, 3)) await expect(listbox.getByRole('option', { name: own, exact: true })).toHaveCount(0)
  await page.keyboard.press('Escape')

  await pickFixture(page, 'cite_mgmt_fee_hcp01')
  await send(page, FEE)
  await expect(status(page)).toHaveAttribute('data-phase', 'done')
  const events = await lastTurnEvents(page, request)
  expect(events[0]!.summary?.pack).toBe('finance')
  const turnId = await page.getByTestId('chat-turn').last().getAttribute('data-turn-id')
  expect(((await (await request.get(`/v1/turns/${turnId}`)).json()) as { pack: string }).pack).toBe('finance')
  await expect(page.getByTestId('citation-chip').first()).toBeVisible()

  // The inspectors show finance's files in the File Tree.
  await setView(page, 'Expert')
  await node(page, 'procedural').click()
  const inspector = page.getByTestId('inspector')
  await inspector.getByRole('tab', { name: 'Files' }).click()
  await expect(inspector.getByTestId('pack-files')).toHaveAttribute('data-pack', 'finance')
  await inspector.locator('[data-testid=file-tree-file][data-path="skills/fund_documents.md"]').click()
  await expect(inspector.getByTestId('pack-file').getByTestId('artifact-editor')).toHaveAttribute('data-name', 'fund_documents')
  await node(page, 'knowledge').click()
  await inspector.getByRole('tab', { name: 'Files' }).click()
  await inspector.locator('[data-testid=file-tree-file][data-path="docs/glossary_of_fund_terms.pdf"]').click()
  await expect(inspector.getByTestId('pack-file-pdf-note')).toContainText('sources/glossary_of_fund_terms.md')
  await expect(inspector.getByTestId('pack-file-text')).toContainText(/carr/i)

  // A reload comes back on the same session and pack.
  await page.reload()
  await expect(page.getByTestId('pack-picker')).toHaveAttribute('data-pack', 'finance')
  await expect(page.getByTestId('chat-turn')).toHaveCount(1)
  await pickPack(page, '_starter')
})

test('one click on a pack card in the empty chat starts a session on that pack, and the next turn runs on it', async ({ page, request }) => {
  const all = await packs(request)
  if ((await page.getByTestId('chat-turn').count()) > 0) await newSession(page)
  const grid = page.getByTestId('empty-pack-grid')
  const cards = grid.getByTestId('empty-pack-card')
  await expect(cards).toHaveCount(all.length)
  await expect(cards.first()).toHaveAttribute('data-pack', '_starter')
  await expect(grid.locator('[data-selected=true]')).toHaveAttribute('data-pack', '_starter')
  const card = grid.locator('[data-pack="finance"]')
  await expect(card).toContainText('Hollowmere Capital Partners')
  await expect(card).toContainText('Citations and the memory writer')
  await card.click()

  // The same switch as the header picker's: the picker, the prompts, the selected card.
  await expect(page.getByTestId('pack-picker')).toHaveAttribute('data-pack', 'finance', { timeout: 30_000 })
  await expect(card).toHaveAttribute('data-selected', 'true')
  await expect(grid.locator('[data-selected=true]')).toHaveCount(1)
  await expect(page.getByTestId('try-prompt').first()).toHaveText(FEE)

  // The next turn runs on finance, as the harness records it, and the chat shows it instead of the cards.
  await pickFixture(page, 'cite_mgmt_fee_hcp01')
  await send(page, FEE)
  await expect(status(page)).toHaveAttribute('data-phase', 'done')
  const events = await lastTurnEvents(page, request)
  expect(events[0]!.summary?.pack).toBe('finance')
  const turnId = await page.getByTestId('chat-turn').last().getAttribute('data-turn-id')
  expect(((await (await request.get(`/v1/turns/${turnId}`)).json()) as { pack: string }).pack).toBe('finance')
  await expect(page.getByTestId('citation-chip').first()).toBeVisible()
  await expect(grid).toHaveCount(0)
  await pickPack(page, '_starter')
})

test('every pack’s golden suite passes from the Eval inspector, all in one server', async ({ page, request }) => {
  test.setTimeout(600_000)
  for (const pack of await packs(request)) {
    await pickPack(page, pack.id)
    await runGolden(page)
    const inspector = page.getByTestId('inspector')
    await expect(inspector.getByTestId('eval-pack')).toHaveAttribute('data-pack', pack.id)
    const [run] = ((await (await request.get(`/v1/evals/runs?pack=${pack.id}&limit=1`)).json()) as { runs: { pack: string; totals: { cases: number; failed: number; skipped: number } }[] }).runs
    expect(run!.pack).toBe(pack.id)
    expect(run!.totals.failed, `${pack.id}: ${JSON.stringify(run!.totals)}`).toBe(0)
    await expect(inspector.getByTestId('eval-headline')).toHaveText(run!.totals.skipped ? /none failed/ : /all passed/)
  }
  await pickPack(page, '_starter')
})

test('the release gate refuses a regressed domain pack, shows the case and its diagnosis, and Release anyway is audited', async ({ page, request }) => {
  test.setTimeout(240_000)
  await pickPack(page, 'ecommerce')
  const started = new Date(Date.now() - 1000).toISOString()
  const base = ((await (await request.get('/v1/releases?pack=ecommerce')).json()) as { active_release_id: string }).active_release_id
  // A passing run first, so the gate's run is a regression and is diagnosed on its own.
  await runGolden(page)
  await quiet(request)

  // Edit the order status skill from the File Tree: the scripted model no longer finds its words.
  await setView(page, 'Expert')
  await node(page, 'procedural').click()
  const inspector = page.getByTestId('inspector')
  await inspector.getByRole('tab', { name: 'Files' }).click()
  await inspector.locator('[data-testid=file-tree-file][data-path="skills/order_status.md"]').click()
  const editor = inspector.getByTestId('pack-file').getByTestId('artifact-editor')
  await editor.getByTestId('artifact-edit').click()
  const textarea = editor.getByTestId('artifact-textarea')
  await textarea.fill((await textarea.inputValue()).replace('Report the status', 'Describe the state'))
  await editor.getByTestId('artifact-note').fill('order status: reworded')
  await editor.getByTestId('artifact-save').click()
  await expect(editor.getByTestId('artifact-saved')).toBeVisible()

  await setView(page, 'Simple')
  await node(page, 'release').click()
  await inspector.getByTestId('release-note').fill('reworded order status')
  await inspector.getByTestId('release-create').click()
  const refused = inspector.getByTestId('gate-failed')
  await expect(refused).toBeVisible({ timeout: 90_000 })
  const kase = refused.locator('[data-testid="gate-case"][data-case="skill_order_status_qf20430"]')
  await expect(kase).toBeVisible()
  await expect(kase.getByTestId('diagnosis-chip')).toHaveAttribute('data-layer', 'prompt_or_skill')
  await expect(kase.getByTestId('diagnosis-suggestion')).toContainText('order_status')
  expect(((await (await request.get('/v1/releases?pack=ecommerce')).json()) as { active_release_id: string }).active_release_id).toBe(base)

  // The failing case replays from its events_url.
  await kase.getByTestId('gate-case-replay').click()
  await expect(page.getByTestId('replay-label')).toHaveText('Replaying Eval case skill_order_status_qf20430 on the diagram')
  await page.getByRole('button', { name: 'Leave replay and return to live' }).click()

  await refused.getByTestId('release-force').click()
  await expect(inspector.getByTestId('release-forced-note')).toContainText('The audit log records it as release_forced')
  const after = (await (await request.get('/v1/releases?pack=ecommerce')).json()) as { active_release_id: string; releases: { release_id: string; gate: { forced: boolean } }[] }
  expect(after.active_release_id).not.toBe(base)
  expect(after.releases[0]!.gate.forced).toBe(true)
  await expect(inspector.locator(`[data-testid="release-row"][data-release="${after.active_release_id}"]`).getByTestId('release-gate')).toHaveAttribute('data-status', 'forced')
  const audit = (await (await request.get(`/v1/audit?since=${encodeURIComponent(started)}`)).json()) as { records: { entry: { action: string; extra?: { pack?: string; release_id?: string } } }[] }
  expect(audit.records.find((r) => r.entry.action === 'release_forced' && r.entry.extra?.release_id === after.active_release_id)?.entry.extra?.pack).toBe('ecommerce')

  // Leave ecommerce as it was for the tests after this one.
  expect((await request.post(`/v1/releases/${base}/rollback`, { data: {} })).ok()).toBe(true)
  await pickPack(page, '_starter')
})

interface Demo {
  pack: string
  fixture?: string
  prompt: string
  disable?: string[]
  note?: string
}

const PII = /[\w.+-]+@[\w-]+(?:\.[\w-]+)+|BH-\d{4}-\d{4}-[A-Z]/

test('every lesson’s demonstration runs from its chapter in the Guide: the turn ends and each disabled node is skipped', async ({ page, request }) => {
  test.setTimeout(900_000)
  const { lessons } = (await (await request.get('/v1/lessons')).json()) as { lessons: { slug: string }[] }
  let ran = 0
  for (const { slug } of lessons) {
    const lesson = (await (await request.get(`/v1/lessons/${slug}`)).json()) as { try: Demo | null; also: Demo[] }
    const demos: [Demo, number][] = [...(lesson.try ? [[lesson.try, -1] as [Demo, number]] : []), ...lesson.also.map((d, i) => [d, i] as [Demo, number])]
    for (const [demo, index] of demos) {
      await test.step(`${slug} ${index < 0 ? 'try' : `also ${demo.pack}`}`, async () => {
        // A fresh page and session: the disabled set and the fixture are per page, and a fake
        // fixture replays from its first reply only in a session that has not used it.
        await page.goto('/')
        await expect(node(page, 'gateway')).toBeVisible()
        await newSession(page)
        await page.getByTestId('guide-button').click()
        await page.locator(`[data-testid=lesson-row][data-slug="${slug}"]`).click()
        const chapter = page.getByTestId('guide-drawer').getByTestId('guide-chapter')
        await expect(chapter).toHaveAttribute('data-slug', slug)
        const button = index < 0 ? chapter.getByTestId('lesson-try-button') : chapter.getByTestId('lesson-also').nth(index).getByTestId('lesson-also-button')
        await button.click()
        await expect(page.getByTestId('pack-picker')).toHaveAttribute('data-pack', demo.pack, { timeout: 30_000 })
        await expect(page.getByTestId('composer-input')).toHaveValue(demo.prompt)
        if (demo.fixture) await expect(page.getByTestId('fixture-select')).toContainText(demo.fixture)
        await page.getByTestId('send-button').click()
        expect(await finish(page)).toBe('done')
        for (const id of demo.disable ?? []) await expect(node(page, id)).toHaveAttribute('data-state', 'skipped')

        const events = await lastTurnEvents(page, request)
        expect(events[0]!.summary?.pack).toBe(demo.pack)
        const turnId = await page.getByTestId('chat-turn').last().getAttribute('data-turn-id')
        const summary = (await (await request.get(`/v1/turns/${turnId}`)).json()) as { message: string; reply: string }
        const off = new Set(demo.disable ?? [])
        const exit = (id: string) => events.find((e) => e.type === 'node_exit' && e.node_id === id)
        // The failure each switch chapter describes.
        if (off.has('input_guardrails')) {
          // Raw values kept: the stored message (what the model saw) still holds the personal data.
          const raw = PII.exec(demo.prompt)![0]
          expect(summary.message).toContain(raw)
          await expect(page.getByTestId('model-saw')).toHaveCount(0)
        }
        if (off.has('policy')) {
          // The action runs with no approval, allowed by the disabled rule.
          expect(events.some((e) => e.type === 'approval_requested')).toBe(false)
          expect(exit('policy')?.summary).toMatchObject({ rule: 'disabled', decision: 'allow' })
          await expect(page.getByTestId('approval-card')).toHaveCount(0)
        }
        if (off.has('output_guardrails')) {
          // The leaked value reaches the user in the reply.
          expect(summary.reply).toMatch(PII)
          await expect(page.getByTestId('assistant-message').last()).toContainText(PII.exec(summary.reply)![0])
        }
        if (off.has('memory_writer')) {
          // Nothing written.
          expect(events.some((e) => e.type === 'memory_written')).toBe(false)
          expect(exit('memory_writer')?.summary).toMatchObject({ written: 0 })
        }
        ran++
      })
    }
  }
  expect(ran).toBeGreaterThanOrEqual(lessons.length)
  await page.goto('/')
  await pickPack(page, '_starter')
})
