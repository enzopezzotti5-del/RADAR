import { createContext, useContext, useEffect, useState, ReactNode } from 'react'
import { isLegacyCompatEnabled } from '@/services/api'
import { radarLogin, radarSessionIsAuthenticated } from '@/services/legacyCompat'

interface AuthContextType {
  user: any
  isAuthenticated: boolean
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
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (isLegacyCompatEnabled) {
      radarSessionIsAuthenticated()
        .then((authenticated) => {
          setUser(authenticated ? { username: 'Radar' } : null)
          setIsAuthenticated(authenticated)
        })
        .catch(() => {
          setUser(null)
          setIsAuthenticated(false)
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
    <AuthContext.Provider value={{ user, isAuthenticated, signIn, signOut, loading }}>
      {children}
    </AuthContext.Provider>
  )
}
