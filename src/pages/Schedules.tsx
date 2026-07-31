import { useCallback, useEffect, useMemo, useState } from 'react'
import { CalendarPlus, Pencil, Trash2 } from 'lucide-react'

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { useToast } from '@/hooks/use-toast'
import {
  createSchedule,
  deleteSchedule,
  getRobots,
  getSchedules,
  isLegacyHomologationMode,
  isLegacyReadOnlyMode,
  updateSchedule,
  type Robot,
  type Schedule,
} from '@/services/api'

type ScheduleRecord = Schedule & {
  expand?: {
    robot?: Robot
  }
}

interface ScheduleFormState {
  robot: string
  weekdays: number[]
  time: string
  enabled: boolean
}

interface ParsedCron {
  weekdays: number[]
  time: string
}

const WEEKDAY_OPTIONS = [
  { value: 1, label: 'Seg' },
  { value: 2, label: 'Ter' },
  { value: 3, label: 'Qua' },
  { value: 4, label: 'Qui' },
  { value: 5, label: 'Sex' },
  { value: 6, label: 'Sab' },
  { value: 0, label: 'Dom' },
]

const WEEKDAY_LABELS: Record<number, string> = {
  0: 'domingo',
  1: 'segunda',
  2: 'terca',
  3: 'quarta',
  4: 'quinta',
  5: 'sexta',
  6: 'sabado',
}

const EMPTY_FORM: ScheduleFormState = {
  robot: '',
  weekdays: [],
  time: '08:00',
  enabled: true,
}

function parseCronExpression(cronExpression: string): ParsedCron | null {
  const trimmed = cronExpression.trim()
  const parts = trimmed.split(/\s+/)

  if (parts.length !== 5) {
    return null
  }

  const [minute, hour, dayOfMonth, month, dayOfWeek] = parts

  if (dayOfMonth !== '*' || month !== '*') {
    return null
  }

  const minuteNumber = Number(minute)
  const hourNumber = Number(hour)

  if (!Number.isInteger(minuteNumber) || minuteNumber < 0 || minuteNumber > 59) {
    return null
  }

  if (!Number.isInteger(hourNumber) || hourNumber < 0 || hourNumber > 23) {
    return null
  }

  const weekdays = dayOfWeek
    .split(',')
    .map((value) => Number(value))
    .filter((value) => Number.isInteger(value) && value >= 0 && value <= 6)

  if (weekdays.length === 0) {
    return null
  }

  const uniqueWeekdays = Array.from(new Set(weekdays)).sort((first, second) => first - second)
  const time = `${String(hourNumber).padStart(2, '0')}:${String(minuteNumber).padStart(2, '0')}`

  return {
    weekdays: uniqueWeekdays,
    time,
  }
}

function buildCronExpression(weekdays: number[], time: string): string | null {
  const [hourText, minuteText] = time.split(':')
  const hour = Number(hourText)
  const minute = Number(minuteText)

  if (!Number.isInteger(hour) || hour < 0 || hour > 23) {
    return null
  }

  if (!Number.isInteger(minute) || minute < 0 || minute > 59) {
    return null
  }

  if (weekdays.length === 0) {
    return null
  }

  const normalizedWeekdays = Array.from(new Set(weekdays)).sort((first, second) => first - second)
  return `${minute} ${hour} * * ${normalizedWeekdays.join(',')}`
}

function formatWeekdays(weekdays: number[]): string {
  if (weekdays.length === 7) {
    return 'Todos os dias'
  }

  return weekdays.map((weekday) => WEEKDAY_LABELS[weekday]).join(', ')
}

function describeSchedule(cronExpression: string): string {
  const parsed = parseCronExpression(cronExpression)
  if (!parsed) {
    return cronExpression
  }

  return `${formatWeekdays(parsed.weekdays)} as ${parsed.time}`
}

