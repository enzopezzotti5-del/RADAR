const apiBase = (import.meta.env.VITE_RADAR_API_BASE || '/api').replace(/\/$/, '')
// Operational Flask integration is the default. Set the variable to true only
// when intentionally publishing a read-only mirror.
const readOnly = (import.meta.env.VITE_RADAR_READ_ONLY || 'false').toLowerCase() === 'true'

export const isLegacyCompatEnabled =
  (import.meta.env.VITE_RADAR_DATA_SOURCE || 'flask').toLowerCase() === 'flask'
export const isFlaskIntegrationEnabled = isLegacyCompatEnabled && !readOnly
export const isRadarReadOnlyMode = isLegacyCompatEnabled && readOnly

function readUrl(path: string): string {
  return `${apiBase}${path}`
}

function assertReadOnly(method?: string) {
  if ((method || 'GET').toUpperCase() !== 'GET') {
    throw new Error('Modo somente leitura: esta acao nao pode ser enviada ao Radar operacional.')
  }
}

async function radarRead<T>(path: string): Promise<T> {
  assertReadOnly('GET')
  const response = await fetch(readUrl(path), { credentials: 'include' })
  let payload: any = null
  try {
    payload = await response.json()
  } catch {
    payload = null
  }
  if (!response.ok || payload?.ok === false) {
    throw new Error(payload?.error || payload?.erro || `Falha ao consultar Radar: ${response.status}`)
  }
  return payload as T
}

