// Pure helpers for the LLM Ops state: the chat markers for working-set saves and releases, why an
// edit is blocked right now, and the artifact an inspector edits (the API's `inspector.artifact`,
// never guessed from the node).
import type { ArtifactRef, Capabilities, InspectorArtifact } from '@/api/types'
import { featureState } from './features'

/** A marker in the chat timeline: a save to the working set, or a release becoming active. */
export interface OpsMark {
  id: string
  /** The change it stands for, so a change reported twice gets one marker. */
  key?: string
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
    key: `saved:${a.kind}/${a.name}@${a.version}`,
    kind: 'saved',
    text: `${artifactLabel(a.kind, a.name)} v${a.version} saved`,
    afterTurnId,
    at,
    ...(a.version > 1 ? { restore: { type: 'artifact' as const, kind: a.kind, name: a.name, version: a.version - 1 } } : {}),
  }
}

export function releaseMark(
  releaseId: string,
  previous: string | null,
  rolledBack: boolean,
  afterTurnId: string | null,
  at = new Date().toISOString(),
  key?: string,
): OpsMark {
  return {
    id: `mark_release_${releaseId}_${at}`,
    ...(key ? { key: `release:${key}` } : {}),
    kind: rolledBack ? 'rolled_back' : 'released',
    text: rolledBack ? `Rolled back: release ${releaseId} active` : `Release ${releaseId} active`,
    afterTurnId,
    at,
    ...(previous && previous !== releaseId ? { restore: { type: 'rollback' as const, releaseId: previous } } : {}),
  }
}

/** Why the working set cannot be edited right now, or null when it can (features.ts says why not). */
export function editBlockReason(capabilities: Capabilities | null, turnRunning: boolean): string | null {
  const edit = featureState(capabilities, 'artifact_edit')
  if (!edit.enabled) return edit.reason
  if (turnRunning) return 'A turn is running. Edits wait until it ends, so a turn never sees half a change.'
  return null
}

/** Why releases cannot be created or rolled back right now, or null when they can. */
export function releaseBlockReason(capabilities: Capabilities | null): string | null {
  return featureState(capabilities, 'release').reason
}

/** Why an eval run cannot start, or null when it can. */
export function evalBlockReason(capabilities: Capabilities | null): string | null {
  return featureState(capabilities, 'evals').reason
}

/** An artifact an inspector can edit: the API's `inspector.artifact`, with the listing's metadata when known. */
export interface EditableArtifact {
  kind: string
  name: string
  version: number
  updated_by?: string
  served_version?: number | null
}

/**
 * The artifact behind an inspector, as the API names it (`inspector.artifact` on a config
 * inspector, on each prompt item, and on code or model nodes whose settings are a config
 * artifact), with who changed it last from GET /v1/artifacts. Never guessed from the node.
 */
export function inspectorArtifact(ref: InspectorArtifact | null | undefined, list: ArtifactRef[]): EditableArtifact | undefined {
  if (!ref || typeof ref !== 'object' || typeof ref.kind !== 'string' || typeof ref.name !== 'string') return undefined
  const listed = list.find((a) => a.kind === ref.kind && a.name === ref.name)
  return {
    kind: ref.kind,
    name: ref.name,
    version: Math.max(ref.version ?? 0, listed?.version ?? 0),
    ...(listed?.updated_by ? { updated_by: listed.updated_by } : {}),
    ...(listed && listed.served_version !== undefined ? { served_version: listed.served_version } : {}),
  }
}
