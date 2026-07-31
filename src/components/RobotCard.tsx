import { Card, CardContent, CardHeader, CardTitle, CardFooter } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { PlayCircle, Clock, CheckCircle2, Pencil } from 'lucide-react'
import type { Robot } from '@/services/api'

interface RobotCardProps {
  robot: Robot
  loading: boolean
  onExecute: (id: string) => void
  onEdit: (robot: Robot) => void
  onToggleActive: (robot: Robot) => void
}

const typeLabels: Record<string, string> = {
  energia: 'Energia',
  telecom: 'Telecom',
  agua: 'Água',
  gas: 'Gás',
}

export function RobotCard({ robot, loading, onExecute, onEdit, onToggleActive }: RobotCardProps) {
  const isActive = robot.active ?? true
  return (
    <Card className="flex flex-col hover:border-primary/50 transition-colors">
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <div className="flex items-center gap-2">
          <CardTitle className="text-xl">{robot.name}</CardTitle>
          {robot.type && (
            <Badge variant="outline" className="text-xs">
              {typeLabels[robot.type] || robot.type}
            </Badge>
          )}
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-muted-foreground">{isActive ? 'Ativo' : 'Inativo'}</span>
          <Switch checked={isActive} onCheckedChange={() => onToggleActive(robot)} />
        </div>
      </CardHeader>
      <CardContent className="flex-1 space-y-3 pt-4">
        {robot.description && (
          <p className="text-sm text-muted-foreground line-clamp-2">{robot.description}</p>
        )}
        <div className="flex items-center gap-2 text-sm text-muted-foreground bg-muted/50 p-2 rounded-md">
          <Clock className="w-4 h-4 text-primary" />
          <div className="flex flex-col">
            <span className="font-medium text-foreground">Última execução</span>
            <span>
              {robot.expand?.last_execution
                ? new Date(robot.expand.last_execution.created).toLocaleString()
                : 'Nunca executado'}
            </span>
          </div>
        </div>
        <div className="flex items-center gap-2 text-sm text-muted-foreground bg-muted/50 p-2 rounded-md">
          <CheckCircle2 className="w-4 h-4 text-green-500" />
          <div className="flex flex-col">
            <span className="font-medium text-foreground">Faturas baixadas</span>
            <span>{robot.download_count || 0} arquivos no total</span>
          </div>
        </div>
      </CardContent>
      <CardFooter className="gap-2">
        <Button
          className="flex-1 font-medium"
          onClick={() => onExecute(robot.id)}
          disabled={loading}
        >
          <PlayCircle className="w-4 h-4 mr-2" />
          {loading ? 'Iniciando...' : 'Executar'}
        </Button>
        <Button variant="outline" size="icon" onClick={() => onEdit(robot)}>
          <Pencil className="w-4 h-4" />
        </Button>
      </CardFooter>
    </Card>
  )
}
