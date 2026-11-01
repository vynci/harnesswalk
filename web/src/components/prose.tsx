import type { ComponentProps } from 'react'
import { cn } from '@/lib/utils'

/**
 * A container for rendered markdown (prompts, skills). shadcn ships no Prose component, so this
 * is the typography page's styles as one data-slot wrapper, on the same tokens as everything else.
 */
export function Prose({ className, ...props }: ComponentProps<'div'>) {
  return (
    <div
      data-slot="prose"
      className={cn(
        'text-sm leading-relaxed [&>*+*]:mt-[0.6em]',
        '[&_h1]:text-[1.05rem] [&_h1]:font-medium [&_h2]:text-[0.95rem] [&_h2]:font-medium [&_h3]:text-[0.95rem] [&_h3]:font-medium',
        '[&_ul]:list-disc [&_ul]:pl-5 [&_ol]:list-decimal [&_ol]:pl-5',
        // Inline code and bare <pre> only: a Code Block inside (the chat's fenced code) styles itself.
        '[&_:not(pre)>code]:rounded-sm [&_:not(pre)>code]:bg-muted [&_:not(pre)>code]:px-1 [&_:not(pre)>code]:font-mono [&_:not(pre)>code]:text-[0.85em]',
        '[&_pre:not([data-slot])]:overflow-x-auto [&_pre:not([data-slot])]:rounded-md [&_pre:not([data-slot])]:bg-muted [&_pre:not([data-slot])]:p-2',
        '[&_a]:underline [&_a]:underline-offset-4',
        className,
      )}
      {...props}
    />
  )
}
