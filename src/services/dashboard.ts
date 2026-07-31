import pb from '@/lib/pocketbase/client'
import type { Execution } from './api'
import { getCompatDashboardStats, getCompatExecutions, isLegacyCompatEnabled } from './legacyCompat'

export interface DashboardStats {
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

export async function getDashboardStats(): Promise<DashboardStats> {
  if (isLegacyCompatEnabled) {
    return getCompatDashboardStats()
  }

  const fiveMinAgo = new Date(Date.now() - 5 * 60 * 1000).toISOString()
  const [awaiting, running, completed, failed, cancelled, activeWorkers, robots, activeRobots] =
    await Promise.all([
      pb.collection('executions').getList(1, 1, { filter: "status='aguardando'" }),
      pb.collection('executions').getList(1, 1, {
        filter:
          "status='preparando_ambiente' || status='atualizando_codigo' || status='instalando_dependencias' || status='executando'",
      }),
      pb.collection('executions').getList(1, 1, { filter: "status='concluido'" }),
      pb.collection('executions').getList(1, 1, { filter: "status='falhou'" }),
      pb.collection('executions').getList(1, 1, { filter: "status='cancelado'" }),
      pb.collection('workers').getList(1, 1, { filter: `last_heartbeat > "${fiveMinAgo}"` }),
      pb.collection('robots').getList(1, 1, {}),
      pb.collection('robots').getList(1, 1, { filter: 'active=true' }),
    ])

  const completedExecs = await pb.collection('executions').getFullList({
    filter: "status='concluido'",
  })

  const totalFiles = completedExecs.reduce(
    (sum: number, e: any) => sum + (e.files_downloaded || 0),
    0,
  )
  const totalSkippedExisting = completedExecs.reduce(
    (sum: number, e: any) => sum + (e.files_skipped_existing || 0),
    0,
  )
  const totalFailedItems = completedExecs.reduce(
    (sum: number, e: any) => sum + (e.files_failed || 0),
    0,
  )

  return {
    awaiting: awaiting.totalItems,
    running: running.totalItems,
    completed: completed.totalItems,
    failed: failed.totalItems,
    cancelled: cancelled.totalItems,
    totalFiles,
    totalSkippedExisting,
    totalFailedItems,
    activeWorkers: activeWorkers.totalItems,
    totalRobots: robots.totalItems,
    activeRobots: activeRobots.totalItems,
  }
}

export async function getRecentExecutions(limit = 8): Promise<Execution[]> {
  if (isLegacyCompatEnabled) {
    return getCompatExecutions(limit) as Promise<Execution[]>
  }

  const result = await pb.collection('executions').getList<Execution>(1, limit, {
    sort: '-created',
    expand: 'robot',
  })

  return result.items
}
