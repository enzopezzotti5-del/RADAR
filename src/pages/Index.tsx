import { useState } from 'react'
import {
  Activity,
  Calendar,
  CheckCircle,
  Clock,
  FileDown,
  History,
  TrendingUp,
  XCircle,
  Zap,
} from 'lucide-react'

import { usePolling } from '@/hooks/use-polling'
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { StatusBadge } from '@/components/StatusBadge'
import {
  dashboardApi,
  type DashboardData,
} from '@/lib/api'
import { formatDuration } from '@/lib/formatters'
import { cn } from '@/lib/utils'

export default function Dashboard() {
  const [data, setData] =
    useState<DashboardData | null>(null)

  const loadData = async () => {
    try {
      const dashboardData =
        await dashboardApi.get()

      setData(dashboardData)
    } catch (error) {
      console.error(
        'Falha ao carregar dashboard:',
        error,
      )
    }
  }

  usePolling(loadData, 5000)

  if (!data) {
    return (
      <div className="animate-pulse p-8 text-center text-muted-foreground">
        Carregando dashboard...
      </div>
    )
  }

  const safeNum = (
    value: unknown,
    fallback = 0,
  ) =>
    typeof value === 'number' &&
    Number.isFinite(value)
      ? value
      : fallback

  const metrics = [
    {
      title:
        'Executando agora',
      value:
        safeNum(
          data.running_now,
        ),
      icon: Activity,
      color:
        'text-blue-500',
    },
    {
      title:
        'Sucessos hoje',
      value:
        safeNum(
          data.success_today,
        ),
      icon: CheckCircle,
      color:
        'text-green-500',
    },
    {
      title:
        'Falhas hoje',
      value:
        safeNum(
          data.failed_today,
        ),
      icon: XCircle,
      color:
        'text-red-500',
    },
    {
      title:
        'Agendamentos',
      value:
        safeNum(
          data.scheduled_count,
        ),
      icon: Calendar,
      color:
        'text-purple-500',
    },
    {
      title:
        'Total historico',
      value:
        safeNum(
          data.history_total,
        ),
      icon: History,
      color:
        'text-cyan-500',
    },
    {
      title:
        'Taxa de sucesso',
      value:
        `${safeNum(
          data.success_rate,
          0,
        )}%`,
      icon: TrendingUp,
      color:
        'text-green-500',
    },
    {
      title:
        'Duracao media',
      value:
        formatDuration(
          safeNum(
            data.avg_duration_s,
            0,
          ),
        ),
      icon: Clock,
      color:
        'text-orange-500',
    },
    {
      title:
        'Ultima tarefa',
      value:
        data.last_task ||
        '-',
      icon: Zap,
      color:
        'text-yellow-500',
    },
  ]

  return (
    <div className="space-y-6 animate-fade-in">
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        {metrics.map(
          (metric) => (
            <Card
              key={
                metric.title
              }
              className="transition-shadow hover:shadow-md"
            >
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium text-muted-foreground">
                  {
                    metric.title
                  }
                </CardTitle>

                <metric.icon
                  className={cn(
                    'h-5 w-5',
                    metric.color,
                  )}
                />
              </CardHeader>

              <CardContent>
                <div className="truncate text-2xl font-bold">
                  {
                    metric.value
                  }
                </div>
              </CardContent>
            </Card>
          ),
        )}
      </div>

      {data.by_concessionaria &&
        data.by_concessionaria.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle className="text-lg">
                Resultado por concessionaria
              </CardTitle>
            </CardHeader>

            <CardContent>
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                {data.by_concessionaria
                  .slice(0, 12)
                  .map((item) => {
                    const lastRun =
                      item.last_run_at
                        ? new Date(
                            item.last_run_at,
                          )
                        : null
                    const validLastRun =
                      lastRun &&
                      !Number.isNaN(
                        lastRun.getTime(),
                      )

                    return (
                      <div
                        key={
                          item.task_id ||
                          item.task_name
                        }
                        className="rounded-xl border border-border bg-muted/20 p-4"
                      >
                        <div className="mb-3 flex items-start justify-between gap-3">
                          <div>
                            <p className="font-semibold text-foreground">
                              {
                                item.task_name
                              }
                            </p>
                            <p className="text-xs text-muted-foreground">
                              {item.task_id ||
                                '-'}
                            </p>
                          </div>

                          <FileDown className="h-4 w-4 text-blue-500" />
                        </div>

                        <div className="space-y-2 text-sm">
                          <div className="flex items-center justify-between">
                            <span className="text-muted-foreground">
                              Novas faturas
                            </span>
                            <span className="font-medium">
                              {
                                item.files_downloaded
                              }
                            </span>
                          </div>
                          <div className="flex items-center justify-between">
                            <span className="text-muted-foreground">
                              Execucoes concluidas
                            </span>
                            <span className="font-medium">
                              {
                                item.completed_runs
                              }
                            </span>
                          </div>
                          <div className="flex items-center justify-between">
                            <span className="text-muted-foreground">
                              Falhas
                            </span>
                            <span className="font-medium">
                              {
                                item.failed_runs
                              }
                            </span>
                          </div>
                          <div className="flex items-center justify-between">
                            <span className="text-muted-foreground">
                              Ja no indice
                            </span>
                            <span className="font-medium">
                              {item.skipped_existing ===
                              null
                                ? 'Em breve'
                                : item.skipped_existing}
                            </span>
                          </div>
                        </div>

                        <div className="mt-4 border-t border-border pt-3 text-xs text-muted-foreground">
                          {validLastRun
                            ? `Ultima execucao: ${lastRun.toLocaleString()}`
                            : 'Sem execucao recente'}
                        </div>
                      </div>
                    )
                  })}
              </div>
            </CardContent>
          </Card>
        )}

      {data.recent_runs &&
        data.recent_runs.length >
          0 && (
          <Card>
            <CardHeader>
              <CardTitle className="text-lg">
                Execucoes Recentes
              </CardTitle>
            </CardHeader>

            <CardContent>
              <div className="space-y-3">
                {data.recent_runs
                  .slice(0, 8)
                  .map(
                    (run) => {
                      const startedAt =
                        run.started_at
                          ? new Date(
                              run.started_at,
                            )
                          : null

                      const validDate =
                        startedAt &&
                        !Number.isNaN(
                          startedAt.getTime(),
                        )

                      return (
                        <div
                          key={
                            run.id
                          }
                          className="flex items-center justify-between border-b border-border pb-2 last:border-0"
                        >
                          <div>
                            <p className="font-medium text-foreground">
                              {run.task_name ||
                                run.task_id ||
                                'Tarefa desconhecida'}
                            </p>

                            <p className="mt-0.5 text-xs text-muted-foreground">
                              {validDate
                                ? startedAt.toLocaleString()
                                : 'Data indisponivel'}
                            </p>
                          </div>

                          <div className="flex items-center gap-3">
                            <span className="text-sm text-muted-foreground">
                              {typeof run.duration_s ===
                              'number'
                                ? formatDuration(run.duration_s)
                                : '-'}
                            </span>

                            <StatusBadge
                              status={
                                run.status
                              }
                            />
                          </div>
                        </div>
                      )
                    },
                  )}
              </div>
            </CardContent>
          </Card>
        )}
    </div>
  )
}