export default function Schedules() {
  const [schedules, setSchedules] = useState<ScheduleRecord[]>([])
  const [robots, setRobots] = useState<Robot[]>([])
  const [loading, setLoading] = useState(true)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingSchedule, setEditingSchedule] = useState<ScheduleRecord | null>(null)
  const [form, setForm] = useState<ScheduleFormState>(EMPTY_FORM)
  const [saving, setSaving] = useState(false)
  const [scheduleToDelete, setScheduleToDelete] = useState<ScheduleRecord | null>(null)
  const [deleting, setDeleting] = useState(false)
  const { toast } = useToast()

  const robotsById = useMemo(
    () =>
      robots.reduce<Record<string, Robot>>((acc, robot) => {
        acc[robot.id] = robot
        return acc
      }, {}),
    [robots],
  )

  const loadData = useCallback(async () => {
    try {
      const [loadedSchedules, loadedRobots] = await Promise.all([getSchedules(), getRobots()])
      setSchedules(loadedSchedules as ScheduleRecord[])
      setRobots(loadedRobots)
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Nao foi possivel carregar os agendamentos.'
      toast({
        title: 'Erro',
        description: message,
        variant: 'destructive',
      })
    } finally {
      setLoading(false)
    }
  }, [toast])

  useEffect(() => {
    void loadData()
  }, [loadData])

  const openNewDialog = () => {
    if (isLegacyReadOnlyMode) {
      toast({
        title: 'Agendamentos protegidos',
        description: 'Os agendamentos sao controlados pelo backend Flask para evitar execucoes duplicadas.',
      })
      return
    }

    setEditingSchedule(null)
    setForm(EMPTY_FORM)
    setDialogOpen(true)
  }

  const openEditDialog = (schedule: ScheduleRecord) => {
    if (isLegacyReadOnlyMode) {
      toast({
        title: 'Modo legado ativo',
        description: 'Agendamentos legados estao em leitura durante esta etapa da migracao.',
      })
      return
    }

    const parsed = parseCronExpression(schedule.cron_expression)

    setEditingSchedule(schedule)
    setForm({
      robot: schedule.robot,
      weekdays: parsed?.weekdays || [],
      time: parsed?.time || '08:00',
      enabled: schedule.enabled,
    })
    setDialogOpen(true)
  }

  const toggleWeekday = (weekday: number, checked: boolean) => {
    setForm((current) => ({
      ...current,
      weekdays: checked
        ? [...current.weekdays, weekday].sort((first, second) => first - second)
        : current.weekdays.filter((value) => value !== weekday),
    }))
  }

  const saveSchedule = async () => {
    if (!form.robot) {
      toast({
        title: 'Downloader obrigatorio',
        description: 'Selecione um downloader para o agendamento.',
        variant: 'destructive',
      })
      return
    }

    if (form.weekdays.length === 0) {
      toast({
        title: 'Dias obrigatorios',
        description: 'Selecione pelo menos um dia da semana.',
        variant: 'destructive',
      })
      return
    }

    const cronExpression = buildCronExpression(form.weekdays, form.time)

    if (!cronExpression) {
      toast({
        title: 'Horario invalido',
        description: 'Informe um horario valido para o agendamento.',
        variant: 'destructive',
      })
      return
    }

    setSaving(true)

    try {
      const payload = {
        robot: form.robot,
        cron_expression: cronExpression,
        enabled: form.enabled,
      }

      if (editingSchedule) {
        await updateSchedule(editingSchedule.id, payload)
        toast({
          title: 'Agendamento atualizado',
          description: 'As alteracoes foram salvas.',
        })
      } else {
        await createSchedule(payload)
        toast({
          title: 'Agendamento criado',
          description: 'O novo agendamento foi salvo com sucesso.',
        })
      }

      setDialogOpen(false)
      setEditingSchedule(null)
      setForm(EMPTY_FORM)
      await loadData()
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Nao foi possivel salvar o agendamento.'
      toast({
        title: 'Erro',
        description: message,
        variant: 'destructive',
      })
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async () => {
    if (!scheduleToDelete) {
      return
    }

    setDeleting(true)

    try {
      await deleteSchedule(scheduleToDelete.id)
      toast({
        title: 'Agendamento excluido',
        description: 'O agendamento foi removido com sucesso.',
      })
      setScheduleToDelete(null)
      await loadData()
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Nao foi possivel excluir o agendamento.'
      toast({
        title: 'Erro',
        description: message,
        variant: 'destructive',
      })
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="space-y-6 animate-fade-in">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold">Agendamentos</h2>
          <p className="text-muted-foreground">
            {isLegacyReadOnlyMode
              ? 'Visualizacao dos agendamentos do Radar atual durante a transicao.'
              : isLegacyHomologationMode
                ? 'Homologacao local dos agendamentos do novo Radar.'
                : 'Gerencie execucoes recorrentes dos seus downloaders.'}
          </p>
        </div>
        <Button onClick={openNewDialog} disabled={isLegacyReadOnlyMode}>
          <CalendarPlus className="mr-2 h-4 w-4" /> Novo agendamento
        </Button>
      </div>

      {isLegacyReadOnlyMode && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          Os agendamentos sao controlados pelo backend Flask para evitar execucoes duplicadas.
        </div>
      )}

      {isLegacyHomologationMode && (
        <div className="rounded-lg border border-emerald-300 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
          Modo de homologacao ativo. Os agendamentos abaixo usam a base nova local, sem alterar o Radar atual.
        </div>
      )}

      {loading ? (
        <div className="animate-pulse py-12 text-center text-muted-foreground">
          Carregando agendamentos...
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border bg-card">
          <Table>
            <TableHeader className="bg-muted/50">
              <TableRow>
                <TableHead>Downloader</TableHead>
                <TableHead>Recorrencia</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Atualizado em</TableHead>
                <TableHead className="text-right">Acoes</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {schedules.map((schedule) => {
                const robot = schedule.expand?.robot || robotsById[schedule.robot]
                const updatedLabel = schedule.updated ? new Date(schedule.updated).toLocaleString() : '-'
                return (
                  <TableRow key={schedule.id} className="hover:bg-muted/30">
                    <TableCell className="font-medium">{robot?.name || 'Downloader removido'}</TableCell>
                    <TableCell className="text-sm">{describeSchedule(schedule.cron_expression)}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {schedule.enabled ? 'Ativo' : 'Inativo'}
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">{updatedLabel}</TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-2">
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => openEditDialog(schedule)}
                          disabled={isLegacyReadOnlyMode}
                        >
                          <Pencil className="mr-1 h-3.5 w-3.5" /> Editar
                        </Button>
                        <Button
                          variant="destructive"
                          size="sm"
                          onClick={() => setScheduleToDelete(schedule)}
                          disabled={isLegacyReadOnlyMode}
                        >
                          <Trash2 className="mr-1 h-3.5 w-3.5" /> Excluir
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                )
              })}

              {schedules.length === 0 && (
                <TableRow>
                  <TableCell colSpan={5} className="py-12 text-center text-muted-foreground">
                    Nenhum agendamento cadastrado ainda.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      )}

      <Dialog open={dialogOpen} onOpenChange={(open) => !saving && setDialogOpen(open)}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>{editingSchedule ? 'Editar agendamento' : 'Novo agendamento'}</DialogTitle>
          </DialogHeader>

          <div className="grid gap-5 py-4">
            <div className="space-y-2">
              <Label htmlFor="schedule-robot">Downloader</Label>
              <select
                id="schedule-robot"
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                value={form.robot}
                onChange={(event) => setForm((current) => ({ ...current, robot: event.target.value }))}
                disabled={saving}
              >
                <option value="">Selecione um downloader</option>
                {robots.map((robot) => (
                  <option key={robot.id} value={robot.id}>
                    {robot.name}
                  </option>
                ))}
              </select>
            </div>

            <div className="grid gap-4 md:grid-cols-[1fr_180px]">
              <div className="space-y-3">
                <Label>Dias da semana</Label>
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                  {WEEKDAY_OPTIONS.map((weekday) => (
                    <label
                      key={weekday.value}
                      className="flex items-center gap-2 rounded-md border border-border px-3 py-2 text-sm"
                    >
                      <Checkbox
                        checked={form.weekdays.includes(weekday.value)}
                        onCheckedChange={(checked) => toggleWeekday(weekday.value, checked === true)}
                        disabled={saving}
                      />
                      <span>{weekday.label}</span>
                    </label>
                  ))}
                </div>
              </div>

              <div className="space-y-2">
                <Label htmlFor="schedule-time">Horario</Label>
                <Input
                  id="schedule-time"
                  type="time"
                  value={form.time}
                  onChange={(event) => setForm((current) => ({ ...current, time: event.target.value }))}
                  disabled={saving}
                />
              </div>
            </div>

            <div className="rounded-lg border border-border px-4 py-3 text-sm text-muted-foreground">
              {form.weekdays.length > 0
                ? `Este agendamento sera executado em ${formatWeekdays(
                    Array.from(new Set(form.weekdays)).sort((a, b) => a - b),
                  )} as ${form.time}.`
                : 'Selecione os dias da semana e o horario para montar o agendamento.'}
            </div>

            <div className="flex items-center justify-between rounded-lg border border-border px-4 py-3">
              <div>
                <Label htmlFor="schedule-enabled">Agendamento ativo</Label>
                <p className="text-sm text-muted-foreground">
                  Desative se quiser manter o cadastro sem executar automaticamente.
                </p>
              </div>
              <Switch
                id="schedule-enabled"
                checked={form.enabled}
                onCheckedChange={(checked) => setForm((current) => ({ ...current, enabled: checked }))}
                disabled={saving}
              />
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)} disabled={saving}>
              Cancelar
            </Button>
            <Button onClick={() => void saveSchedule()} disabled={saving}>
              {saving ? 'Salvando...' : editingSchedule ? 'Salvar alteracoes' : 'Criar agendamento'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog
        open={Boolean(scheduleToDelete)}
        onOpenChange={(open) => !open && setScheduleToDelete(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Excluir agendamento</AlertDialogTitle>
            <AlertDialogDescription>
              {scheduleToDelete
                ? `Tem certeza que deseja excluir o agendamento de ${
                    scheduleToDelete.expand?.robot?.name || robotsById[scheduleToDelete.robot]?.name || 'este downloader'
                  }?`
                : 'Esta acao nao pode ser desfeita.'}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>Cancelar</AlertDialogCancel>
            <AlertDialogAction onClick={handleDelete} disabled={deleting}>
              {deleting ? 'Excluindo...' : 'Excluir'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
