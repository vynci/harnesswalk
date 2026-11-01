import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ArrowLeft, ArrowRight, FlaskConical, Play } from 'lucide-react'
import type { DiagramNode, Lesson, LessonAlso, LessonCodeLink } from '@/api/types'
import { Prose } from '@/components/prose'
import { Button } from '@/components/ui/button'
import { packTitle } from '@/state/packs'
import { useApp } from '@/state/store'
import { CheckYourself } from './CheckYourself'
import { classifyLink } from './links'
import { stepEntry, type OutlineEntry } from './outline'
import { lessonRef, parseLesson } from './parse'
import { headingSlug, rehypeHeadingIds } from './slug'
import { openChapter, takeLessonFocus, useLesson, useOutline } from './useLessons'
import { useLessonProgress } from './useProgress'

const reduceMotion = () => !!globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches

/** A code walk link with the chapter it came from, for the Code tab. */
export type CodeTarget = LessonCodeLink & { slug: string }

const PROSE = '[&_h2]:mt-6 [&>h2:first-child]:mt-0 [&_h2]:scroll-mt-2 [&_h3]:mt-4 [&_h3]:scroll-mt-2'
const LINK = 'underline underline-offset-4 hover:text-foreground'

function NewTab({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noreferrer noopener" className={LINK}>
      {children}
      <span className="sr-only"> (opens in a new tab)</span>
    </a>
  )
}

/**
 * "Disable X and try it" or "Try it": sets up one demonstration for the next turn. A demonstration
 * on another pack than the session's starts a new session on that pack first, and says so.
 */
function TryDemo({ demo, also = false }: { demo: LessonAlso; also?: boolean }) {
  const diagram = useApp((s) => s.diagram)
  const capabilities = useApp((s) => s.capabilities)
  const disabled = useApp((s) => s.disabled)
  const packs = useApp((s) => s.packs)
  const pack = useApp((s) => s.pack)
  const switching = useApp((s) => s.switchingPack)
  const [ready, setReady] = useState<{ pack: string | null; switched: boolean } | null>(null)
  if (!capabilities) return null
  const mode = capabilities.mode
  const dev = mode === 'dev'
  const target = demo.pack && capabilities.features.packs ? demo.pack : null
  const known = !target || !packs || packs.some((p) => p.id === target)
  const otherPack = !!target && target !== pack
  const nodes = (demo.disable ?? []).map((id) => diagram?.nodes.find((n) => n.id === id)).filter((n): n is DiagramNode => !!n)
  const wantsDisable = (demo.disable ?? []).length > 0
  // Disabling is a dev and private mode switch; fixtures are dev only.
  const canDisable = wantsDisable && nodes.length === demo.disable!.length && nodes.every((n) => n.can_disable) && mode !== 'public' && capabilities.features.disable_nodes !== false
  const fixture = demo.fixture && dev && capabilities.fixtures.includes(demo.fixture) ? demo.fixture : null
  const plain = !wantsDisable && !!demo.prompt && (!demo.fixture || dev)
  if ((!canDisable && !plain) || !known) return null
  const labels = nodes.map((n) => n.label).join(' and ')
  const title = packTitle(packs, target)

  const run = async () => {
    if (otherPack && !(await useApp.getState().choosePack(target!))) return
    const s = useApp.getState()
    if (canDisable) for (const n of nodes) if (!s.disabled.includes(n.id)) s.toggleDisabled(n.id)
    if (fixture) s.setFixture(fixture)
    if (demo.prompt) s.setDraft(demo.prompt)
    s.focusComposer()
    setReady({ pack: target ?? pack, switched: otherPack })
  }

  const steps = [
    otherPack ? `starts a new session on ${title}` : null,
    canDisable ? `turns ${labels} off for the next turn` : null,
    fixture ? `picks the ${fixture} fixture` : null,
    demo.prompt ? 'puts the prompt in the chat' : null,
  ].filter(Boolean)
  const off = nodes.every((n) => disabled.includes(n.id))
  const done = !!ready && ready.pack === pack && (!canDisable || off)
  const base = canDisable ? `Disable ${labels} and try it` : 'Try it'
  return (
    <div className="flex flex-col gap-1.5" data-testid={also ? 'lesson-also' : 'lesson-try'} data-pack={target ?? ''}>
      {also && demo.note && <p className="text-sm leading-relaxed">{demo.note}</p>}
      <Button
        type="button"
        size="sm"
        variant="outline"
        className="self-start"
        onClick={() => void run()}
        aria-disabled={switching ? true : undefined}
        data-testid={also ? 'lesson-also-button' : 'lesson-try-button'}
      >
        {canDisable ? <FlaskConical aria-hidden /> : <Play aria-hidden />}
        {also || otherPack ? `${base} on ${title}` : base}
      </Button>
      <p className="text-xs text-muted-foreground" role="status">
        {done
          ? ready!.switched
            ? `Started a new session on ${title}. Ready: press send in the chat.`
            : 'Ready: press send in the chat.'
          : `This ${steps.join(', ')}. You press send.`}
      </p>
    </div>
  )
}

