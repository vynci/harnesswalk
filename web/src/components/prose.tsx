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
        '[&_code]:rounded-sm [&_code]:bg-muted [&_code]:px-1 [&_code]:font-mono [&_code]:text-[0.85em]',
        '[&_pre]:overflow-x-auto [&_pre]:rounded-md [&_pre]:bg-muted [&_pre]:p-2 [&_pre_code]:bg-transparent [&_pre_code]:p-0',
        '[&_a]:underline [&_a]:underline-offset-4',
        className,
      )}
      {...props}
    />
  )
}
