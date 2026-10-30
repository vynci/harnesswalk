// Pure helpers for the LLM Ops state: the chat markers for working-set saves and releases, why an
// edit is blocked right now, and how a node's artifacts are found (by the spec's artifact hint and
// the API's listing, never by node id).
import type { ArtifactRef, Capabilities, DiagramNode } from '@/api/types'

/** A marker in the chat timeline: a save to the working set, or a release becoming active. */
export interface OpsMark {
  id: string
  kind: 'saved' | 'released' | 'rolled_back'
  text: string
  /** The turn the marker follows; null before the first turn. */
  afterTurnId: string | null
  at: string
  /** What the marker's Restore puts back, when there is something to put back. */
  restore?: { type: 'artifact'; kind: string; name: string; version: number } | { type: 'rollback'; releaseId: string }
}

function capital(s: string): string {
  return s ? s[0]!.toUpperCase() + s.slice(1) : s
}

/** "Prompt", "Skill returns", "Policy": how the chat and toasts name an artifact. */
export function artifactLabel(kind: string, name: string): string {
  if (kind === 'prompt') return 'Prompt'
  if (kind === 'skill') return `Skill ${name}`
  return name && name !== kind ? `${capital(kind)} ${name}` : capital(kind)
}

export function savedMark(a: { kind: string; name: string; version: number }, afterTurnId: string | null, at = new Date().toISOString()): OpsMark {
  return {
    id: `mark_saved_${a.kind}_${a.name}_${a.version}_${at}`,
    kind: 'saved',
    text: `${artifactLabel(a.kind, a.name)} v${a.version} saved`,
    afterTurnId,
    at,
    ...(a.version > 1 ? { restore: { type: 'artifact' as const, kind: a.kind, name: a.name, version: a.version - 1 } } : {}),
  }
}

export function releaseMark(releaseId: string, previous: string | null, rolledBack: boolean, afterTurnId: string | null, at = new Date().toISOString()): OpsMark {
  return {
    id: `mark_release_${releaseId}_${at}`,
    kind: rolledBack ? 'rolled_back' : 'released',
    text: rolledBack ? `Rolled back: release ${releaseId} active` : `Release ${releaseId} active`,
    afterTurnId,
    at,
    ...(previous && previous !== releaseId ? { restore: { type: 'rollback' as const, releaseId: previous } } : {}),
  }
}

/** Why the working set cannot be edited right now, or null when it can. */
export function editBlockReason(capabilities: Capabilities | null, turnRunning: boolean): string | null {
  if (!capabilities) return 'Connecting to the API.'
  if (capabilities.mode === 'public') return 'Editing is off in public mode: shared artifacts change only in dev and private.'
  if (capabilities.features.artifact_edit !== true) return 'This API does not offer artifact editing.'
  if (turnRunning) return 'A turn is running. Edits wait until it ends, so a turn never sees half a change.'
  return null
}

/** Why releases cannot be created or rolled back right now, or null when they can. */
export function releaseBlockReason(capabilities: Capabilities | null): string | null {
  if (!capabilities) return 'Connecting to the API.'
  if (capabilities.mode === 'public') return 'Releases are managed in dev and private mode.'
  if (capabilities.features.release !== true) return 'This API does not offer releases.'
  return null
}

/** Why an eval run cannot start, or null when it can. */
export function evalBlockReason(capabilities: Capabilities | null): string | null {
  if (!capabilities) return 'Connecting to the API.'
  if (capabilities.mode === 'public') return 'Eval runs start in dev and private mode only.'
  if (capabilities.features.evals !== true) return 'This API does not offer eval runs.'
  return null
}

const PROMPT_KINDS = new Set(['prompt', 'skill'])

/** The YAML artifact kind a node's `artifact_hint` names: `config/policy.yaml` is kind policy. */
export function hintKind(hint: string | undefined): string | null {
  const m = hint ? /([A-Za-z0-9_-]+)\.ya?ml$/.exec(hint) : null
  return m ? m[1]! : null
}

/** The working-set artifacts a node shows: the prompt and skills for a prompt node, else the YAML its hint names. */
export function nodeArtifacts(node: Pick<DiagramNode, 'kind' | 'artifact_hint'>, list: ArtifactRef[]): ArtifactRef[] {
  if (node.kind === 'prompt') return list.filter((a) => PROMPT_KINDS.has(a.kind))
  const kind = hintKind(node.artifact_hint)
  return kind ? list.filter((a) => a.kind === kind) : []
}

/**
 * The artifact behind one item of a prompt inspector. http_api.md lists the system prompt first,
 * then the skills: the first item is the pack's prompt artifact, the others the skill of their name.
 */
export function promptItemArtifact(itemName: string, index: number, list: ArtifactRef[]): ArtifactRef | undefined {
  if (index === 0) {
    const prompts = list.filter((a) => a.kind === 'prompt')
    return prompts.length === 1 ? prompts[0] : prompts.find((a) => a.name === itemName)
  }
  return list.find((a) => a.kind === 'skill' && a.name === itemName)
}
