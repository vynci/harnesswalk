import { beforeEach, describe, expect, it } from 'vitest'
import { sessionReports, usePanels } from './panels'
import { useApp } from './store'

describe('the Guide panel state', () => {
  beforeEach(() => {
    usePanels.setState({ guideOpen: false, guideNode: null, codeLink: null, sessionOpen: false })
    useApp.setState({ selectedNode: null })
  })

  it('opens on the chapter list, on a node, or where the reader left it', () => {
    usePanels.getState().openGuide(null)
    expect(usePanels.getState()).toMatchObject({ guideOpen: true, guideNode: null })
    usePanels.getState().openGuide('policy')
    usePanels.getState().closeGuide()
    expect(usePanels.getState()).toMatchObject({ guideOpen: false, guideNode: 'policy' })
    usePanels.getState().openGuide()
    expect(usePanels.getState()).toMatchObject({ guideOpen: true, guideNode: 'policy' })
  })

  it('follows the node picked on the canvas only while it is open', () => {
    useApp.getState().selectNode('gateway')
    expect(usePanels.getState().guideNode).toBeNull()
    usePanels.getState().openGuide(null)
    useApp.getState().selectNode('policy')
    expect(usePanels.getState().guideNode).toBe('policy')
    // Closing the inspector keeps the chapter.
    useApp.getState().selectNode(null)
    expect(usePanels.getState().guideNode).toBe('policy')
  })

  it('hands a code walk link to the inspector with a fresh key per click', () => {
    const target = { slug: '02-input_guardrails', path: 'harnesswalk/control/pii.py', symbol: 'PiiDetector.find', line: 12, node_id: null }
    usePanels.getState().showCode('input_guardrails', target)
    const first = usePanels.getState().codeLink!.key
    usePanels.getState().showCode('input_guardrails', target)
    expect(usePanels.getState().codeLink).toMatchObject({ node: 'input_guardrails', target })
    expect(usePanels.getState().codeLink!.key).toBeGreaterThan(first)
    usePanels.getState().clearCode()
    expect(usePanels.getState().codeLink).toBeNull()
  })
})

describe('turn reports on the chat timeline', () => {
  beforeEach(() => usePanels.setState({ reports: {} }))

  it('keeps open rows per session and follows the reader on the latest turn', () => {
    const p = usePanels.getState()
    p.setReportOpen('s1', 't1', true, false)
    expect(sessionReports(usePanels.getState(), 's1')).toEqual({ open: ['t1'], follow: false })
    expect(sessionReports(usePanels.getState(), 's2')).toEqual({ open: [], follow: false })
    p.setReportOpen('s1', 't2', true, true)
    expect(sessionReports(usePanels.getState(), 's1')).toEqual({ open: ['t1', 't2'], follow: true })
    p.setReportOpen('s1', 't1', false, false)
    expect(sessionReports(usePanels.getState(), 's1')).toEqual({ open: ['t2'], follow: true })
  })

  it("opens the latest turn's report when it ends only if the reader left the one before open", () => {
    const running = { phase: 'running' } as never
    const done = { phase: 'done' } as never
    const turn = (id: string, state: unknown) => ({ turnId: id, createdAt: 0, state }) as never
    useApp.setState({ sessionId: 's1', order: ['t1', 't2'], turns: { t1: turn('t1', done), t2: turn('t2', running) } })
    useApp.setState({ turns: { t1: turn('t1', done), t2: turn('t2', done) } })
    expect(sessionReports(usePanels.getState(), 's1').open).toEqual([])
    usePanels.getState().setReportOpen('s1', 't2', true, true)
    useApp.setState({ order: ['t1', 't2', 't3'], turns: { t1: turn('t1', done), t2: turn('t2', done), t3: turn('t3', running) } })
    useApp.setState({ turns: { t1: turn('t1', done), t2: turn('t2', done), t3: turn('t3', done) } })
    expect(sessionReports(usePanels.getState(), 's1').open).toEqual(['t2', 't3'])
  })
})
