import pb from '@/lib/pocketbase/client'
import {
  createCompatSchedule,
  deleteCompatSchedule,
  getCompatDashboardStats,
  getCompatExecution,
  getCompatExecutionLogs,
  getCompatExecutions,
  getCompatRobots,
  getCompatSchedules,
  getCompatWorkers,
  isLegacyCompatEnabled,
  isFlaskIntegrationEnabled,
  stopCompatExecution,
  createFlaskSchedule,
  updateFlaskSchedule,
  deleteFlaskSchedule,
  deleteFlaskCatalogTask,
  saveFlaskCatalogTask,
  startCompatExecution,
  updateCompatSchedule,
  type LegacyCompatDashboardStats,
} from '@/services/legacyCompat'

export const isLegacyHomologationMode = false
export const isLegacyReadOnlyMode = isLegacyCompatEnabled && !isFlaskIntegrationEnabled

function toFlaskSchedulePayload(data: Record<string, any>) {
  const [, hour = '8', , , weekdays = '0'] = String(data.cron_expression || '').split(/\s+/)
  const [minute = '0'] = String(data.cron_expression || '').split(/\s+/)
  const selectedDays = weekdays.split(',').map(Number).filter((day) => Number.isInteger(day) && day >= 0 && day <= 6)
  const isDaily = selectedDays.length === 7
  return {
    task_id: data.robot,
    frequency: isDaily ? 'daily' : 'weekly',
    time_of_day: `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`,
    day_of_week: isDaily ? null : (selectedDays[0] ?? 0),
    weekdays: selectedDays,
    enabled: Boolean(data.enabled),
    params: data,
  }
}

export interface Robot {
  id: string
  name: string
  task_id?: string
  category?: string
  status: 'online' | 'offline'
  worker_id?: string
  next_execution?: string
  average_time?: number
  download_count?: number
  last_execution?: string
  description?: string
  type?: string
  repository?: string
  branch?: string
  main_file_path?: string
  execution_command?: string
  dependencies_path?: string
  pasta_template?: string
  supports_month_year?: boolean
  supports_type?: boolean
  default_type?: string
  supports_stage_flags?: boolean
  supports_pasta?: boolean
  download_condition_options?: string[]
  extra_args?: string[]
  active?: boolean
  timeout_minutes?: number
  created: string
  updated: string
  expand?: { last_execution?: Execution }
  compat?: Record<string, any>
}

export type ExecutionStatus =
  | 'aguardando'
  | 'preparando_ambiente'
  | 'atualizando_codigo'
  | 'instalando_dependencias'
  | 'executando'
  | 'parando'
  | 'concluido'
  | 'falhou'
  | 'cancelado'

export interface Execution {
  id: string
  robot: string
  status: ExecutionStatus
  started_at: string
  finished_at?: string
  duration?: number
  worker_id?: string
  files_downloaded?: number
  files_skipped_existing?: number
  files_failed?: number
  error_message?: string
  parameters?: Record<string, any>
  cancel_requested?: boolean
  created: string
  updated: string
  expand?: { robot?: Robot }
  compat?: Record<string, any>
}

export interface ExecutionLog {
  id: string
  execution: string
  timestamp: string
  level: 'INFO' | 'WARN' | 'ERROR' | 'SUCCESS'
  message: string
  line_number?: number
  created: string
  updated: string
}

export interface Worker {
  id: string
  worker_id: string
  last_heartbeat?: string
  status: 'online' | 'offline' | 'busy'
  current_execution?: string
  version?: string
  created: string
  updated: string
}

export interface Schedule {
  id: string
  robot: string
  cron_expression: string
  enabled: boolean
  created: string
  updated: string
  compat?: Record<string, any>
}

export { isLegacyCompatEnabled }
export type { LegacyCompatDashboardStats }

export const getRobots = () =>
  isLegacyCompatEnabled
    ? (getCompatRobots() as Promise<Robot[]>)
    : pb.collection('robots').getFullList<Robot>({ sort: 'name', expand: 'last_execution' })

export const getRobot = (id: string) =>
  isLegacyCompatEnabled
    ? getCompatRobots().then((robots) => {
        const found = robots.find((robot) => robot.id === id)
        if (!found) throw new Error('Robot not found')
        return found as Robot
      })
    : pb.collection('robots').getOne<Robot>(id, { expand: 'last_execution' })

export const createRobot = (data: Record<string, any>) =>
  isLegacyReadOnlyMode ? saveFlaskCatalogTask(data) : isFlaskIntegrationEnabled ? saveFlaskCatalogTask(data) : pb.collection('robots').create(data)
