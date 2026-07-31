import { useState } from 'react'
import { FileText } from 'lucide-react'

import { LogPanel } from '@/components/LogPanel'
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
import { runsApi, type HistoryRun } from '@/lib/api'
import { formatDuration } from '@/lib/formatters'

export default function History() {
  const [executions, setExecutions] = useState<HistoryRun[]>([])
  const [logRunId, setLogRunId] = useState<string | null>(null)
  const [logPanelOpen, setLogPanelOpen] = useState(false)

  const loadData = async () => {
    try {
      const data = await runsApi.history(200)
      setExecutions(data.runs || [])
    } catch {
      /* intentionally ignored */
    }
  }

  usePolling(loadData, 5000)

  const openLogs = (runId: string) => {
    setLogRunId(runId)
    setLogPanelOpen(true)
  }

  return (
    <div className="space-y-4 animate-fade-in">
      <div className="mb-6">
        <h2 className="text-2xl font-bold">Historico do Radar</h2>
        <p className="text-muted-foreground">Registro consolidado das tarefas finalizadas no catalogo.</p>
      </div>

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
            {executions.map((execution) => (
              <TableRow key={execution.id} className="hover:bg-muted/30">
                <TableCell className="font-mono text-xs text-muted-foreground">{execution.id}</TableCell>
                <TableCell className="font-medium">{execution.task_name || execution.task_id}</TableCell>
                <TableCell className="text-sm">{new Date(execution.started_at).toLocaleString()}</TableCell>
                <TableCell className="text-sm">
                  {execution.finished_at ? new Date(execution.finished_at).toLocaleString() : '-'}
                </TableCell>
                <TableCell className="text-sm">{formatDuration(execution.duration_s)}</TableCell>
                <TableCell>
                  <StatusBadge status={execution.status} />
                </TableCell>
                <TableCell className="text-right">
                  <Button variant="outline" size="icon" title="Ver logs" onClick={() => openLogs(execution.id)}>
                    <FileText className="h-4 w-4 text-blue-500" />
                  </Button>
                </TableCell>
              </TableRow>
            ))}

            {executions.length === 0 && (
              <TableRow>
                <TableCell colSpan={7} className="py-12 text-center text-muted-foreground">
                  Nenhuma execucao encontrada.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      <LogPanel runId={logRunId} open={logPanelOpen} onOpenChange={setLogPanelOpen} />
    </div>
  )
}
