import { useState } from 'react'
import { Link } from 'react-router-dom'
import { FileText, Play } from 'lucide-react'

import { LogPanel } from '@/components/LogPanel'
import { StartExecutionDialog } from '@/components/StartExecutionDialog'
import { StatusBadge } from '@/components/StatusBadge'
import { Button } from '@/components/ui/button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { usePolling } from '@/hooks/use-polling'
import { runsApi, type HistoryRun, type LiveRun } from '@/lib/api'
import { isLegacyReadOnlyMode } from '@/services/api'
import { formatDuration } from '@/lib/formatters'

export default function Executions() {
  const [liveRuns, setLiveRuns] = useState<LiveRun[]>([])
  const [historyRuns, setHistoryRuns] = useState<HistoryRun[]>([])
  const [logRunId, setLogRunId] = useState<string | null>(null)
  const [logPanelOpen, setLogPanelOpen] = useState(false)
  const [startDialogOpen, setStartDialogOpen] = useState(false)

  const loadLive = async () => {
    try {
      const data = await runsApi.live()
      setLiveRuns(data.runs || [])
    } catch {
      /* intentionally ignored */
    }
  }

  const loadHistory = async () => {
    try {
      const data = await runsApi.history()
      setHistoryRuns(data.runs || [])
    } catch {
      /* intentionally ignored */
    }
  }

  usePolling(loadLive, 3000)
  usePolling(loadHistory, 3000)

  const openLogs = (runId: string) => {
    setLogRunId(runId)
    setLogPanelOpen(true)
  }

  return (
    <div className="space-y-6 animate-fade-in">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold">Monitoramento do Radar</h2>
          <p className="text-muted-foreground">
            Acompanhe a fila, as execucoes em andamento e o historico das tarefas.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {isLegacyReadOnlyMode && <span className="text-sm text-muted-foreground">Modo somente leitura</span>}
          {!isLegacyReadOnlyMode && (
            <Button onClick={() => setStartDialogOpen(true)}>
              <Play className="mr-2 h-4 w-4" /> Nova Execucao
            </Button>
          )}
        </div>
      </div>

      <div className="space-y-3">
        <h3 className="text-lg font-semibold">Em andamento</h3>
        <div className="overflow-x-auto rounded-lg border border-border bg-card">
          <Table>
            <TableHeader className="bg-muted/50">
              <TableRow>
                <TableHead className="w-[80px]">ID</TableHead>
                <TableHead>Tarefa</TableHead>
                <TableHead>Inicio</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Acoes</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {liveRuns.map((run) => (
                <TableRow key={run.run_id} className="hover:bg-muted/30">
                  <TableCell className="font-mono text-xs text-muted-foreground">{run.run_id}</TableCell>
                  <TableCell className="font-medium">{run.task_name || run.task_id}</TableCell>
                  <TableCell className="text-sm">{new Date(run.started_at).toLocaleString()}</TableCell>
                  <TableCell>
                    <StatusBadge status={run.status_text} />
                  </TableCell>
                  <TableCell className="space-x-1 text-right">
                    <Button variant="outline" size="icon" title="Ver logs" onClick={() => openLogs(run.run_id)}>
                      <FileText className="h-4 w-4 text-blue-500" />
                    </Button>
                  </TableCell>
                </TableRow>
              ))}

              {liveRuns.length === 0 && (
                <TableRow>
                  <TableCell colSpan={5} className="py-8 text-center text-muted-foreground">
                    Nenhuma execucao em andamento.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      </div>

      <div className="space-y-3">
        <h3 className="text-lg font-semibold">Historico recente</h3>
        <div className="overflow-x-auto rounded-lg border border-border bg-card">
          <Table>
            <TableHeader className="bg-muted/50">
              <TableRow>
                <TableHead className="w-[80px]">ID</TableHead>
                <TableHead>Tarefa</TableHead>
                <TableHead>Inicio</TableHead>
                <TableHead>Fim</TableHead>
                <TableHead>Duracao</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Acoes</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {historyRuns.map((run) => (
                <TableRow key={run.id} className="hover:bg-muted/30">
                  <TableCell className="font-mono text-xs text-muted-foreground">{run.id}</TableCell>
                  <TableCell className="font-medium">{run.task_name || run.task_id}</TableCell>
                  <TableCell className="text-sm">{new Date(run.started_at).toLocaleString()}</TableCell>
                  <TableCell className="text-sm">
                    {run.finished_at ? new Date(run.finished_at).toLocaleString() : '-'}
                  </TableCell>
                  <TableCell className="text-sm">{formatDuration(run.duration_s)}</TableCell>
                  <TableCell>
                    <StatusBadge status={run.status} />
                  </TableCell>
                  <TableCell className="space-x-1 text-right">
                    <Button variant="outline" size="icon" title="Ver logs" onClick={() => openLogs(run.id)}>
                      <FileText className="h-4 w-4 text-blue-500" />
                    </Button>
                    <Button variant="outline" size="icon" asChild title="Abrir detalhes">
                      <Link to={`/executions/${run.id}`}>
                        <FileText className="h-4 w-4 text-slate-700" />
                      </Link>
                    </Button>
                  </TableCell>
                </TableRow>
              ))}

              {historyRuns.length === 0 && (
                <TableRow>
                  <TableCell colSpan={7} className="py-8 text-center text-muted-foreground">
                    Nenhuma execucao no historico.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      </div>

      <LogPanel runId={logRunId} open={logPanelOpen} onOpenChange={setLogPanelOpen} />

      <StartExecutionDialog
        open={startDialogOpen}
        onOpenChange={setStartDialogOpen}
        onStarted={() => {
          void loadLive()
          void loadHistory()
        }}
      />
    </div>
  )
}
