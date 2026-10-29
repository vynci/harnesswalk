import hljs from 'highlight.js/lib/core'
import json from 'highlight.js/lib/languages/json'
import python from 'highlight.js/lib/languages/python'
import yaml from 'highlight.js/lib/languages/yaml'

hljs.registerLanguage('python', python)
hljs.registerLanguage('yaml', yaml)
hljs.registerLanguage('json', json)

function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

/** Highlighted HTML for code. highlight.js escapes its input, so the result is safe to inject. */
export function highlight(code: string, language: string): string {
  if (!hljs.getLanguage(language)) return escapeHtml(code)
  return hljs.highlight(code, { language, ignoreIllegals: true }).value
}
