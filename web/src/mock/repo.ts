// Repo files the mock serves for the inspector: the real Python source of each node and the
// real config, prompt and skill files. Bundled only into mock builds.
const sources = import.meta.glob('../../../harnesswalk/**/*.py', { query: '?raw', import: 'default', eager: true }) as Record<string, string>
// The packs the mock serves (mock/packs.ts): the starter pack and the finance pack, text files only.
const files = import.meta.glob(['../../../config/*.yaml', '../../../datasets/_starter/**/*.{md,yaml,jsonl}', '../../../datasets/finance/**/*.{md,yaml,jsonl}'], {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

function key(path: string): string {
  return `../../../${path}`
}

export function sourceFor(sourceHint: string): string | undefined {
  return sources[key(sourceHint)]
}

export function repoFile(path: string): string | undefined {
  return files[key(path)]
}

/** Repo files under a directory, as [path relative to the repo, content]. */
export function repoFilesUnder(dir: string): [string, string][] {
  const prefix = key(dir.endsWith('/') ? dir : `${dir}/`)
  return Object.entries(files)
    .filter(([k]) => k.startsWith(prefix))
    .map(([k, v]) => [k.slice('../../../'.length), v])
}
