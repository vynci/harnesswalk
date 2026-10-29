// Repo files the mock serves for the inspector: the real Python source of each node and the
// real config, prompt and skill files. Bundled only into mock builds.
const sources = import.meta.glob('../../../harnesswalk/**/*.py', { query: '?raw', import: 'default', eager: true }) as Record<string, string>
const files = import.meta.glob(['../../../config/*.yaml', '../../../datasets/_starter/**/*.{md,yaml}'], {
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
