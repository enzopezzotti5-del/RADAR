import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { addMonths, eachDayOfInterval, endOfMonth, endOfWeek, isSameDay, isSameMonth, isToday, startOfWeek, subMonths } from 'date-fns'
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { CalendarDays, ChevronLeft, ChevronRight, RefreshCw } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { StatusBadge } from '@/components/StatusBadge'
import { formatDuration } from '@/lib/formatters'
import { calendarApi, runsApi, tasksApi, type HistoryRun, type LiveRun, type Task } from '@/lib/api'
import { calendarStatusLabel, calendarStatusOrder, toCalendarStatus, type CalendarStatus } from '@/lib/radar-status'
import { concessionariaFromTask, dateFromKey, dateKey, dateKeyFromRadar, formatDay, formatMonth, formatShortDate, monthKey, monthStart, todayLocal } from '@/lib/calendar-utils'
import type { CalendarMetricSummary } from '@/services/legacyCompat'

type CalendarRun = HistoryRun & { concessionaria: string; statusKey: CalendarStatus }
type StatusFilter = CalendarStatus | 'todos'
type MonthCache = Record<string, CalendarRun[]>
type CalendarView = 'faturas' | 'execucoes'
type InvoiceMonthCache = Record<string, CalendarMetricSummary>

const chartColors: Record<CalendarStatus, string> = {
  concluido: '#22c55e', falhou: '#ef4444', cancelado: '#f97316', executando: '#3b82f6',
  aguardando: '#64748b', parando: '#f59e0b', outros: '#94a3b8',
}
const EMPTY_RUNS: CalendarRun[] = []

function InvoiceMetricsUnavailable({ selectedDay, error }: { selectedDay: Date; error: string }) {
  const metricNames = ['Processadas', 'Baixadas', 'Puladas', 'Erros', 'Outros']

  return <>
    <div className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-950 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
      <p className="font-semibold">Sem dados detalhados de faturas</p>
      <p className="mt-1">{error || 'Nao ha metricas operacionais persistidas para o periodo selecionado. Esta tela nao converte status de execucao em quantidade de faturas.'}</p>
    </div>
    <div>
      <h3 className="mb-3 text-lg font-semibold">Faturas de {formatDay(selectedDay)}</h3>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
        {metricNames.map((label) => <Card key={label}><CardContent className="p-4"><p className="text-xs text-muted-foreground">{label}</p><p className="mt-2 text-sm font-medium text-muted-foreground">Sem dados detalhados</p></CardContent></Card>)}
      </div>
    </div>
    <div className="grid gap-6 xl:grid-cols-2">
      <Card><CardHeader><CardTitle>Faturas por concessionaria</CardTitle></CardHeader><CardContent><div className="flex h-80 items-center justify-center text-center text-sm text-muted-foreground">A distribuicao por concessionaria sera exibida quando os downloaders registrarem metricas estruturadas por execucao.</div></CardContent></Card>
      <Card><CardHeader><CardTitle>Resultado mensal de faturas</CardTitle></CardHeader><CardContent><div className="flex h-80 items-center justify-center text-center text-sm text-muted-foreground">Nenhum grafico e exibido sem metricas operacionais verificaveis.</div></CardContent></Card>
    </div>
  </>
}

function liveToCalendarRun(run: LiveRun): CalendarRun {
  const statusKey = toCalendarStatus(run.status_text)
  return { id: run.run_id, task_id: run.task_id, task_name: run.task_name, started_at: run.started_at,
    finished_at: '', duration_s: 0, status: run.status_text, files_downloaded: 0, files_skipped_existing: 0,
    files_failed: 0, concessionaria: concessionariaFromTask(run.task_id, run.task_name), statusKey }
}

function countStatuses(runs: CalendarRun[]) {
  return calendarStatusOrder.reduce<Record<CalendarStatus, number>>((result, status) => {
    result[status] = runs.filter((run) => run.statusKey === status).length
    return result
  }, {} as Record<CalendarStatus, number>)
}

