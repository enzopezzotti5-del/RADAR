import {
  cancelExecution,
  getExecution as getExecutionById,
  getExecutionLogs,
  getExecutions,
  getRobots,
  isLegacyCompatEnabled,
  startExecution,
  type Robot,
} from '@/services/api'
import { getDashboardStats, getRecentExecutions } from '@/services/dashboard'
import { getCompatCalendarSummary, getCompatRunLog, type CalendarMetricSummary } from '@/services/legacyCompat'

export function deriveCommand(mainFilePath: string, extraArgs: string[] = []): string {
  if (!mainFilePath) return '-'

  const modulePath = mainFilePath.replace(/\.py$/, '').replace(/\//g, '.')
  const args = extraArgs.length > 0 ? ` ${extraArgs.join(' ')}` : ''
  return `python -m ${modulePath}${args}`
}

export interface Task {
  task_id: string
  name: string
  category: string
  notes: string
  script: string
  supports_month_year: boolean
  supports_type: boolean
  default_type: string
  supports_stage_flags: boolean
  supports_pasta: boolean
  pasta_template: string
  download_condition_options: string[]
  extra_args: string[]
  robot_id: string
}

export interface LiveRun {
  run_id: string
  task_name: string
  task_id: string
  started_at: string
  status_text: string
  cancel_requested?: boolean
}

export interface StopRunResult {
  stopped?: boolean
  already_finished?: boolean
  error?: string
}

export interface HistoryRun {
  id: string
  task_name: string
  task_id: string
  started_at: string
  finished_at: string
  duration_s: number
  status: string
  files_downloaded: number
  files_skipped_existing: number
  files_failed: number
}

export interface StartRunBody {
  task_id: string
  month: string
  year: string
  selected_type: string
  stage_flag: string
  pasta: string
  download_condition: string
  extra_text: string
}

export interface DashboardData {
  running_now: number
  success_today: number
  failed_today: number
  scheduled_count: number
  history_total: number
  success_rate: number
  avg_duration_s: number
  last_task: string
  recent_runs: Array<{
    id: string
    task_name: string
    task_id: string
    started_at: string
    duration_s: number
    status: string
  }>
  by_concessionaria: Array<{
    task_name: string
    task_id: string
    files_downloaded: number
    completed_runs: number
    failed_runs: number
    files_failed: number
    skipped_existing: number | null
    last_run_at: string
  }>
}

export { isLegacyCompatEnabled }

const RUNNING_STATUSES = [
  'aguardando',
  'preparando_ambiente',
  'atualizando_codigo',
  'instalando_dependencias',
  'executando',
  'parando',
]

function mapRobotToTask(robot: Robot): Task {
  return {
    task_id: robot.task_id || robot.id,
    name: robot.name,
    category: robot.category || 'Downloaders',
    notes: robot.description || '',
    script: robot.main_file_path || '',
    supports_month_year: Boolean(robot.supports_month_year),
    supports_type: Boolean(robot.supports_type),
    default_type: robot.default_type || 'ambos',
    supports_stage_flags: Boolean(robot.supports_stage_flags),
    supports_pasta: Boolean(robot.supports_pasta),
    pasta_template: robot.pasta_template || '',
    download_condition_options: robot.download_condition_options || [],
    extra_args: robot.extra_args || [],
    robot_id: robot.id,
  }
}

function dedupeTasks(tasks: Task[]): Task[] {
  const unique = new Map<string, Task>()

  for (const task of tasks) {
    const primaryKey = task.task_id?.trim()
    const fallbackKey = `${task.name.trim().toLowerCase()}::${task.category.trim().toLowerCase()}`
    const key = primaryKey || fallbackKey
    const current = unique.get(key)

    if (!current || task.robot_id < current.robot_id) {
      unique.set(key, task)
    }
  }

  return Array.from(unique.values()).sort((first, second) => first.name.localeCompare(second.name))
}

export const tasksApi = {
  async list(): Promise<{ tasks: Record<string, Task[]> }> {
    const robots = await getRobots()
    const grouped: Record<string, Task[]> = {}

    for (const robot of robots) {
      const task = mapRobotToTask(robot)
      const category = task.category || 'Downloaders'
      if (!grouped[category]) grouped[category] = []
      grouped[category].push(task)
    }

    Object.keys(grouped).forEach((category) => {
      grouped[category] = dedupeTasks(grouped[category])
    })

    return { tasks: grouped }
  },
}

export const calendarApi = {
  summary(start: string, end: string, utility?: string): Promise<CalendarMetricSummary> {
    return getCompatCalendarSummary(start, end, utility)
  },
}

export const dashboardApi = {
  async get(): Promise<DashboardData> {
    const [stats, recent, history] = await Promise.all([
      getDashboardStats(),
      getRecentExecutions(8),
      runsApi.history(200),
    ])

    const total = stats.completed + stats.failed + stats.cancelled
    const grouped = new Map<
      string,
      {
        task_name: string
        task_id: string
        files_downloaded: number
        completed_runs: number
        failed_runs: number
        files_failed: number
        skipped_existing: number | null
        last_run_at: string
      }
    >()

    for (const run of history.runs) {
      const key = run.task_id || run.task_name || run.id
      const current = grouped.get(key) || {
        task_name: run.task_name || run.task_id || 'Tarefa desconhecida',
        task_id: run.task_id,
        files_downloaded: 0,
        completed_runs: 0,
        failed_runs: 0,
        files_failed: 0,
        skipped_existing: 0,
        last_run_at: run.started_at,
      }

      if (run.status === 'concluido') current.completed_runs += 1
      if (run.status === 'falhou') current.failed_runs += 1

      current.last_run_at =
        new Date(run.started_at).getTime() > new Date(current.last_run_at).getTime()
          ? run.started_at
          : current.last_run_at
      current.files_downloaded += run.files_downloaded || 0
      current.skipped_existing = (current.skipped_existing || 0) + (run.files_skipped_existing || 0)
      current.files_failed += run.files_failed || 0

      grouped.set(key, current)
    }

    return {
      running_now: stats.running + stats.awaiting,
      success_today: stats.completed,
      failed_today: stats.failed,
      scheduled_count: 0,
      history_total: total,
      success_rate: total > 0 ? Math.round((stats.completed / total) * 100) : 0,
      avg_duration_s: 0,
      last_task: recent[0]?.expand?.robot?.name || '-',
      recent_runs: recent.map((execution) => ({
        id: execution.id,
        task_name: execution.expand?.robot?.name || '',
        task_id: execution.expand?.robot?.task_id || execution.robot,
        started_at: execution.started_at,
        duration_s: execution.duration || 0,
        status: execution.status,
      })),
      by_concessionaria: Array.from(grouped.values()).sort((first, second) => {
        if (second.files_downloaded !== first.files_downloaded) {
          return second.files_downloaded - first.files_downloaded
        }
        return first.task_name.localeCompare(second.task_name)
      }),
    }
  },
}

export const runsApi = {
  async live(): Promise<{ runs: LiveRun[] }> {
    const result = await getExecutions('', 1, 50)
    const executions = result.items

    return {
      runs: executions
        .filter((execution) => RUNNING_STATUSES.includes(execution.status))
        .map((execution) => ({
          run_id: execution.id,
          task_name: execution.expand?.robot?.name || '',
          task_id: execution.expand?.robot?.task_id || execution.robot,
          started_at: execution.started_at,
          status_text: execution.cancel_requested ? 'parando' : execution.status,
          cancel_requested: execution.cancel_requested,
        })),
    }
  },

  async history(perPage = 50): Promise<{ runs: HistoryRun[] }> {
    const result = await getExecutions('', 1, perPage)
    const executions = result.items

    return {
      runs: executions
        .filter((execution) => !RUNNING_STATUSES.includes(execution.status))
        .map((execution) => ({
          id: execution.id,
          task_name: execution.expand?.robot?.name || '',
          task_id: execution.expand?.robot?.task_id || execution.robot,
          started_at: execution.started_at,
          finished_at: execution.finished_at || '',
          duration_s: execution.duration || 0,
          status: execution.status,
          files_downloaded: execution.files_downloaded || 0,
          files_skipped_existing: execution.files_skipped_existing || 0,
          files_failed: execution.files_failed || 0,
        })),
    }
  },

  async start(body: StartRunBody): Promise<unknown> {
    const taskMap = await tasksApi.list()
    const allTasks = Object.values(taskMap.tasks).flat()
    const selectedTask = allTasks.find((task) => task.task_id === body.task_id)

    if (!selectedTask) {
      throw new Error('Tarefa nao encontrada no catalogo.')
    }

    const params: Record<string, unknown> = {}
    if (body.month) params.month = body.month
    if (body.year) params.year = body.year
    if (body.selected_type) params.selected_type = body.selected_type
    if (body.stage_flag) params.stage_flag = body.stage_flag
    if (body.pasta) params.pasta = body.pasta
    if (body.download_condition) params.download_condition = body.download_condition
    if (body.extra_text) params.extra_text = body.extra_text

    const executionTarget = isLegacyCompatEnabled
      ? selectedTask.task_id || body.task_id
      : selectedTask.robot_id

    return startExecution(executionTarget, params)
  },

  async stop(runId: string): Promise<StopRunResult> {
    return cancelExecution(runId) as Promise<StopRunResult>
  },

  async rerun(runId: string): Promise<unknown> {
    const original = await getExecutionById(runId)
    return startExecution(original.robot, (original.parameters as Record<string, unknown>) || {})
  },

  async logs(
    runId: string,
    nextLine: number,
  ): Promise<{
    log: string
    next_line: number
    status_text: string
    is_running: boolean
    is_live: boolean
  }> {
    if (isLegacyCompatEnabled) {
      return getCompatRunLog(runId, nextLine)
    }

    const [logs, execution] = await Promise.all([getExecutionLogs(runId), getExecutionById(runId)])
    const newLogs = logs.filter((log) => (log.line_number || 0) >= nextLine)
    const logText = newLogs.map((log) => log.message).join('\n')
    const maxLine = logs.length > 0 ? Math.max(...logs.map((log) => log.line_number || 0)) + 1 : 0
    const isRunning = RUNNING_STATUSES.includes(execution.status)

    return {
      log: logText,
      next_line: maxLine,
      status_text: execution.status,
      is_running: isRunning,
      is_live: isRunning,
    }
  },
}
