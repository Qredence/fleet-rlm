import { createFileRoute, redirect } from '@tanstack/react-router'

export const Route = createFileRoute('/app/skills')({
  beforeLoad: () => {
    throw redirect({
      to: '/app/workspace',
    })
  },
})
