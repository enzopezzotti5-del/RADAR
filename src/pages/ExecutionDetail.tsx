import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertCircle, Clock, Copy, Download, Search, Terminal } from 'lucide-react'
import { useParams } from 'react-router-dom'

import { StatusBadge } from '@/components/StatusBadge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useRealtime } from '@/hooks/use-realtime'
import { useToast } from '@/hooks/use-toast'
import { formatDuration } from '@/lib/formatters'
import {
  getExecution,
  getExecutionLogs,
  isLegacyCompatEnabled,
  type Execution,
  type ExecutionLog,
  type Robot,
} from '@/services/api'

type ExecutionRecord = Execution & {
  expand?: {
    robot?: Robot
  }
}

type RealtimeExecutionLog = ExecutionLog

export default function ExecutionDetail() {
  const { id } = useParams()
  const [execution, setExecution] = useState<ExecutionRecord | null>(null)
  const [logs, setLogs] = useState<RealtimeExecutionLog[]>([])
  const [search, setSearch] = useState('')
  const [levelFilter, setLevelFilter] = useState('ALL')
  const [loading, setLoading] = useState(true)
  const bottomRef = useRef<HTMLDivElement>(null)
  const { toast } = useToast()

  const loadData = useCallback(async () => {
    if (!id) {
      setLoading(false)
      return
    }

    try {
      const [exec, logsData] = await Promise.all([getExecution(id), getExecutionLogs(id)])
      setExecution(exec as ExecutionRecord)
      setLogs(logsData as RealtimeExecutionLog[])
    } catch (error) {
      console.error('Falha ao carregar execucao:', error)
      toast({
        title: 'Erro',
        description: 'Nao foi possivel carregar os detalhes da execucao.',
        variant: 'destructive',
      })
    } finally {
      setLoading(false)
    }
  }, [id, toast])

  useEffect(() => {
    void loadData()
  }, [loadData])

  useRealtime<ExecutionRecord>(
    'executions',
    (event) => {
      if (event.record.id !== id) {
        return
      }

      setExecution((current) => ({
        ...(current ?? {}),
        ...event.record,
        expand: current?.expand,
      }))
    },
    !isLegacyCompatEnabled,
  )

  useRealtime<RealtimeExecutionLog>(
    'execution_logs',
    (event) => {
      if (event.record.execution !== id) {
        return
      }

      setLogs((currentLogs) => {
        if (event.action === 'create') {
          const newLog = event.record
          const alreadyExists = currentLogs.some((log) => log.id === newLog.id)

          if (alreadyExists) {
            return currentLogs
          }

          return [...currentLogs, newLog].sort((first, second) => {
            const firstLine = first.line_number ?? 0
            const secondLine = second.line_number ?? 0

            if (firstLine !== secondLine) {
              return firstLine - secondLine
            }

            const firstDate = new Date(first.created ?? first.timestamp ?? 0).getTime()
            const secondDate = new Date(second.created ?? second.timestamp ?? 0).getTime()

            return firstDate - secondDate
          })
        }

        if (event.action === 'update') {
          return currentLogs.map((log) =>
            log.id === event.record.id
              ? {
                  ...log,
                  ...event.record,
                }
              : log,
          )
        }

        if (event.action === 'delete') {
          return currentLogs.filter((log) => log.id !== event.record.id)
        }

        return currentLogs
      })
    },
    !isLegacyCompatEnabled,
  )

  useEffect(() => {
    bottomRef.current?.scrollIntoView({
      behavior: 'smooth',
    })
  }, [logs])

  const getLogTimestamp = (log: RealtimeExecutionLog) => {
    const rawDate = log.timestamp ?? log.created

    if (!rawDate) {
      return 'Horario indisponivel'
    }

    const date = new Date(rawDate)

    if (Number.isNaN(date.getTime())) {
      return 'Horario invalido'
    }

    return date.toLocaleTimeString()
  }

  const getLogText = () =>
    logs
      .map((log) => {
        const rawDate = log.timestamp ?? log.created
        const date = rawDate ? new Date(rawDate) : null
        const timestamp = date && !Number.isNaN(date.getTime()) ? date.toISOString() : 'sem-data'

        return `[${timestamp}] [${log.level}] ${log.message}`
      })
      .join('\n')

  const copyLogs = async () => {
    try {
      await navigator.clipboard.writeText(getLogText())
      toast({
        title: 'Logs copiados',
        description: 'Os logs foram copiados para a area de transferencia.',
      })
    } catch (error) {
      console.error('Falha ao copiar logs:', error)
      toast({
        title: 'Erro',
        description: 'Nao foi possivel copiar os logs.',
        variant: 'destructive',
      })
    }
  }

  const downloadLogs = () => {
    const blob = new Blob([getLogText()], {
      type: 'text/plain;charset=utf-8',
    })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')

    anchor.href = url
    anchor.download = `logs-${execution?.id ?? 'execucao'}.txt`
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    URL.revokeObjectURL(url)
  }

  const normalizedSearch = search.trim().toLowerCase()
  const filteredLogs = logs.filter((log) => {
    const message = String(log.message ?? '').toLowerCase()
    const level = String(log.level ?? '').toUpperCase()
    const matchesSearch =
      !normalizedSearch ||
      message.includes(normalizedSearch) ||
      level.toLowerCase().includes(normalizedSearch)
    const matchesLevel = levelFilter === 'ALL' || level === levelFilter

    return matchesSearch && matchesLevel
  })

  const getLevelClassName = (level: string) => {
    switch (level.toUpperCase()) {
      case 'ERROR':
        return 'text-[#f48771]'
      case 'WARNING':
      case 'WARN':
        return 'text-[#cca700]'
      case 'SUCCESS':
        return 'text-[#89d185]'
      case 'DEBUG':
        return 'text-[#c586c0]'
      default:
        return 'text-[#569cd6]'
    }
  }

  if (loading) {
    return <div className="animate-pulse p-8 text-center text-muted-foreground">Carregando detalhes...</div>
  }

  if (!execution) {
    return <div className="p-8 text-center text-muted-foreground">Execucao nao encontrada.</div>
  }

  const startedAt = execution.started_at ? new Date(execution.started_at) : null
  const validStartedAt = startedAt && !Number.isNaN(startedAt.getTime())

  return (
    <div className="space-y-6 animate-fade-in">
      <div className="flex flex-col gap-4 border-b border-border pb-6 md:flex-row md:items-center md:justify-between">
        <div>
          <h2 className="flex items-center gap-3 text-2xl font-bold">
            {execution.expand?.robot?.name ?? execution.robot ?? 'Desconhecido'}
            <StatusBadge status={execution.status} />
          </h2>
          <p className="mt-1 font-mono text-sm text-muted-foreground">ID: {execution.id}</p>
        </div>

        <div className="flex gap-6 rounded-lg border border-border bg-muted/30 p-3 text-sm">
          <div>
            <p className="flex items-center gap-1 text-muted-foreground">
              <Clock className="h-3.5 w-3.5" />
              Inicio
            </p>
            <p className="font-medium">{validStartedAt ? startedAt.toLocaleString() : 'Nao iniciado'}</p>
          </div>

          <div>
            <p className="flex items-center gap-1 text-muted-foreground">
              <Terminal className="h-3.5 w-3.5" />
              Duracao
            </p>
            <p className="font-medium">
              {typeof execution.duration === 'number' && execution.duration > 0
                ? formatDuration(execution.duration)
                : 'Em andamento'}
            </p>
          </div>

          <div>
            <p className="flex items-center gap-1 text-muted-foreground">
              <Download className="h-3.5 w-3.5" />
              Downloads
            </p>
            <p className="font-medium">{execution.files_downloaded ?? 0} arquivos</p>
          </div>
        </div>
      </div>

      <Tabs defaultValue="logs" className="w-full">
        <TabsList className="bg-muted">
          <TabsTrigger value="logs" className="flex items-center gap-2">
            <Terminal className="h-4 w-4" />
            Visualizador de Logs
          </TabsTrigger>

          <TabsTrigger value="errors" className="flex items-center gap-2">
            <AlertCircle className="h-4 w-4" />
            Erros & Tracebacks
          </TabsTrigger>
        </TabsList>

        <TabsContent value="logs" className="mt-4">
          <Card className="flex h-[70vh] min-h-[500px] flex-col overflow-hidden border-gray-800 bg-[#1e1e1e] font-mono text-sm text-gray-300">
            <div className="flex flex-col gap-2 border-b border-gray-800 bg-[#252526] p-2 lg:flex-row lg:items-center lg:justify-between">
              <div className="flex flex-col gap-2 md:flex-row md:items-center">
                <div className="relative w-full md:w-56">
                  <Search className="absolute left-2 top-2 h-4 w-4 text-gray-500" />
                  <Input
                    className="h-8 border-gray-700 bg-[#333333] pl-8 text-gray-200 focus-visible:ring-1 focus-visible:ring-blue-500"
                    placeholder="Filtrar logs..."
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                  />
                </div>

                <div className="flex flex-wrap items-center gap-1">
                  {['ALL', 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'SUCCESS'].map((level) => (
                    <button
                      key={level}
                      type="button"
                      onClick={() => setLevelFilter(level)}
                      className={`rounded px-2 py-1 text-xs transition-colors ${
                        levelFilter === level
                          ? 'bg-blue-600 text-white'
                          : 'text-gray-400 hover:bg-gray-700 hover:text-gray-100'
                      }`}
                    >
                      {level === 'ALL' ? 'Tudo' : level}
                    </button>
                  ))}
                </div>
              </div>

              <div className="flex items-center gap-1">
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-8 text-gray-400 hover:bg-gray-700 hover:text-gray-100"
                  onClick={downloadLogs}
                  disabled={logs.length === 0}
                >
                  <Download className="mr-2 h-4 w-4" />
                  Baixar
                </Button>

                <Button
                  variant="ghost"
                  size="sm"
                  className="h-8 text-gray-400 hover:bg-gray-700 hover:text-gray-100"
                  onClick={() => {
                    void copyLogs()
                  }}
                  disabled={logs.length === 0}
                >
                  <Copy className="mr-2 h-4 w-4" />
                  Copiar Tudo
                </Button>
              </div>
            </div>

            <ScrollArea className="flex-1 bg-[#1e1e1e] p-4">
              <div className="space-y-1">
                {filteredLogs.map((log, index) => {
                  const level = String(log.level ?? 'INFO').toUpperCase()

                  return (
                    <div
                      key={log.id ?? `${log.line_number ?? index}-${index}`}
                      className="flex gap-4 rounded px-2 py-0.5 leading-relaxed hover:bg-[#2d2d2d]"
                    >
                      <span className="shrink-0 select-none text-[#858585]">[{getLogTimestamp(log)}]</span>
                      <span
                        className={`w-20 shrink-0 select-none font-semibold ${getLevelClassName(level)}`}
                      >
                        {level}
                      </span>
                      <span
                        className={`whitespace-pre-wrap ${
                          level === 'ERROR' ? 'text-[#f48771]' : 'text-[#cccccc]'
                        }`}
                      >
                        {log.message}
                      </span>
                    </div>
                  )
                })}

                {filteredLogs.length === 0 && (
                  <div className="p-2 italic text-[#858585]">Nenhum log encontrado...</div>
                )}

                <div ref={bottomRef} />
              </div>
            </ScrollArea>
          </Card>
        </TabsContent>

        <TabsContent value="errors" className="mt-4">
          <Card>
            <CardContent className="space-y-4 pt-6">
              {execution.error_message ? (
                <div className="whitespace-pre-wrap rounded-md border border-red-500/20 bg-red-500/10 p-4 font-mono text-sm text-red-400">
                  {execution.error_message}
                </div>
              ) : (
                <div className="rounded-lg border-2 border-dashed p-8 text-center text-muted-foreground">
                  Nenhum erro reportado nesta execucao.
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  )
}
