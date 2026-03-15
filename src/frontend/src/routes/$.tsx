import { createFileRoute, redirect } from '@tanstack/react-router'

// Catch-all route mapping to 404
export const Route = createFileRoute('/$')({
  beforeLoad: () => {
    throw redirect({
      to: '/404',
    })
  },
})
