import { beforeEach, describe, expect, it } from 'vitest'
import { usePanels } from './panels'
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
