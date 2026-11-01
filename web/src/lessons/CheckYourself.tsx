import { useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import { CircleCheck, CircleX } from 'lucide-react'
import {
  Questionnaire,
  QuestionnaireActions,
  QuestionnaireChoice,
  QuestionnaireChoices,
  QuestionnaireError,
  QuestionnaireItem,
  QuestionnaireNext,
  QuestionnairePrevious,
  QuestionnaireProgress,
  QuestionnaireTitle,
} from '@/components/ui/questionnaire'
import type { CheckQuestion } from './parse'
import { answersOf } from './progress'
import { choiceLetter, firstOpen, quizView } from './quiz'
import { useLessonProgress } from './useProgress'

/** The explanation as a sentence: chapters write `Answer: the reason...`. */
const sentence = (text: string) => text.charAt(0).toUpperCase() + text.slice(1)

/** Inline markdown (code, emphasis) without a wrapping paragraph. */
function Inline({ text }: { text: string }) {
  return (
    <ReactMarkdown allowedElements={['code', 'em', 'strong', 'p']} unwrapDisallowed components={{ p: ({ children }) => <>{children}</> }}>
      {text}
    </ReactMarkdown>
  )
}

/**
 * The chapter's Check yourself section as a Questionnaire: one question at a time, Previous and
 * Next, letters as shortcuts. A choice says at once whether it was right and shows the answer's
 * explanation. Answers are kept per viewer; the chapter is checked once all are answered.
 */
export function CheckYourself({ slug, questions }: { slug: string; questions: CheckQuestion[] }) {
  const stored = useLessonProgress((s) => s.progress[slug])
  const answer = useLessonProgress((s) => s.answer)
  const answers = useMemo(() => answersOf({ [slug]: stored ?? {} }, slug), [slug, stored])
  const view = quizView(questions, answers)
  const items = useMemo(() => questions.map((q, i) => ({ name: `q${i}`, choices: q.choices.map((_, j) => ({ value: `c${j}` })) })), [questions])

  return (
    <div className="flex flex-col gap-3" data-testid="check-yourself" data-complete={view.complete ? 'true' : 'false'}>
      <Questionnaire
        key={slug}
        items={items}
        defaultItem={`q${firstOpen(view)}`}
        shortcuts="letters"
        aria-label="Check yourself"
        onSubmit={(e) => e.preventDefault()}
        className="gap-4"
      >
        <div className="flex items-center justify-between gap-2">
          <QuestionnaireProgress />
          <span className="text-xs text-muted-foreground tabular-nums" data-testid="check-count">
            {view.complete ? `All ${view.total} answered, ${view.right} right` : `${view.answered} of ${view.total} answered`}
          </span>
        </div>
        {questions.map((q, i) => {
          const item = view.items[i]!
          return (
            <QuestionnaireItem key={i} name={`q${i}`} className="gap-3" data-testid="check-question">
              <QuestionnaireTitle className="mb-0! text-sm leading-relaxed font-medium">
                <Inline text={q.text} />
              </QuestionnaireTitle>
              <QuestionnaireChoices className="gap-2">
                {q.choices.map((c, j) => (
                  <QuestionnaireChoice key={j} value={`c${j}`} checked={item.chosen === j} onChange={() => answer(slug, i, j, questions.length)} className="px-3 py-2.5">
                    <span>
                      <Inline text={c.text} />
                    </span>
                  </QuestionnaireChoice>
                ))}
              </QuestionnaireChoices>
              <QuestionnaireError>Choose an answer to see why and go on.</QuestionnaireError>
              <div role="status" aria-live="polite" className="empty:hidden">
                {item.chosen !== null && (
                  <div className="flex flex-col gap-1.5 border-l pl-3 text-sm leading-relaxed text-muted-foreground" data-testid="check-feedback" data-correct={item.correct ? 'true' : 'false'}>
                    <p className={item.correct ? 'flex items-center gap-1.5 font-medium text-foreground' : 'flex items-center gap-1.5 font-medium text-destructive'}>
                      {item.correct ? <CircleCheck className="size-4 text-state-done" aria-hidden /> : <CircleX className="size-4" aria-hidden />}
                      {item.correct ? 'Right.' : `Not quite. The answer is ${choiceLetter(item.rightIndex)}.`}
                    </p>
                    <div className="[&_code]:rounded-sm [&_code]:bg-muted [&_code]:px-1 [&_code]:font-mono [&_code]:text-[0.85em]">
                      <ReactMarkdown>{sentence(q.answer)}</ReactMarkdown>
                    </div>
                  </div>
                )}
              </div>
            </QuestionnaireItem>
          )
        })}
        <QuestionnaireActions>
          <QuestionnairePrevious size="sm">Previous question</QuestionnairePrevious>
          <QuestionnaireNext size="sm">Next question</QuestionnaireNext>
        </QuestionnaireActions>
      </Questionnaire>
    </div>
  )
}
