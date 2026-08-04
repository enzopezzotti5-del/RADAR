import { useCallback, useEffect, useMemo, useState } from 'react'
import { ChevronDown, ChevronRight, Pencil, Play, RefreshCcw, Trash2 } from 'lucide-react'

import { RobotFormDialog } from '@/components/RobotFormDialog'
import { StartExecutionDialog } from '@/components/StartExecutionDialog'
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { useToast } from '@/hooks/use-toast'
import { deriveCommand } from '@/lib/api'
import { legacyCatalogTasks } from '@/lib/legacyCatalog'
import {
  createRobot,
  deleteRobot,
  getRobots,
  isLegacyHomologationMode,
  isLegacyReadOnlyMode,
  updateRobot,
  type Robot,
} from '@/services/api'

const CATEGORIES = ['Downloaders', 'Pipelines'] as const
const DEFAULT_REPOSITORY_URL = 'https://github.com/acaoengenhariaeinstalacoes/energia-automacao.git'

function resolveTaskCategory(robot: Robot): string {
  if (robot.category === 'Pipelines' || robot.category === 'Downloaders') {
    return robot.category
  }

  if ((robot.main_file_path || '').includes('core/pipelines/')) {
    return 'Pipelines'
  }

  return 'Downloaders'
}

export default function Downloaders() {
  const [tasks, setTasks] = useState<Robot[]>([])
  const [loading, setLoading] = useState(true)
  const [syncing, setSyncing] = useState(false)
  const [startDialogOpen, setStartDialogOpen] = useState(false)
  const [startTaskId, setStartTaskId] = useState<string | undefined>(undefined)
  const [collapsedSections, setCollapsedSections] = useState<Record<string, boolean>>({
    Downloaders: false,
    Pipelines: false,
  })
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingRobot, setEditingRobot] = useState<Robot | null>(null)
  const [robotToDelete, setRobotToDelete] = useState<Robot | null>(null)
  const [deleting, setDeleting] = useState(false)
  const { toast } = useToast()

  const loadData = useCallback(async () => {
    try {
      const robots = await getRobots()
      setTasks(robots)
    } catch {
      /* intentionally ignored */
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadData()
  }, [loadData])

  const tasksByCategory = useMemo(() => {
    const grouped = tasks.reduce<Record<string, Robot[]>>((acc, robot) => {
      const category = resolveTaskCategory(robot)
      if (!acc[category]) {
        acc[category] = []
      }
      acc[category].push(robot)
      return acc
    }, {})

    Object.keys(grouped).forEach((category) => {
      grouped[category].sort((first, second) => first.name.localeCompare(second.name))
    })

    return grouped
  }, [tasks])

  const handleRun = (robot: Robot) => {
    setStartTaskId(robot.task_id || undefined)
    setStartDialogOpen(true)
  }

  const handleEdit = (robot: Robot) => {
    if (isLegacyReadOnlyMode) {
      toast({
        title: 'Catalogo do backend Flask',
        description: 'As tarefas sao carregadas diretamente do backend Flask.',
      })
      return
    }

    setEditingRobot(robot)
    setDialogOpen(true)
  }

  const handleDelete = async () => {
    if (!robotToDelete) {
      return
    }

    setDeleting(true)

    try {
      await deleteRobot(robotToDelete.id)
      toast({
        title: 'Tarefa excluida',
        description: `${robotToDelete.name} foi removida do catalogo.`,
      })
      setRobotToDelete(null)
      await loadData()
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Nao foi possivel excluir a tarefa.'
      toast({
        title: 'Erro',
        description: message,
        variant: 'destructive',
      })
    } finally {
      setDeleting(false)
    }
  }

  const toggleSection = (category: string) => {
    setCollapsedSections((current) => ({
      ...current,
      [category]: !current[category],
    }))
  }

  const handleSyncCatalog = async () => {
    if (isLegacyReadOnlyMode) {
      toast({
        title: 'Modo legado ativo',
        description: 'O espelhamento manual do catalogo fica desabilitado quando o painel consome apenas leitura do backend legado.',
      })
      return
    }

    setSyncing(true)

    try {
      const existingRobots = await getRobots()
      const existingByTaskId = new Map(
        existingRobots
          .filter((robot) => robot.task_id)
          .map((robot) => [robot.task_id as string, robot]),
      )
      const existingByScript = new Map(
        existingRobots
          .filter((robot) => robot.main_file_path)
          .map((robot) => [robot.main_file_path as string, robot]),
      )
      const existingByName = new Map(
        existingRobots.map((robot) => [robot.name.trim().toLowerCase(), robot]),
      )

      for (const task of legacyCatalogTasks) {
        const existing =
          existingByTaskId.get(task.task_id) ||
          existingByScript.get(task.script) ||
          existingByName.get(task.name.trim().toLowerCase())
        const payload = {
          name: task.name,
          task_id: task.task_id,
          category: task.category,
          type: 'energia',
          description: task.notes || '',
          repository: existing?.repository || DEFAULT_REPOSITORY_URL,
          branch: existing?.branch || 'main',
          main_file_path: task.script,
          execution_command: existing?.execution_command || deriveCommand(task.script, task.extra_args || []),
          dependencies_path: existing?.dependencies_path || 'requirements.txt',
          pasta_template: task.pasta_template || '',
          supports_month_year: Boolean(task.supports_month_year),
          supports_type: Boolean(task.supports_type),
          default_type: task.default_type || 'ambos',
          supports_stage_flags: Boolean(task.supports_stage_flags),
          supports_pasta: Boolean(task.supports_pasta),
          download_condition_options: task.download_condition_options || [],
          extra_args: task.extra_args || [],
          active: existing?.active ?? true,
          timeout_minutes: existing?.timeout_minutes || 120,
          status: existing?.status || 'offline',
        }

        if (existing) {
          await updateRobot(existing.id, payload)
        } else {
          await createRobot(payload)
        }
      }

      toast({
        title: 'Catalogo sincronizado',
        description: 'Downloaders e pipelines foram espelhados a partir do catalogo legado.',
      })
      await loadData()
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Nao foi possivel sincronizar o catalogo legado.'
      toast({
        title: 'Erro',
        description: message,
        variant: 'destructive',
      })
    } finally {
      setSyncing(false)
    }
  }

  return (
    <div className="animate-fade-in">
      <div className="mb-6 flex items-center justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold">Catalogo do Radar</h2>
          <p className="text-muted-foreground">
            {isLegacyReadOnlyMode
              ? 'Consulta ao catalogo do Radar atual durante a transicao.'
              : isLegacyHomologationMode
                ? 'Homologacao local do catalogo novo com espelhamento a partir do legado.'
                : 'Espelhamento do catalogo legado com task_id real, script relativo e pasta_template.'}
          </p>
        </div>
        <Button onClick={() => void handleSyncCatalog()} disabled={syncing || isLegacyReadOnlyMode}>
          <RefreshCcw className="mr-2 h-4 w-4" /> {syncing ? 'Sincronizando...' : 'Espelhar catalogo legado'}
        </Button>
      </div>

      {isLegacyReadOnlyMode && (
        <div className="mb-6 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          Este catalogo e fornecido diretamente pelo backend Flask.
        </div>
      )}

      {isLegacyHomologationMode && (
        <div className="mb-6 rounded-lg border border-emerald-300 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
          Modo de homologacao ativo. O catalogo abaixo usa a base nova local, sem alterar o Radar atual.
        </div>
      )}

      {loading ? (
        <div className="animate-pulse py-12 text-center text-muted-foreground">
          Carregando tarefas...
        </div>
      ) : (
        <div className="space-y-8">
          {CATEGORIES.map((category) => (
            <div key={category} className="space-y-3">
              <div className="flex items-end justify-between">
                <button
                  type="button"
                  onClick={() => toggleSection(category)}
                  className="flex items-start gap-3 text-left"
                >
                  {collapsedSections[category] ? (
                    <ChevronRight className="mt-1 h-4 w-4 text-muted-foreground" />
                  ) : (
                    <ChevronDown className="mt-1 h-4 w-4 text-muted-foreground" />
                  )}
                  <div>
                    <h3 className="text-lg font-semibold">{category}</h3>
                    <p className="text-sm text-muted-foreground">
                      {category === 'Downloaders'
                        ? 'Tarefas de captura e download direto das concessionarias.'
                        : 'Tarefas completas com parametros, pasta de saida e etapas adicionais.'}
                    </p>
                  </div>
                </button>
                <span className="text-sm text-muted-foreground">
                  {tasksByCategory[category]?.length || 0} itens
                </span>
              </div>

              {!collapsedSections[category] && (
                <div className="overflow-x-auto rounded-lg border border-border bg-card">
                  <Table>
                    <TableHeader className="bg-muted/50">
                      <TableRow>
                        <TableHead>task_id</TableHead>
                        <TableHead>Tarefa</TableHead>
                        <TableHead>Resumo</TableHead>
                        <TableHead>Ativo</TableHead>
                        <TableHead className="text-right">Acoes</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {tasksByCategory[category]?.map((robot) => (
                        <TableRow key={robot.id} className="hover:bg-muted/30">
                          <TableCell className="font-mono text-xs text-muted-foreground">
                            {robot.task_id || '-'}
                          </TableCell>
                          <TableCell className="font-medium">{robot.name}</TableCell>
                          <TableCell className="max-w-md text-sm text-muted-foreground">
                            {robot.description || 'Sem descricao cadastrada.'}
                          </TableCell>
                          <TableCell className="text-sm text-muted-foreground">
                            {robot.active ? 'Sim' : 'Nao'}
                          </TableCell>
                          <TableCell className="text-right">
                            <div className="flex justify-end gap-2">
                              {!isLegacyReadOnlyMode && (
                                <Button
                                  variant="default"
                                  size="sm"
                                  onClick={() => handleRun(robot)}
                                >
                                  <Play className="mr-1 h-3.5 w-3.5" /> Executar
                                </Button>
                              )}
                              <Button
                                variant="outline"
                                size="sm"
                                onClick={() => handleEdit(robot)}
                                disabled={isLegacyReadOnlyMode}
                              >
                                <Pencil className="mr-1 h-3.5 w-3.5" /> Ajustar
                              </Button>
                              <Button
                                variant="destructive"
                                size="sm"
                                onClick={() => setRobotToDelete(robot)}
                                disabled={isLegacyReadOnlyMode}
                              >
                                <Trash2 className="mr-1 h-3.5 w-3.5" /> Excluir
                              </Button>
                            </div>
                          </TableCell>
                        </TableRow>
                      ))}

                      {(!tasksByCategory[category] || tasksByCategory[category].length === 0) && (
                        <TableRow>
                          <TableCell colSpan={5} className="py-10 text-center text-muted-foreground">
                            Nenhuma tarefa sincronizada nesta categoria.
                          </TableCell>
                        </TableRow>
                      )}
                    </TableBody>
                  </Table>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      <RobotFormDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        robot={editingRobot}
        onSaved={() => {
          void loadData()
        }}
      />

      <AlertDialog open={Boolean(robotToDelete)} onOpenChange={(open) => !open && setRobotToDelete(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Excluir tarefa do catalogo</AlertDialogTitle>
            <AlertDialogDescription>
              {robotToDelete
                ? `Tem certeza que deseja excluir ${robotToDelete.name}? Esta acao nao pode ser desfeita.`
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

      <StartExecutionDialog
        open={startDialogOpen}
        onOpenChange={(open) => {
          setStartDialogOpen(open)
          if (!open) setStartTaskId(undefined)
        }}
        initialTaskId={startTaskId}
        onStarted={() => void loadData()}
      />
    </div>
  )
}