/** The chapter's demonstration, then the same one on the domain packs (`also`). */
function TryActions({ lesson }: { lesson: Lesson }) {
  const also = lesson.also ?? []
  if (!lesson.try && also.length === 0) return null
  return (
    <div className="flex flex-col gap-3">
      {lesson.try && <TryDemo demo={lesson.try} />}
      {also.length > 0 && (
        <section className="flex flex-col gap-3" aria-label="On a domain pack" data-testid="lesson-also-list">
          <h4 className="text-xs font-medium text-muted-foreground">The same demonstration on a domain pack</h4>
          {also.map((a, i) => (
            <TryDemo key={`${a.pack ?? ''}:${i}`} demo={a} also />
          ))}
        </section>
      )}
    </div>
  )
}

function ChapterNav({ entry, outline }: { entry: OutlineEntry | undefined; outline: OutlineEntry[] }) {
  const prev = entry ? stepEntry(outline, entry.slug, -1) : null
  const next = entry ? stepEntry(outline, entry.slug, 1) : null
  if (!prev && !next) return null
  const name = (e: OutlineEntry) => `${String(e.n).padStart(2, '0')} ${e.title}`
  return (
    <nav aria-label="Chapters" className="mt-8 flex flex-wrap justify-between gap-2 border-t pt-4">
      {prev ? (
        <Button type="button" variant="ghost" size="sm" className="text-muted-foreground" onClick={() => openChapter(prev)} disabled={!prev.nodeId} data-testid="lesson-prev">
          <ArrowLeft aria-hidden />
          Previous <span className="sr-only">chapter: {name(prev)}</span>
        </Button>
      ) : (
        <span />
      )}
      {next && (
        <Button type="button" variant="outline" size="sm" onClick={() => openChapter(next)} disabled={!next.nodeId} data-testid="lesson-next">
          Next chapter<span className="max-w-48 truncate text-muted-foreground">{name(next)}</span>
          <ArrowRight aria-hidden />
        </Button>
      )}
    </nav>
  )
}

/**
 * The inspector's Lessons tab: the node's chapter (`lesson_url`), its markdown in Prose with
 * GitHub heading ids, the Check yourself section as a Questionnaire, and Previous and Next. An
 * expert node opens its group chapter at its own section.
 */
