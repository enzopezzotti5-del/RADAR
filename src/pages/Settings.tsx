import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { useTheme } from '@/hooks/use-theme'

export default function Settings() {
  const { theme, toggleTheme } = useTheme()

  return (
    <div className="space-y-6 animate-fade-in max-w-2xl">
      <div>
        <h2 className="text-2xl font-bold">Configurações</h2>
        <p className="text-muted-foreground">Preferências da plataforma.</p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Aparência</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <Label>Tema escuro</Label>
              <p className="text-sm text-muted-foreground">Alterna entre modo claro e escuro.</p>
            </div>
            <Switch checked={theme === 'dark'} onCheckedChange={toggleTheme} />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Sobre</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm text-muted-foreground">
          <p>Plataforma RPA de Faturas — Radar V2</p>
          <p>Integrado com o backend Flask para gerenciamento de robôs de download.</p>
          <p className="text-xs">
            CRUD de downloaders e agendamentos estarão disponíveis em breve.
          </p>
        </CardContent>
      </Card>
    </div>
  )
}
