// The turn's budgets as rows (used, limit, the text), and the status bar's one-line readout.
import type { PublicInfo } from '@/api/types'
import type { TurnState } from '@/state/reducer'
import type { BudgetFacts } from '@/state/turnFacts'
import { formatTokens } from './stats'

export interface BudgetRow {
  label: string
  used: number
  limit: number
  text: string
}

export function budgetRows(turn: TurnState, budget: BudgetFacts): BudgetRow[] {
  const rows: BudgetRow[] = []
  const tokens = turn.cost.input_tokens + turn.cost.output_tokens
  if (budget.budgetTokens) rows.push({ label: 'Tokens', used: tokens, limit: budget.budgetTokens, text: `${formatTokens(tokens)} / ${formatTokens(budget.budgetTokens)}` })
  if (budget.budgetUsd) rows.push({ label: 'Cost', used: turn.cost.usd, limit: budget.budgetUsd, text: `$${turn.cost.usd.toFixed(4)} / $${budget.budgetUsd.toFixed(2)}` })
  if (budget.maxSteps && budget.steps !== undefined) rows.push({ label: 'Steps', used: budget.steps, limit: budget.maxSteps, text: `${budget.steps} / ${budget.maxSteps}` })
  if (budget.budget) rows.push({ label: budget.budget.name.replace(/_/g, ' '), used: budget.budget.used, limit: budget.budget.limit, text: `${budget.budget.used} / ${budget.budget.limit}` })
  return rows
}

/** How much of a budget is used, in whole percent. */
export const rowShare = (r: BudgetRow) => Math.min(100, Math.round((r.used / r.limit) * 100))

/** The status bar's readout: the most used budget, else the requests left, else "Budget". */
export function budgetReadout(rows: BudgetRow[], limits?: PublicInfo['limits']): string {
  if (rows.length) return `${Math.max(...rows.map(rowShare))}% budget`
  return limits ? `${limits.remaining_this_hour} requests left` : 'Budget'
}
