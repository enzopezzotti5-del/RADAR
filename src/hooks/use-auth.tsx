import { createContext, useContext, useEffect, useState, ReactNode } from 'react'
import { isLegacyCompatEnabled } from '@/services/api'
import { radarLogin, radarSessionStatus } from '@/services/legacyCompat'

interface AuthContextType {
  user: any
  isAuthenticated: boolean
  authEnabled: boolean
  signIn: (email: string, password: string) => Promise<{ error: any }>
  signOut: () => Promise<void>
  loading: boolean
}

const AuthContext = createContext<AuthContextType | undefined>(undefined)

export const useAuth = () => {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used within an AuthProvider')
  return context
}

export const AuthProvider = ({ children }: { children: ReactNode }) => {
  const [user, setUser] = useState<any>(null)
  const [isAuthenticated, setIsAuthenticated] = useState(false)
  const [authEnabled, setAuthEnabled] = useState(false)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (isLegacyCompatEnabled) {
      radarSessionStatus()
        .then(({ authenticated, authEnabled: enabled }) => {
          setUser(authenticated ? { username: 'Radar' } : null)
          setIsAuthenticated(authenticated)
          setAuthEnabled(enabled)
        })
        .catch(() => {
          // Server unreachable: default to open access rather than trapping
          // the user on a login screen the backend has disabled.
          setUser(null)
          setIsAuthenticated(true)
          setAuthEnabled(false)
        })
        .finally(() => setLoading(false))
      return
    }

    setLoading(false)
  }, [])

  const signIn = async (email: string, password: string) => {
    if (isLegacyCompatEnabled) {
      try {
        await radarLogin(email, password)
        setUser({ username: email })
        setIsAuthenticated(true)
        return { error: null }
      } catch (error) {
        setUser(null)
        setIsAuthenticated(false)
        return { error }
      }
    }

    return { error: new Error('Fonte de autenticacao nao configurada.') }
  }

  const signOut = async () => {
    if (isLegacyCompatEnabled) {
      try {
        // Nesta fase o logout remoto fica bloqueado para manter chamadas operacionais somente em leitura.
      } catch {
        /* intentionally ignored */
      } finally {
        setUser(null)
        setIsAuthenticated(false)
      }
      return
    }

    setUser(null)
    setIsAuthenticated(false)
  }

  return (
    <AuthContext.Provider value={{ user, isAuthenticated, authEnabled, signIn, signOut, loading }}>
      {children}
    </AuthContext.Provider>
  )
}
