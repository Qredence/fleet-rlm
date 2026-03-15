import { createFileRoute, redirect } from '@tanstack/react-router'

export const Route = createFileRoute('/app/analytics')({
  beforeLoad: () => {
    throw redirect({
      to: '/app/workspace',
    })
  },
})