export default function Calendar() {
  const today = useMemo(todayLocal, [])
  const [visibleMonth, setVisibleMonth] = useState(monthStart(today))
  const [selectedDay, setSelectedDay] = useState(today)
  const [runsByMonth, setRunsByMonth] = useState<MonthCache>({})
  const cacheRef = useRef<MonthCache>({})
  const [invoiceByMonth, setInvoiceByMonth] = useState<InvoiceMonthCache>({})
  const invoiceCacheRef = useRef<InvoiceMonthCache>({})
  const [tasks, setTasks] = useState<Task[]>([])
  const [concessionariaFilter, setConcessionariaFilter] = useState('todos')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('todos')
  const [view, setView] = useState<CalendarView>('faturas')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [invoiceError, setInvoiceError] = useState('')
  const [partialData, setPartialData] = useState(false)

  const loadMonth = async (month: Date, force = false) => {
    const key = monthKey(month)
    if (!force && cacheRef.current[key]) return
    setLoading(true)
    setError('')
    try {
      const [history, live] = await Promise.all([runsApi.history(500), runsApi.live()])
      const mappedHistory = history.runs.map((run) => ({
        ...run,
        concessionaria: concessionariaFromTask(run.task_id, run.task_name),
        statusKey: toCalendarStatus(run.status),
      }))
      const allRuns = [...mappedHistory, ...live.runs.map(liveToCalendarRun)]
      const deduplicated = Array.from(new Map(allRuns.map((run) => [run.id, run])).values())
      const monthRuns = deduplicated.filter((run) => dateKeyFromRadar(run.started_at)?.startsWith(key))
      cacheRef.current = { ...cacheRef.current, [key]: monthRuns }
      setRunsByMonth(cacheRef.current)
      setPartialData(history.runs.length >= 500)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Nao foi possivel carregar as execucoes.')
    } finally {
      setLoading(false)
    }
  }

  const loadInvoiceMonth = async (month: Date, force = false) => {
    const key = monthKey(month)
    if (!force && invoiceCacheRef.current[key]) return
    setInvoiceError('')
    try {
      const summary = await calendarApi.summary(dateKey(month), dateKey(endOfMonth(month)))
      invoiceCacheRef.current = { ...invoiceCacheRef.current, [key]: summary }
      setInvoiceByMonth(invoiceCacheRef.current)
    } catch (cause) {
      setInvoiceError(cause instanceof Error ? cause.message : 'Nao foi possivel carregar as metricas de faturas.')
    }
  }

  useEffect(() => {
    void tasksApi.list().then((data) => setTasks(Object.values(data.tasks).flat())).catch(() => setTasks([]))
  }, [])

  useEffect(() => { void loadMonth(visibleMonth); void loadInvoiceMonth(visibleMonth) }, [visibleMonth])

  const monthRuns = runsByMonth[monthKey(visibleMonth)] || EMPTY_RUNS
  const invoiceSummary = invoiceByMonth[monthKey(visibleMonth)]
  const hasInvoiceMetricsForFilter = Boolean(invoiceSummary?.has_metrics && (
    concessionariaFilter === 'todos' || invoiceSummary.utilities.some((row) => row.utility === concessionariaFilter)
  ))
  const concessionarias = useMemo(() => Array.from(new Set([...tasks.map((task) => concessionariaFromTask(task.task_id, task.name)), ...monthRuns.map((run) => run.concessionaria), ...(invoiceSummary?.utilities || []).map((row) => row.utility)])).sort(), [tasks, monthRuns, invoiceSummary])
  const filteredRuns = useMemo(() => monthRuns.filter((run) =>
    (concessionariaFilter === 'todos' || run.concessionaria === concessionariaFilter) &&
    (view === 'faturas' || statusFilter === 'todos' || run.statusKey === statusFilter),
  ), [monthRuns, concessionariaFilter, statusFilter, view])
  const selectedRuns = useMemo(() => filteredRuns.filter((run) => dateKeyFromRadar(run.started_at) === dateKey(selectedDay)), [filteredRuns, selectedDay])
  const summary = useMemo(() => countStatuses(selectedRuns), [selectedRuns])
  const selectedInvoiceDay = useMemo(() => invoiceSummary?.days.find((day) => day.date === dateKey(selectedDay)), [invoiceSummary, selectedDay])
  const selectedInvoiceRows = useMemo(() => (invoiceSummary?.utilities || []).filter((row) =>
    row.date === dateKey(selectedDay) && (concessionariaFilter === 'todos' || row.utility === concessionariaFilter),
  ), [invoiceSummary, selectedDay, concessionariaFilter])
  const selectedInvoiceTotals = useMemo(() => selectedInvoiceRows.reduce((total, row) => ({
    downloaded: total.downloaded + row.downloaded,
    skipped_existing: total.skipped_existing + row.skipped_existing,
    errors: total.errors + row.errors,
    other: total.other + row.other,
    processed: total.processed + row.processed,
  }), { downloaded: 0, skipped_existing: 0, errors: 0, other: 0, processed: 0 }), [selectedInvoiceRows])

  const gridDays = useMemo(() => eachDayOfInterval({
    start: startOfWeek(visibleMonth, { weekStartsOn: 0 }),
    end: endOfWeek(endOfMonth(visibleMonth), { weekStartsOn: 0 }),
  }), [visibleMonth])

  const dayChart = useMemo(() => concessionarias.map((name) => {
    const counts = countStatuses(selectedRuns.filter((run) => run.concessionaria === name))
    return { concessionaria: name, total: Object.values(counts).reduce((sum, value) => sum + value, 0), ...counts }
  }).filter((row) => row.total > 0).sort((a, b) => b.total - a.total), [concessionarias, selectedRuns])

  const monthChart = useMemo(() => eachDayOfInterval({ start: visibleMonth, end: endOfMonth(visibleMonth) }).map((day) => {
    const counts = countStatuses(filteredRuns.filter((run) => dateKeyFromRadar(run.started_at) === dateKey(day)))
    return { day: day.getDate(), date: dateKey(day), total: Object.values(counts).reduce((sum, value) => sum + value, 0), ...counts }
  }), [visibleMonth, filteredRuns])
  const invoiceDayChart = useMemo(() => selectedInvoiceRows.map((row) => ({ concessionaria: row.utility, ...row })), [selectedInvoiceRows])
  const invoiceMonthChart = useMemo(() => eachDayOfInterval({ start: visibleMonth, end: endOfMonth(visibleMonth) }).map((day) => {
    const row = (invoiceSummary?.days || []).find((item) => item.date === dateKey(day))
    return { day: day.getDate(), date: dateKey(day), downloaded: row?.downloaded || 0, skipped_existing: row?.skipped_existing || 0, errors: row?.errors || 0, other: row?.other || 0, hasMetrics: Boolean(row) }
  }), [visibleMonth, invoiceSummary])

  const selectMonth = (month: Date) => {
    const normalized = monthStart(month)
    setVisibleMonth(normalized)
    setSelectedDay(isSameMonth(selectedDay, normalized) ? selectedDay : normalized)
  }

  return (
    <div className="space-y-6 animate-fade-in">
      <div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
        <div>
          <h2 className="text-2xl font-bold">Calendario</h2>
          <p className="text-muted-foreground">Acompanhe os resultados operacionais e as execucoes por dia, mes e concessionaria.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2" aria-label="Controles do calendario">
          <Button variant="outline" size="icon" title="Mes anterior" onClick={() => selectMonth(subMonths(visibleMonth, 1))}><ChevronLeft className="h-4 w-4" /></Button>
          <Button variant="outline" onClick={() => { setVisibleMonth(monthStart(today)); setSelectedDay(today) }}>Hoje</Button>
          <Button variant="outline" size="icon" title="Proximo mes" onClick={() => selectMonth(addMonths(visibleMonth, 1))}><ChevronRight className="h-4 w-4" /></Button>
          <Select value={concessionariaFilter} onValueChange={setConcessionariaFilter}>
            <SelectTrigger className="w-[170px]" aria-label="Filtrar por concessionaria"><SelectValue placeholder="Concessionaria" /></SelectTrigger>
            <SelectContent><SelectItem value="todos">Todas as concessionarias</SelectItem>{concessionarias.map((item) => <SelectItem key={item} value={item}>{item}</SelectItem>)}</SelectContent>
          </Select>
          {view === 'execucoes' && <Select value={statusFilter} onValueChange={(value) => setStatusFilter(value as StatusFilter)}>
            <SelectTrigger className="w-[150px]" aria-label="Filtrar por status"><SelectValue placeholder="Status" /></SelectTrigger>
            <SelectContent><SelectItem value="todos">Todos os status</SelectItem>{calendarStatusOrder.map((status) => <SelectItem key={status} value={status}>{calendarStatusLabel[status]}</SelectItem>)}</SelectContent>
          </Select>}
          <Button variant="outline" onClick={() => { void loadMonth(visibleMonth, true); void loadInvoiceMonth(visibleMonth, true) }} disabled={loading}><RefreshCw className={`mr-2 h-4 w-4 ${loading ? 'animate-spin' : ''}`} />Atualizar</Button>
        </div>
      </div>

      {partialData && <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200">A API atual retornou o limite de 500 registros. Meses mais antigos podem ter dados parciais.</div>}
      {error && <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">{error}</div>}

      <Tabs value={view} onValueChange={(value) => setView(value as CalendarView)}>
        <TabsList aria-label="Tipo de resultado do calendario"><TabsTrigger value="faturas">Faturas</TabsTrigger><TabsTrigger value="execucoes">Execucoes</TabsTrigger></TabsList>
      </Tabs>

      <Card>
        <CardHeader className="flex-row items-center justify-between"><CardTitle className="capitalize">{formatMonth(visibleMonth)}</CardTitle><CalendarDays className="h-5 w-5 text-muted-foreground" /></CardHeader>
        <CardContent>
          <div className="grid grid-cols-7 gap-1 text-center text-xs font-medium text-muted-foreground">{['Dom', 'Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sab'].map((day) => <div key={day} className="py-2">{day}</div>)}</div>
          <div className="grid grid-cols-7 gap-1">
            {gridDays.map((day) => {
              const runs = filteredRuns.filter((run) => dateKeyFromRadar(run.started_at) === dateKey(day))
              const counts = countStatuses(runs)
              const selected = isSameDay(day, selectedDay)
              const invoiceDay = (invoiceSummary?.days || []).find((item) => item.date === dateKey(day))
              return <button key={dateKey(day)} type="button" onClick={() => setSelectedDay(day)} className={`min-h-20 rounded-lg border p-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${selected ? 'border-primary bg-primary/10' : 'border-border hover:bg-muted/60'} ${!isSameMonth(day, visibleMonth) ? 'opacity-35' : ''}`}>
                <div className={`text-sm font-semibold ${isToday(day) ? 'text-primary' : ''}`}>{day.getDate()}</div>
                {view === 'execucoes' && runs.length > 0 && <div className="mt-2 space-y-0.5 text-[11px]">
                  <div>{runs.length} exec.</div>
                  <div className="text-emerald-600">{counts.concluido} concl.</div>
                  <div className="text-destructive">{counts.falhou} falhas</div>
                  {counts.executando > 0 && <div className="text-blue-600">{counts.executando} em exec.</div>}
                  {counts.cancelado > 0 && <div className="text-orange-600">{counts.cancelado} cancel.</div>}
                  {(counts.aguardando + counts.parando + counts.outros) > 0 && <div className="text-muted-foreground">{counts.aguardando + counts.parando + counts.outros} outros</div>}
                </div>}
                {view === 'faturas' && invoiceDay?.has_metrics && <div className="mt-2 space-y-0.5 text-[11px]"><div className="text-emerald-600">B {invoiceDay.downloaded}</div><div className="text-amber-600">P {invoiceDay.skipped_existing}</div><div className="text-destructive">E {invoiceDay.errors}</div></div>}
                {view === 'faturas' && !invoiceDay?.has_metrics && runs.length > 0 && <div className="mt-2 text-[11px] text-muted-foreground">Sem detalhe</div>}
              </button>
            })}
          </div>
        </CardContent>
      </Card>

      {view === 'faturas' && !hasInvoiceMetricsForFilter ? <InvoiceMetricsUnavailable selectedDay={selectedDay} error={invoiceError} /> : view === 'faturas' ? <>
      <div><h3 className="mb-3 text-lg font-semibold">Faturas de {formatDay(selectedDay)}</h3><div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
        {[
          ['Processadas', selectedInvoiceTotals.processed, 'text-foreground'], ['Baixadas', selectedInvoiceTotals.downloaded, 'text-emerald-600'], ['Puladas', selectedInvoiceTotals.skipped_existing, 'text-amber-600'], ['Erros', selectedInvoiceTotals.errors, 'text-destructive'], ['Outros', selectedInvoiceTotals.other, 'text-foreground'],
        ].map(([label, value, tone]) => <Card key={String(label)}><CardContent className="p-4"><p className="text-xs text-muted-foreground">{label}</p><p className={`mt-1 text-2xl font-bold ${tone}`}>{value}</p>{selectedInvoiceDay && !selectedInvoiceDay.metrics_complete && <p className="mt-1 text-xs text-amber-700">Parcial</p>}</CardContent></Card>)}
      </div></div>
      <div className="grid gap-6 xl:grid-cols-2">
        <Card><CardHeader><CardTitle>Faturas de {formatDay(selectedDay)} por concessionaria</CardTitle></CardHeader><CardContent><div className="h-80">{invoiceDayChart.length ? <ResponsiveContainer width="100%" height="100%"><BarChart data={invoiceDayChart} layout="vertical" margin={{ left: 20 }}><CartesianGrid strokeDasharray="3 3" /><XAxis type="number" allowDecimals={false} /><YAxis type="category" dataKey="concessionaria" width={100} /><Tooltip /><Legend /><Bar dataKey="downloaded" name="Baixadas" stackId="invoice" fill="#22c55e" /><Bar dataKey="skipped_existing" name="Puladas" stackId="invoice" fill="#f59e0b" /><Bar dataKey="errors" name="Erros" stackId="invoice" fill="#ef4444" /><Bar dataKey="other" name="Outros" stackId="invoice" fill="#94a3b8" /></BarChart></ResponsiveContainer> : <div className="flex h-full items-center justify-center text-sm text-muted-foreground">Sem dados detalhados para os filtros selecionados.</div>}</div></CardContent></Card>
        <Card><CardHeader><CardTitle className="capitalize">Faturas de {formatMonth(visibleMonth)}</CardTitle></CardHeader><CardContent><div className="h-80"><ResponsiveContainer width="100%" height="100%"><BarChart data={invoiceMonthChart} onClick={(event) => { const row = event?.activePayload?.[0]?.payload as { date?: string } | undefined; const day = row?.date ? dateFromKey(row.date) : null; if (day) setSelectedDay(day) }}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="day" /><YAxis allowDecimals={false} /><Tooltip /><Legend /><Bar dataKey="downloaded" name="Baixadas" stackId="invoice" fill="#22c55e" /><Bar dataKey="skipped_existing" name="Puladas" stackId="invoice" fill="#f59e0b" /><Bar dataKey="errors" name="Erros" stackId="invoice" fill="#ef4444" /></BarChart></ResponsiveContainer></div></CardContent></Card>
      </div>
      </> : <>
      <div><h3 className="mb-3 text-lg font-semibold">Resumo de {formatDay(selectedDay)}</h3><div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-8">
        {[['Total', selectedRuns.length, 'text-foreground'], ...calendarStatusOrder.map((status) => [calendarStatusLabel[status], summary[status], status === 'falhou' ? 'text-destructive' : status === 'concluido' ? 'text-emerald-600' : 'text-foreground'])].map(([label, value, tone]) => <Card key={String(label)}><CardContent className="p-4"><p className="text-xs text-muted-foreground">{label}</p><p className={`mt-1 text-2xl font-bold ${tone}`}>{value}</p></CardContent></Card>)}
      </div></div>

      <div className="grid gap-6 xl:grid-cols-2">
        <Card><CardHeader><CardTitle>Resultados de {formatDay(selectedDay)} por concessionaria</CardTitle></CardHeader><CardContent><div className="h-80" aria-label="Grafico de resultados diarios por concessionaria">
          {dayChart.length ? <ResponsiveContainer width="100%" height="100%"><BarChart data={dayChart} layout="vertical" margin={{ left: 20 }}><CartesianGrid strokeDasharray="3 3" /><XAxis type="number" allowDecimals={false} /><YAxis type="category" dataKey="concessionaria" width={100} /><Tooltip /><Legend />{calendarStatusOrder.map((status) => <Bar key={status} dataKey={status} name={calendarStatusLabel[status]} stackId="status" fill={chartColors[status]} />)}</BarChart></ResponsiveContainer> : <div className="flex h-full items-center justify-center text-sm text-muted-foreground">Nenhuma execucao para os filtros selecionados.</div>}
        </div></CardContent></Card>
        <Card><CardHeader><CardTitle className="capitalize">Resultados de {formatMonth(visibleMonth)}</CardTitle></CardHeader><CardContent><div className="h-80" aria-label="Grafico mensal de execucoes"><ResponsiveContainer width="100%" height="100%"><BarChart data={monthChart} onClick={(event) => { const row = event?.activePayload?.[0]?.payload as { date?: string } | undefined; const day = row?.date ? dateFromKey(row.date) : null; if (day) setSelectedDay(day) }}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="day" /><YAxis allowDecimals={false} /><Tooltip /><Legend />{calendarStatusOrder.map((status) => <Bar key={status} dataKey={status} name={calendarStatusLabel[status]} stackId="status" fill={chartColors[status]} />)}</BarChart></ResponsiveContainer></div></CardContent></Card>
      </div>

      <Card><CardHeader><CardTitle>Execucoes do dia</CardTitle></CardHeader><CardContent>{selectedRuns.length ? <div className="overflow-x-auto"><table className="w-full text-sm"><thead className="border-b text-left text-muted-foreground"><tr><th className="p-2">ID</th><th className="p-2">Tarefa</th><th className="p-2">Concessionaria</th><th className="p-2">Inicio</th><th className="p-2">Fim</th><th className="p-2">Duracao</th><th className="p-2">Status</th></tr></thead><tbody>{selectedRuns.map((run) => <tr key={run.id} className="border-b last:border-0"><td className="p-2 font-mono"><Link className="text-primary hover:underline" to={`/executions/${run.id}`}>{run.id}</Link></td><td className="p-2">{run.task_name || run.task_id}</td><td className="p-2">{run.concessionaria}</td><td className="p-2">{formatShortDate(run.started_at)}</td><td className="p-2">{formatShortDate(run.finished_at)}</td><td className="p-2">{formatDuration(run.duration_s)}</td><td className="p-2"><StatusBadge status={run.status} /></td></tr>)}</tbody></table></div> : <p className="py-6 text-center text-sm text-muted-foreground">Nenhuma execucao neste dia para os filtros selecionados.</p>}</CardContent></Card>
      </>}
    </div>
  )
}
