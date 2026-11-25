import { createElement, type ComponentProps } from 'react'
import type { ExtraProps, Options } from 'react-markdown'
import remarkGfm from 'remark-gfm'

function ChatMarkdownLink({ node: _node, ...props }: ComponentProps<'a'> & ExtraProps) {
  // Fragment links stay in place for followMarkdownFragment. External links must not replace the app.
  const external = /^(?:https?:)?\/\//i.test(props.href ?? '')
  return createElement('a', external ? { ...props, target: '_blank', rel: 'noopener noreferrer' } : props)
}

// Chat Markdown can contain web research content, so do not load remote images.
export const chatMarkdownProps = {
  remarkPlugins: [remarkGfm],
  skipHtml: true,
  disallowedElements: ['img'],
  unwrapDisallowed: true,
  components: { a: ChatMarkdownLink },
} satisfies Options
