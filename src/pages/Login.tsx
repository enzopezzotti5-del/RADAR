import { useState } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { Activity, AlertCircle, Loader2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useAuth } from '@/hooks/use-auth'

export default function Login() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [authError, setAuthError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const { signIn, isAuthenticated, loading } = useAuth()
  const navigate = useNavigate()

  if (loading) return null
  if (isAuthenticated) return <Navigate to="/" />

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault()

    if (!email.trim() || !password) {
      setAuthError('Preencha email e senha.')
      return
    }

    setSubmitting(true)
    setAuthError('')

    const result = await signIn(email, password)

    setSubmitting(false)

    if (result.error) {
      setAuthError('Credenciais invalidas. Verifique seu email e senha.')
      return
    }

    navigate('/')
  }

  return (
    <div className="flex h-screen w-full items-center justify-center bg-background px-4">
      <Card className="w-full max-w-md border-border bg-card">
        <CardHeader className="space-y-4">
          <div className="flex justify-center">
            <div className="rounded-full bg-blue-500/10 p-3">
              <Activity className="h-8 w-8 text-blue-500" />
            </div>
          </div>

          <div className="space-y-1 text-center">
            <CardTitle className="text-2xl">Radar Faturas</CardTitle>
            <CardDescription>
              Entre para acessar o painel de automacao de faturas
            </CardDescription>
          </div>
        </CardHeader>

        <CardContent>
          <form onSubmit={handleLogin} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                placeholder="seu@email.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                disabled={submitting}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="password">Senha</Label>
              <Input
                id="password"
                type="password"
                placeholder="********"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={submitting}
              />

              {authError && (
                <div className="mt-2 flex items-start gap-2 rounded-md bg-red-500/10 p-3">
                  <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-500" />
                  <p className="text-sm text-red-500">{authError}</p>
                </div>
              )}
            </div>

            <Button type="submit" className="mt-2 w-full" disabled={submitting}>
              {submitting ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Entrando...
                </>
              ) : (
                'Entrar no sistema'
              )}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
