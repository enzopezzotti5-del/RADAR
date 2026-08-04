import { useEffect, useState } from 'react'
import { Loader2 } from 'lucide-react'

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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useToast } from '@/hooks/use-toast'
import { runsApi, tasksApi, type StartRunBody, type Task } from '@/lib/api'

interface StartExecutionDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onStarted: () => void
  initialTaskId?: string
}

interface ExecutionParameters {
  month: string
  year: string
  selected_type: string
  stage_flag: string
  pasta: string
  download_condition: string
  extra_text: string
}

const DEFAULT_COMPETENCY = import.meta.env.VITE_RADAR_DEFAULT_COMPETENCY || '2026-08'
const [DEFAULT_YEAR, DEFAULT_MONTH] = DEFAULT_COMPETENCY.split('-')

const EMPTY_PARAMETERS: ExecutionParameters = {
  month: /^\d{2}$/.test(DEFAULT_MONTH || '') ? DEFAULT_MONTH : '',
  year: /^\d{4}$/.test(DEFAULT_YEAR || '') ? DEFAULT_YEAR : '',
  selected_type: '',
  stage_flag: '',
  pasta: '',
  download_condition: '',
  extra_text: '',
}

export function StartExecutionDialog({
  open,
  onOpenChange,
  onStarted,
  initialTaskId,
}: StartExecutionDialogProps) {
  const [tasks, setTasks] = useState<Task[]>([])
  const [taskId, setTaskId] = useState('')
  const [params, setParams] = useState<ExecutionParameters>(EMPTY_PARAMETERS)
  const [loadingTasks, setLoadingTasks] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const { toast } = useToast()

  useEffect(() => {
    if (!open) {
      return
    }

    let active = true

    const loadTasks = async () => {
      setLoadingTasks(true)

      try {
        const data = await tasksApi.list()

        if (!active) {
          return
        }

        const allTasks = Object.values(data.tasks).flat()
        setTasks(allTasks)
        if (initialTaskId && allTasks.some((t) => t.task_id === initialTaskId)) {
          setTaskId(initialTaskId)
        }
      } catch (error) {
        console.error('Falha ao carregar tarefas:', error)

        if (!active) {
          return
        }

        toast({
          title: 'Erro',
          description: 'Falha ao carregar tarefas do catalogo.',
          variant: 'destructive',
        })
      } finally {
        if (active) {
          setLoadingTasks(false)
        }
      }
    }

    void loadTasks()

    return () => {
      active = false
    }
  }, [open, toast, initialTaskId])

  const selectedTask = tasks.find((task) => task.task_id === taskId)

  const setParam = (key: keyof ExecutionParameters, value: string) => {
    setParams((currentParameters) => ({
      ...currentParameters,
      [key]: value,
    }))
  }

  const resetForm = () => {
    setTaskId('')
    setParams(EMPTY_PARAMETERS)
  }

  const handleDialogChange = (nextOpen: boolean) => {
    if (submitting && !nextOpen) {
      return
    }

    onOpenChange(nextOpen)

    if (!nextOpen) {
      resetForm()
    }
  }

  const validateParameters = () => {
    if (selectedTask?.supports_month_year) {
      const month = Number(params.month)
      const year = Number(params.year)

      if (!Number.isInteger(month) || month < 1 || month > 12) {
        toast({
          title: 'Mes invalido',
          description: 'Informe um mes entre 1 e 12.',
          variant: 'destructive',
        })
        return false
      }

      if (!Number.isInteger(year) || year < 2000 || year > 2100) {
        toast({
          title: 'Ano invalido',
          description: 'Informe um ano valido.',
          variant: 'destructive',
        })
        return false
      }
    }

    if (
      params.pasta.includes('..') ||
      params.pasta.startsWith('/') ||
      /^[A-Za-z]:[\\/]/.test(params.pasta)
    ) {
      toast({
        title: 'Pasta invalida',
        description: 'Use apenas um subcaminho relativo, sem "../" ou caminho absoluto.',
        variant: 'destructive',
      })
      return false
    }

    return true
  }

  const handleStart = async () => {
    if (!taskId || submitting) {
      return
    }

    if (!validateParameters()) {
      return
    }

    setSubmitting(true)

    try {
      const body: StartRunBody = {
        task_id: taskId,
        month: params.month.trim(),
        year: params.year.trim(),
        selected_type: params.selected_type.trim(),
        stage_flag: params.stage_flag.trim(),
        pasta: params.pasta.trim(),
        download_condition: params.download_condition.trim(),
        extra_text: params.extra_text.trim(),
      }

      await runsApi.start(body)

      toast({
        title: 'Execucao iniciada',
        description: 'A tarefa foi adicionada a fila do Radar com sucesso.',
      })

      resetForm()
      onOpenChange(false)
      onStarted()
    } catch (error) {
      console.error('Falha ao iniciar execucao:', error)

      const message = error instanceof Error ? error.message : 'Falha ao iniciar a execucao.'

      toast({
        title: 'Erro',
        description: message,
        variant: 'destructive',
      })
    } finally {
      setSubmitting(false)
    }
  }

  const hasDownloadConditions = Boolean(
    selectedTask &&
      Array.isArray(selectedTask.download_condition_options) &&
      selectedTask.download_condition_options.length > 0,
  )

  return (
    <Dialog open={open} onOpenChange={handleDialogChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Executar tarefa do Radar</DialogTitle>
        </DialogHeader>

        <div className="space-y-4 py-2">
          <div className="space-y-2">
            <Label>Tarefa catalogada *</Label>
            <Select value={taskId} onValueChange={setTaskId} disabled={loadingTasks || submitting}>
              <SelectTrigger>
                <SelectValue
                  placeholder={
                    loadingTasks ? 'Carregando tarefas...' : 'Selecione uma tarefa do catalogo...'
                  }
                />
              </SelectTrigger>
              <SelectContent>
                {tasks.map((task) => (
                  <SelectItem key={task.task_id} value={task.task_id}>
                    {task.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {selectedTask?.supports_month_year && (
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="execution-month">Mes</Label>
                <Input
                  id="execution-month"
                  type="number"
                  min={1}
                  max={12}
                  value={params.month}
                  onChange={(event) => setParam('month', event.target.value)}
                  placeholder="07"
                  disabled={submitting}
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="execution-year">Ano</Label>
                <Input
                  id="execution-year"
                  type="number"
                  min={2000}
                  max={2100}
                  value={params.year}
                  onChange={(event) => setParam('year', event.target.value)}
                  placeholder="2026"
                  disabled={submitting}
                />
              </div>
            </div>
          )}

          {selectedTask?.supports_type && (
            <div className="space-y-2">
              <Label htmlFor="execution-type">Tipo</Label>
              <Input
                id="execution-type"
                value={params.selected_type}
                onChange={(event) => setParam('selected_type', event.target.value)}
                placeholder={selectedTask.default_type || 'ambos'}
                disabled={submitting}
              />
            </div>
          )}

          {selectedTask?.supports_stage_flags && (
            <div className="space-y-2">
              <Label htmlFor="execution-stage-flag">Stage flag</Label>
              <Input
                id="execution-stage-flag"
                value={params.stage_flag}
                onChange={(event) => setParam('stage_flag', event.target.value)}
                disabled={submitting}
              />
            </div>
          )}

          {selectedTask?.supports_pasta && (
            <div className="space-y-2">
              <Label htmlFor="execution-folder">Pasta de saida</Label>
              <Input
                id="execution-folder"
                value={params.pasta}
                onChange={(event) => setParam('pasta', event.target.value)}
                placeholder={selectedTask.pasta_template || 'cpfl/2026/07'}
                disabled={submitting}
              />
              <p className="text-xs text-muted-foreground">
                Informe apenas um subcaminho relativo. O worker criara a pasta dentro do diretorio canonico de downloads.
              </p>
            </div>
          )}

          {hasDownloadConditions && (
            <div className="space-y-2">
              <Label>Condicao de download</Label>
              <Select
                value={params.download_condition}
                onValueChange={(value) => setParam('download_condition', value)}
                disabled={submitting}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Selecione..." />
                </SelectTrigger>
                <SelectContent>
                  {selectedTask?.download_condition_options.map((option) => (
                    <SelectItem key={option} value={option}>
                      {option}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}

          <div className="space-y-2">
            <Label htmlFor="execution-extra-text">Argumentos extras</Label>
            <Input
              id="execution-extra-text"
              value={params.extra_text}
              onChange={(event) => setParam('extra_text', event.target.value)}
              disabled={submitting}
            />
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => handleDialogChange(false)} disabled={submitting}>
            Cancelar
          </Button>

          <Button
            onClick={() => {
              void handleStart()
            }}
            disabled={!taskId || submitting || loadingTasks}
          >
            {submitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            Executar tarefa
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