export function LessonTab({ node, onCodeLink }: { node: DiagramNode; onCodeLink: (target: CodeTarget) => void }) {
  const ref = lessonRef(node.lesson_url)
  const slug = ref?.slug ?? null
  const anchor = ref?.anchor ?? null
  const lesson = useLesson(slug)
  const diagram = useApp((s) => s.diagram)
  const repoUrl = useApp((s) => s.capabilities?.repo_url ?? null)
  const { outline } = useOutline()
  const markRead = useLessonProgress((s) => s.markRead)
  const article = useRef<HTMLElement>(null)
  const data = lesson.status === 'ok' ? lesson.data : undefined
  const parsed = useMemo(() => (data ? parseLesson(data.markdown) : null), [data])
  const entry = outline.find((e) => e.slug === slug)

  useEffect(() => {
    if (data) markRead(data.slug)
  }, [data, markRead])

  // The drawer opened this chapter: focus its title once it shows.
  const shown = lesson.status === 'ok' || lesson.status === 'missing' || lesson.status === 'error'
  useEffect(() => {
    if (shown && takeLessonFocus()) document.getElementById('lesson-title')?.focus()
  }, [shown, slug])

  // An expert node starts at its own section of the group chapter.
  useEffect(() => {
    if (!data || !anchor) return
    const frame = requestAnimationFrame(() => {
      const el = article.current?.querySelector<HTMLElement>(`#${CSS.escape(anchor)}`)
      el?.scrollIntoView({ block: 'start', behavior: 'auto' })
    })
    return () => cancelAnimationFrame(frame)
  }, [data, anchor])

  const components = useMemo<Components>(() => {
    const scrollTo = (id: string) => {
      const el = article.current?.querySelector<HTMLElement>(`#${CSS.escape(id)}`)
      el?.scrollIntoView({ block: 'start', behavior: reduceMotion() ? 'auto' : 'smooth' })
    }
    const codeTarget = (path: string, symbol: string | null): CodeTarget => {
      const links = data?.code_links ?? []
      const found = links.find((l) => l.path === path && l.symbol === symbol) ?? links.find((l) => l.path === path)
      return {
        slug: data?.slug ?? '',
        path,
        symbol,
        line: found && found.symbol === symbol ? found.line : null,
        node_id: found?.node_id ?? diagram?.nodes.find((n) => n.source_hint === path)?.id ?? null,
      }
    }
    return {
      a: ({ href, children }) => {
        const link = classifyLink(href ?? '', repoUrl)
        if (link.kind === 'external') return <NewTab href={link.href}>{children}</NewTab>
        if (link.kind === 'repo') return link.href ? <NewTab href={link.href}>{children}</NewTab> : <span title={link.path}>{children}</span>
        if (link.kind === 'code') {
          const target = codeTarget(link.path, link.symbol)
          return (
            <button
              type="button"
              className={`${LINK} cursor-pointer text-left`}
              onClick={() => onCodeLink(target)}
              title={`${link.path}${target.line ? `, line ${target.line}` : ''}`}
              data-testid="lesson-code-link"
              data-path={link.path}
              data-symbol={link.symbol ?? ''}
            >
              {children}
              <span className="sr-only"> (opens in the Code tab)</span>
            </button>
          )
        }
        if (link.kind === 'anchor') {
          return (
            <a
              href={`#${link.id}`}
              className={LINK}
              onClick={(e) => {
                e.preventDefault()
                scrollTo(link.id)
              }}
            >
              {children}
            </a>
          )
        }
        const target = outline.find((e) => e.slug === link.slug)
        return (
          <button type="button" className={`${LINK} cursor-pointer text-left`} onClick={() => target && openChapter(target)} disabled={!target?.nodeId}>
            {children}
          </button>
        )
      },
    }
  }, [data, diagram, outline, onCodeLink, repoUrl])

  if (!ref) return <p className="text-sm text-muted-foreground">This node has no chapter.</p>
  if (lesson.status === 'loading' || lesson.status === 'idle') return <p className="text-xs text-muted-foreground">Loading the chapter…</p>
  if (lesson.status === 'missing' || lesson.status === 'error' || !data || !parsed) {
    return (
      <div className="flex flex-col gap-2" data-testid="lesson-tab" data-status={lesson.status}>
        <p className="text-xs text-muted-foreground tabular-nums">Chapter {slug!.slice(0, 2)}</p>
        <h3 id="lesson-title" tabIndex={-1} className="text-base font-medium outline-none" data-testid="lesson-title">
          {entry?.title ?? node.label}
        </h3>
        {lesson.status === 'error' ? (
          <p className="text-xs text-destructive">Could not load the chapter: {lesson.error}</p>
        ) : (
          <p className="text-sm text-muted-foreground" data-testid="lesson-missing">
            This chapter is not written yet.
          </p>
        )}
        {node.lesson_summary && <p className="text-sm leading-relaxed">{node.lesson_summary}</p>}
        <ChapterNav entry={entry} outline={outline} />
      </div>
    )
  }

  const parent = data.group && data.node_id ? diagram?.nodes.find((n) => n.id === data.node_id) : undefined
  const checksId = parsed.checksHeading ? headingSlug(parsed.checksHeading) : null
  return (
    <article ref={article} className="flex flex-col gap-4" aria-labelledby="lesson-title" data-testid="lesson-tab" data-status="ok" data-slug={data.slug}>
      <header className="flex flex-col gap-1">
        <p className="text-xs text-muted-foreground tabular-nums">
          Chapter {String(data.n).padStart(2, '0')}
          {parent ? ` · inside ${parent.label}` : ''} · {Math.max(1, Math.round(data.words / 200))} min read
        </p>
        <h3 id="lesson-title" tabIndex={-1} className="text-base font-medium outline-none" data-testid="lesson-title">
          {parsed.title || data.title}
        </h3>
        {(parsed.hook || data.hook) && <p className="text-sm text-muted-foreground">{parsed.hook || data.hook}</p>}
      </header>
      <TryActions lesson={data} />
      <Prose className={PROSE}>
        <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHeadingIds]} components={components}>
          {parsed.before}
        </ReactMarkdown>
      </Prose>
      {parsed.checks && (
        <section aria-labelledby={checksId!} className="mt-2 flex flex-col gap-3">
          <h2 id={checksId!} className="scroll-mt-2 text-[0.95rem] font-medium">
            {parsed.checksHeading}
          </h2>
          <CheckYourself slug={data.slug} questions={parsed.checks} />
        </section>
      )}
      {parsed.after && (
        <Prose className={PROSE}>
          <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHeadingIds]} components={components}>
            {parsed.after}
          </ReactMarkdown>
        </Prose>
      )}
      <ChapterNav entry={entry} outline={outline} />
    </article>
  )
}