export const updateRobot = (id: string, data: Record<string, any>) =>
  isLegacyReadOnlyMode ? saveFlaskCatalogTask(data) : isFlaskIntegrationEnabled ? saveFlaskCatalogTask({ ...data, task_id: id }) : pb.collection('robots').update(id, data)
export const deleteRobot = (id: string) =>
  isLegacyReadOnlyMode ? deleteFlaskCatalogTask(id) : isFlaskIntegrationEnabled ? deleteFlaskCatalogTask(id) : pb.collection('robots').delete(id)

export const getExecutions = (filter = '', page = 1, perPage = 50) =>
  isLegacyCompatEnabled
    ? getCompatExecutions(perPage).then((items) => ({
        items,
        totalItems: items.length,
        page,
        perPage,
        totalPages: 1,
      }))
    : pb.collection('executions').getList<Execution>(page, perPage, {
        sort: '-created',
        filter,
        expand: 'robot',
      })

export const getExecution = (id: string) =>
  isLegacyCompatEnabled
    ? (getCompatExecution(id) as Promise<Execution>)
    : pb.collection('executions').getOne<Execution>(id, { expand: 'robot' })

export const getExecutionLogs = (executionId: string) =>
  isLegacyCompatEnabled
    ? (getCompatExecutionLogs(executionId) as Promise<ExecutionLog[]>)
    : pb.collection('execution_logs').getFullList<ExecutionLog>({
        filter: `execution="${executionId}"`,
      sort: 'line_number,created',
    })

function rejectReadOnlyAction(): Promise<never> {
  return Promise.reject(new Error('Modo somente leitura: esta acao nao pode ser enviada ao Radar operacional.'))
}

export const startExecution = (robotId: string, parameters?: Record<string, any>) =>
  isLegacyReadOnlyMode
    ? rejectReadOnlyAction()
    : isLegacyCompatEnabled
    ? startCompatExecution(robotId, parameters)
    : pb.send('/backend/v1/executions/start', {
        method: 'POST',
        body: JSON.stringify({ robotId, parameters }),
        headers: { 'Content-Type': 'application/json' },
      })

export const cancelExecution = (executionId: string) =>
  isLegacyReadOnlyMode
    ? rejectReadOnlyAction()
    : isLegacyCompatEnabled
    ? stopCompatExecution(executionId)
    : pb.send(`/backend/v1/executions/${executionId}/cancel`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      })

export const getWorkers = () =>
  isLegacyCompatEnabled
    ? (getCompatWorkers() as Promise<Worker[]>)
    : pb.collection('workers').getFullList<Worker>({ sort: '-created' })

export const sendHeartbeat = (workerId: string, status = 'online') =>
  isLegacyReadOnlyMode
    ? rejectReadOnlyAction()
    : isLegacyCompatEnabled
    ? Promise.resolve({ worker_id: workerId, status })
    : pb.send('/backend/v1/workers/heartbeat', {
        method: 'POST',
        body: JSON.stringify({ worker_id: workerId, status }),
        headers: { 'Content-Type': 'application/json' },
      })

export const getSchedules = (robotId?: string) =>
  isLegacyCompatEnabled
    ? (getCompatSchedules().then((items) =>
        robotId ? items.filter((schedule) => schedule.robot === robotId) : items,
      ) as Promise<Schedule[]>)
    : pb.collection('schedules').getFullList<Schedule>(
        robotId ? { filter: `robot="${robotId}"` } : {},
      )

export const createSchedule = (data: Record<string, any>) =>
  isFlaskIntegrationEnabled
    ? createFlaskSchedule(toFlaskSchedulePayload(data))
    : isLegacyReadOnlyMode ? createCompatSchedule() : pb.collection('schedules').create(data)

export const updateSchedule = (id: string, data: Record<string, any>) =>
  isFlaskIntegrationEnabled ? updateFlaskSchedule(id, toFlaskSchedulePayload(data)) : isLegacyReadOnlyMode ? updateCompatSchedule() : pb.collection('schedules').update(id, data)

export const deleteSchedule = (id: string) =>
  isFlaskIntegrationEnabled ? deleteFlaskSchedule(id) : isLegacyReadOnlyMode ? deleteCompatSchedule() : pb.collection('schedules').delete(id)

export const getDashboardStats = () =>
  isLegacyCompatEnabled
    ? getCompatDashboardStats()
    : Promise.reject(new Error('Use src/services/dashboard.ts para dashboard stats fora do modo legado.'))
