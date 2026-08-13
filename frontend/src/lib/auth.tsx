import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { api, getToken, setToken } from './api'
import type { User } from './types'

interface AuthState {
  user: User | null
  loading: boolean
  login: (email: string, password: string) => Promise<void>
  register: (email: string, password: string, fullName: string, organisation: string) => Promise<void>
  logout: () => void
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    // Restore the session from a stored token; a stale token resolves to null.
    if (!getToken()) {
      setLoading(false)
      return
    }
    api
      .me()
      .then(setUser)
      .catch(() => setToken(null))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    const onUnauthorised = () => setUser(null)
    window.addEventListener('graphene:unauthorised', onUnauthorised)
    return () => window.removeEventListener('graphene:unauthorised', onUnauthorised)
  }, [])

  const login = useCallback(async (email: string, password: string) => {
    const response = await api.login({ email, password })
    setToken(response.access_token)
    setUser(response.user)
  }, [])

  const register = useCallback(
    async (email: string, password: string, fullName: string, organisation: string) => {
      const response = await api.register({
        email,
        password,
        full_name: fullName || undefined,
        organisation: organisation || undefined,
      })
      setToken(response.access_token)
      setUser(response.user)
    },
    [],
  )

  const logout = useCallback(() => {
    setToken(null)
    setUser(null)
  }, [])

  const value = useMemo(
    () => ({ user, loading, login, register, logout }),
    [user, loading, login, register, logout],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used inside AuthProvider')
  return context
}