async function radarWrite<T>(path: string, method: 'POST' | 'DELETE', body?: unknown): Promise<T> {
  if (readOnly) {
    throw new Error('Modo somente leitura: esta acao nao pode ser enviada ao Radar operacional.')
  }
  const response = await fetch(readUrl(path), {
    method,
    credentials: 'include',
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  let payload: any = null
  try {
    payload = await response.json()
  } catch {
    payload = null
  }
  if (!response.ok || payload?.ok === false) {
    throw new Error(payload?.error || payload?.erro || `Falha ao atualizar Radar: ${response.status}`)
  }
  return payload as T
}

export async function radarSessionIsAuthenticated(): Promise<boolean> {
  const response = await fetch(readUrl('/session'), { credentials: 'include' })
  if (!response.ok) return false
  const payload = await response.json().catch(() => null)
  return Boolean(payload?.authenticated)
}

export async function radarLogin(username: string, password: string): Promise<void> {
  const body = new URLSearchParams({ username, password, next: '/' })
  const response = await fetch('/login?next=/', {
    method: 'POST',
    credentials: 'include',
    redirect: 'follow',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
  })
  if (!response.ok || !(await radarSessionIsAuthenticated())) {
    throw new Error('Credenciais invalidas ou sessao nao confirmada pelo Radar.')
  }
}

export interface LegacyCompatRobot {
  id: string
  name: string
  task_id?: string
  category?: string
  status: 'online' | 'offline'
  description?: string
  main_file_path?: string
  pasta_template?: string
  supports_month_year?: boolean
  supports_type?: boolean
  default_type?: string
  supports_stage_flags?: boolean
  supports_pasta?: boolean
  download_condition_options?: string[]
  extra_args?: string[]
  active?: boolean
  created?: string
  updated?: string
}

export interface LegacyCompatExecution {
  id: string
  robot: string
  status: 'aguardando' | 'executando' | 'parando' | 'concluido' | 'falhou' | 'cancelado'
  started_at: string
  finished_at?: string
  duration?: number
  error_message?: string
  cancel_requested?: boolean
  created?: string
  updated?: string
  expand?: { robot?: { id: string; name: string; task_id?: string } }
  compat?: { source?: string; origin?: string; command?: string }
}

export interface LegacyCompatExecutionLog {
  id: string
  execution: string
  timestamp?: string
  level: 'INFO' | 'WARN' | 'ERROR' | 'SUCCESS'
  message: string
  line_number?: number
  created?: string
  updated?: string
}

export interface LegacyCompatWorker {
  id: string
  worker_id: string
  status: 'online' | 'offline' | 'busy'
}

export interface LegacyCompatSchedule {
  id: string
  robot: string
  cron_expression: string
  enabled: boolean
  created?: string
  updated?: string
  compat?: { source?: string; readonly?: boolean; task_id?: string; next_run_at?: string }
}

export interface LegacyCompatDashboardStats {
  awaiting: number
  running: number
  completed: number
  failed: number
  cancelled: number
  totalFiles: number
  totalSkippedExisting: number
  totalFailedItems: number
  activeWorkers: number
  totalRobots: number
  activeRobots: number
}

function mapStatus(status: unknown): LegacyCompatExecution['status'] {
  switch (String(status || '').toLowerCase()) {
    case 'queued': return 'aguardando'
    case 'running': return 'executando'
    case 'stopping': return 'parando'
    case 'stopped': return 'cancelado'
    case 'completed':
    case 'success': return 'concluido'
    case 'failed':
    case 'error': return 'falhou'
    case 'aguardando':
    case 'executando':
    case 'parando':
    case 'concluido':
    case 'falhou':
    case 'cancelado': return String(status) as LegacyCompatExecution['status']
    default: return 'falhou'
  }
}

function mapRun(run: any): LegacyCompatExecution {
  const taskId = String(run.task_id || '')
  const id = String(run.run_id ?? run.id)
  return {
    id,
    robot: taskId,
    status: mapStatus(run.status ?? run.status_text),
    started_at: String(run.started_at || ''),
    finished_at: run.finished_at || undefined,
    duration: Number(run.duration_s || 0),
    error_message: run.error || run.error_message || undefined,
    cancel_requested: Boolean(run.cancel_requested),
    created: String(run.started_at || ''),
    updated: String(run.finished_at || run.started_at || ''),
    expand: { robot: { id: taskId, task_id: taskId, name: String(run.task_name || taskId) } },
    compat: { source: 'canonical_flask', origin: run.origin, command: run.command },
  }
}

export async function getCompatRobots(): Promise<LegacyCompatRobot[]> {
  const payload = await radarRead<{ tasks?: Record<string, any[]> }>('/tasks')
  return Object.entries(payload.tasks || {}).flatMap(([category, tasks]) =>
    tasks.filter((task) => category === 'Downloaders' && String(task.task_id).startsWith('dl_')).map((task) => ({
      id: String(task.task_id),
      name: String(task.name || task.task_id),
      task_id: String(task.task_id),
      category,
      status: task.exists === false ? 'offline' : 'online',
      description: task.notes || '',
      main_file_path: task.script || '',
      pasta_template: task.pasta_template || '',
      supports_month_year: Boolean(task.supports_month_year),
      supports_type: Boolean(task.supports_type),
      default_type: task.default_type || 'ambos',
      supports_stage_flags: Boolean(task.supports_stage_flags),
      supports_pasta: Boolean(task.supports_pasta),
      download_condition_options: task.download_condition_options || [],
      extra_args: task.extra_args || [],
      active: true,
      created: '',
      updated: '',
    })),
  )
}

export async function getCompatExecutions(limit = 50): Promise<LegacyCompatExecution[]> {
  const [live, history] = await Promise.all([
    radarRead<{ runs?: any[] }>('/runs/live'),
    radarRead<{ runs?: any[] }>(`/runs/history?limit=${Math.min(limit, 500)}`),
  ])
  const byId = new Map<string, LegacyCompatExecution>()
  for (const run of [...(live.runs || []), ...(history.runs || [])]) {
    const mapped = mapRun(run)
    byId.set(mapped.id, mapped)
  }
  return Array.from(byId.values()).sort((first, second) => second.id.localeCompare(first.id, undefined, { numeric: true }))
}

export async function getCompatExecution(id: string): Promise<LegacyCompatExecution> {
  const runs = await getCompatExecutions(500)
  const run = runs.find((item) => item.id === String(id))
  if (!run) throw new Error('Execucao nao encontrada no historico consultado.')
  return run
}

export async function getCompatExecutionLogs(executionId: string): Promise<LegacyCompatExecutionLog[]> {
  const payload = await getCompatRunLog(executionId, 0)
  return payload.log.split(/\r?\n/).filter(Boolean).map((message, index) => ({
    id: `${executionId}-${payload.start_line + index}`,
    execution: executionId,
    level: message.includes('[ERROR]') ? 'ERROR' : 'INFO',
    message,
    line_number: payload.start_line + index,
  }))
}

export interface LegacyCompatRunLog {
  log: string
  start_line: number
  next_line: number
  total_lines: number
  is_live: boolean
  is_running: boolean
  status_text: string
}

export interface CalendarMetricRow {
  date: string
  utility: string
  has_metrics: boolean
  downloaded: number
  skipped_existing: number
  errors: number
  other: number
  processed: number
  metrics_complete: boolean
  run_ids?: number[]
  run_count?: number
  last_update?: string | null
}

export interface CalendarMetricSummary {
  ok: true
  start: string
  end: string
  timezone: 'America/Sao_Paulo'
  totals: Omit<CalendarMetricRow, 'date' | 'utility' | 'metrics_complete'>
  days: Array<Omit<CalendarMetricRow, 'utility'>>
  utilities: CalendarMetricRow[]
  has_metrics: boolean
  metrics_complete: boolean
}

export const getCompatRunLog = (runId: string, afterLine: number) =>
  radarRead<LegacyCompatRunLog>(`/runs/${encodeURIComponent(runId)}/log?after=${afterLine}`)

export async function getCompatCalendarSummary(
  start: string,
  end: string,
  utility?: string,
): Promise<CalendarMetricSummary> {
  const query = new URLSearchParams({ start, end, timezone: 'America/Sao_Paulo' })
  if (utility && utility !== 'todos') query.set('utility', utility)
  return radarRead<CalendarMetricSummary>(`/calendar/summary?${query.toString()}`)
}

export async function getCompatWorkers(): Promise<LegacyCompatWorker[]> {
  return []
}

export async function getCompatDashboardStats(): Promise<LegacyCompatDashboardStats> {
  const payload = await radarRead<any>('/dashboard')
  return {
    awaiting: 0,
    running: Number(payload.running_now || 0),
    completed: Number(payload.success_today || 0),
    failed: Number(payload.failed_today || 0),
    cancelled: 0,
    totalFiles: 0,
    totalSkippedExisting: 0,
    totalFailedItems: 0,
    activeWorkers: 0,
    totalRobots: 0,
    activeRobots: 0,
  }
}

export async function getCompatSchedules(): Promise<LegacyCompatSchedule[]> {
  const payload = await radarRead<{ schedules?: any[] }>('/schedules')
  return (payload.schedules || []).map((schedule) => {
    const days = Array.isArray(schedule.weekdays_json) ? schedule.weekdays_json : []
    const dayOfWeek = days.length ? days.join(',') : schedule.day_of_week ?? '*'
    const [hour = '08', minute = '00'] = String(schedule.time_of_day || '08:00').split(':')
    return {
      id: String(schedule.id),
      robot: String(schedule.task_id || ''),
      cron_expression: `${Number(minute)} ${Number(hour)} * * ${dayOfWeek}`,
      enabled: Boolean(schedule.enabled),
      created: schedule.created_at || '',
      updated: schedule.last_run_at || '',
      compat: { source: 'canonical_flask', readonly: true, task_id: schedule.task_id, next_run_at: schedule.next_run_at },
    }
  })
}

export async function startCompatExecution(taskId: string, params: Record<string, unknown> = {}): Promise<unknown> {
  return radarWrite('/runs/start', 'POST', { task_id: taskId, ...params })
}
export async function stopCompatExecution(runId: string): Promise<unknown> { return radarWrite(`/runs/${encodeURIComponent(runId)}/stop`, 'POST') }
export async function createFlaskSchedule(payload: Record<string, unknown>): Promise<unknown> { return radarWrite('/schedules', 'POST', payload) }
export async function updateFlaskSchedule(id: string, payload: Record<string, unknown>): Promise<unknown> { return radarWrite(`/schedules/${encodeURIComponent(id)}`, 'POST', payload) }
export async function toggleFlaskSchedule(id: string, enabled: boolean): Promise<unknown> { return radarWrite(`/schedules/${encodeURIComponent(id)}/toggle`, 'POST', { enabled }) }
export async function deleteFlaskSchedule(id: string): Promise<unknown> { return radarWrite(`/schedules/${encodeURIComponent(id)}`, 'DELETE') }
export async function saveFlaskCatalogTask(): Promise<never> { throw new Error('Modo somente leitura: alterar catalogo esta bloqueado.') }
export async function deleteFlaskCatalogTask(): Promise<never> { throw new Error('Modo somente leitura: alterar catalogo esta bloqueado.') }
export async function createCompatSchedule(): Promise<never> { throw new Error('Modo somente leitura: alterar agendamentos esta bloqueado.') }
export async function updateCompatSchedule(): Promise<never> { throw new Error('Modo somente leitura: alterar agendamentos esta bloqueado.') }
export async function deleteCompatSchedule(): Promise<never> { throw new Error('Modo somente leitura: alterar agendamentos esta bloqueado.') }
