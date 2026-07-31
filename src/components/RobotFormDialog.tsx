import { useEffect, useState } from 'react'
import { Loader2 } from 'lucide-react'

import { deriveCommand } from '@/lib/api'
import { validateRobotForm } from '@/lib/validation'
import { createRobot, updateRobot, type Robot } from '@/services/api'
import { useToast } from '@/hooks/use-toast'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'

interface RobotFormDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  robot: Robot | null
  onSaved: () => void
}

interface FormData {
  name: string
  type: string
  description: string
  repository: string
  branch: string
  main_file_path: string
  execution_command: string
  dependencies_path: string
  active: boolean
  timeout_minutes: string
}

const DEFAULT_REPOSITORY_URL = 'https://github.com/acaoengenhariaeinstalacoes/energia-automacao.git'

function extractFieldErrors(error: unknown): Record<string, string> {
  const data = (error as { data?: { data?: Record<string, { message?: string }> } })?.data?.data
  if (!data) return {}
  return Object.fromEntries(
    Object.entries(data).map(([field, details]) => [field, details?.message || 'Valor invalido.']),
  )
}

function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Nao foi possivel salvar a tarefa.'
}

const defaultForm: FormData = {
  name: '',
  type: 'energia',
  description: '',
  repository: DEFAULT_REPOSITORY_URL,
  branch: 'main',
  main_file_path: '',
  execution_command: '',
  dependencies_path: '',
  active: true,
  timeout_minutes: '120',
}

export function RobotFormDialog({ open, onOpenChange, robot, onSaved }: RobotFormDialogProps) {
  const [form, setForm] = useState<FormData>(defaultForm)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [submitting, setSubmitting] = useState(false)
  const { toast } = useToast()

  useEffect(() => {
    if (open) {
      if (robot) {
        setForm({
          name: robot.name || '',
          type: 'energia',
          description: robot.description || '',
          repository: robot.repository || DEFAULT_REPOSITORY_URL,
          branch: robot.branch || 'main',
          main_file_path: robot.main_file_path || '',
          execution_command: robot.execution_command || '',
          dependencies_path: robot.dependencies_path || '',
          active: robot.active ?? true,
          timeout_minutes: String(robot.timeout_minutes || 120),
        })
      } else {
        setForm(defaultForm)
      }
      setErrors({})
    }
  }, [open, robot])

  const setField = (key: keyof FormData, value: string | boolean) => {
    setForm((prev) => {
      const next = { ...prev, [key]: value }
      if (key === 'main_file_path' && typeof value === 'string' && value && !next.execution_command) {
        next.execution_command = deriveCommand(value)
      }
      return next
    })
  }

  const handleSubmit = async () => {
    const validationErrors = validateRobotForm(form)
    if (Object.keys(validationErrors).length > 0) {
      setErrors(validationErrors)
      return
    }

    setSubmitting(true)

    try {
      const data: Record<string, unknown> = {
        ...form,
        type: 'energia',
        timeout_minutes: parseInt(form.timeout_minutes) || 120,
        status: robot?.status || 'offline',
      }

      if (robot?.category === 'Pipelines') {
        data.pasta_template = robot.pasta_template || ''
      }

      if (robot) {
        await updateRobot(robot.id, data)
        toast({ title: 'Tarefa atualizada', description: `${form.name} foi atualizada.` })
      } else {
        await createRobot(data)
        toast({ title: 'Tarefa criada', description: `${form.name} foi criada.` })
      }

      onOpenChange(false)
      onSaved()
    } catch (err) {
      const fieldErrors = extractFieldErrors(err)
      if (Object.keys(fieldErrors).length > 0) {
        setErrors(fieldErrors)
      } else {
        toast({ title: 'Erro', description: getErrorMessage(err), variant: 'destructive' })
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{robot ? 'Ajustar tarefa do catalogo' : 'Nova tarefa'}</DialogTitle>
        </DialogHeader>

        <div className="grid gap-4 py-4">
          {robot && (
            <div className="grid grid-cols-2 gap-4 rounded-lg border border-border bg-muted/30 p-4">
              <div className="space-y-1">
                <p className="text-xs uppercase tracking-wide text-muted-foreground">task_id</p>
                <p className="font-mono text-sm">{robot.task_id || '-'}</p>
              </div>
              <div className="space-y-1">
                <p className="text-xs uppercase tracking-wide text-muted-foreground">Categoria</p>
                <p className="text-sm">{robot.category || 'Downloaders'}</p>
              </div>
            </div>
          )}

          <div className="space-y-2">
            <Label htmlFor="name">Nome da tarefa *</Label>
            <Input id="name" value={form.name} onChange={(e) => setField('name', e.target.value)} />
            {errors.name && <p className="text-sm text-red-500">{errors.name}</p>}
          </div>

          <div className="space-y-2">
            <Label htmlFor="repository">Repositorio canonico (Git URL) *</Label>
            <Input
              id="repository"
              placeholder="https://github.com/user/repo.git"
              value={form.repository}
              onChange={(e) => setField('repository', e.target.value)}
            />
            {errors.repository && <p className="text-sm text-red-500">{errors.repository}</p>}
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="branch">Branch *</Label>
              <Input id="branch" value={form.branch} onChange={(e) => setField('branch', e.target.value)} />
              {errors.branch && <p className="text-sm text-red-500">{errors.branch}</p>}
            </div>

            <div className="space-y-2">
              <Label htmlFor="main_file_path">Script relativo a raiz *</Label>
              <Input
                id="main_file_path"
                placeholder="core/downloaders/enel_sp/enel_sp.py"
                value={form.main_file_path}
                onChange={(e) => setField('main_file_path', e.target.value)}
              />
              {errors.main_file_path && <p className="text-sm text-red-500">{errors.main_file_path}</p>}
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="execution_command">Comando de execucao *</Label>
              <Input
                id="execution_command"
                value={form.execution_command}
                onChange={(e) => setField('execution_command', e.target.value)}
              />
              {errors.execution_command && <p className="text-sm text-red-500">{errors.execution_command}</p>}
            </div>

            <div className="space-y-2">
              <Label htmlFor="dependencies_path">Arquivo de dependencias</Label>
              <Input
                id="dependencies_path"
                placeholder="requirements.txt"
                value={form.dependencies_path}
                onChange={(e) => setField('dependencies_path', e.target.value)}
              />
              {errors.dependencies_path && <p className="text-sm text-red-500">{errors.dependencies_path}</p>}
            </div>
          </div>

          <div className="flex items-center gap-2">
            <Switch checked={form.active} onCheckedChange={(value) => setField('active', value)} />
            <span className="text-sm text-muted-foreground">{form.active ? 'Ativo' : 'Inativo'}</span>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancelar
          </Button>
          <Button onClick={handleSubmit} disabled={submitting}>
            {submitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {robot ? 'Salvar ajustes' : 'Criar tarefa'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
