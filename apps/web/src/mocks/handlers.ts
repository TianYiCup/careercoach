import { handlers as apiHandlers } from './handlers/api'
import { authHandlers } from './handlers/auth'

// Auth handlers come first so the more specific `/auth/...` paths
// are matched before any future wildcard handler we add for catch-all.
export const allHandlers = [...authHandlers, ...apiHandlers]
