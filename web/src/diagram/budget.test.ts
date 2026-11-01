import { describe, expect, it } from 'vitest'
import { budgetReadout, rowShare, type BudgetRow } from './budget'

const row = (used: number, limit: number): BudgetRow => ({ label: 'x', used, limit, text: '' })

describe('the status bar budget readout', () => {
  it('names the most used budget', () => {
    expect(budgetReadout([row(10, 100), row(54, 100)])).toBe('54% budget')
    expect(rowShare(row(150, 100))).toBe(100)
  })

  it('falls back to the requests left in public mode, then to Budget', () => {
    const limits = { per_ip_per_hour: 30, remaining_this_hour: 27, max_steps: 8, budget_tokens: 16000 } as Parameters<typeof budgetReadout>[1]
    expect(budgetReadout([], limits)).toBe('27 requests left')
    expect(budgetReadout([])).toBe('Budget')
  })
})
