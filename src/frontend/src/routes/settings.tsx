import { createFileRoute, redirect } from '@tanstack/react-router'

export const Route = createFileRoute('/settings')({
  beforeLoad: ({ location }) => {
    throw redirect({
      to: '/app/settings',
      search: location.search,
    })
  },
})
