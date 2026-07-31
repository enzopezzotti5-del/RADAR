import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

const statusConfig: Record<string, { label: string; className: string }> = {
  aguardando: { label: 'Aguardando', className: 'bg-gray-500 hover:bg-gray-600' },
  queued: { label: 'Aguardando', className: 'bg-gray-500 hover:bg-gray-600' },
  running: { label: 'Executando', className: 'bg-blue-500 hover:bg-blue-600 animate-pulse' },
  stopping: { label: 'Parando', className: 'bg-amber-500 hover:bg-amber-600 animate-pulse' },
  stopped: { label: 'Cancelado', className: 'bg-orange-500 hover:bg-orange-600' },
  completed: { label: 'Concluido', className: 'bg-green-500 hover:bg-green-600' },
  success: { label: 'Concluido', className: 'bg-green-500 hover:bg-green-600' },
  failed: { label: 'Falhou', className: 'bg-red-500 hover:bg-red-600' },
  error: { label: 'Falhou', className: 'bg-red-500 hover:bg-red-600' },
  preparando_ambiente: {
    label: 'Preparando',
    className: 'bg-blue-500 hover:bg-blue-600 animate-pulse',
  },
  atualizando_codigo: {
    label: 'Atualizando código',
    className: 'bg-blue-500 hover:bg-blue-600 animate-pulse',
  },
  instalando_dependencias: {
    label: 'Instalando deps',
    className: 'bg-blue-500 hover:bg-blue-600 animate-pulse',
  },
  executando: { label: 'Executando', className: 'bg-blue-500 hover:bg-blue-600 animate-pulse' },
  parando: { label: 'Encerrando processo', className: 'bg-amber-500 hover:bg-amber-600 animate-pulse' },
  concluido: { label: 'Concluído', className: 'bg-green-500 hover:bg-green-600' },
  falhou: { label: 'Falhou', className: 'bg-red-500 hover:bg-red-600' },
  cancelado: { label: 'Cancelado', className: 'bg-orange-500 hover:bg-orange-600' },
}

export function StatusBadge({ status }: { status: string }) {
  const config = statusConfig[status] || { label: status, className: 'bg-gray-500' }
  return (
    <Badge className={cn('text-white font-medium cursor-default border-none', config.className)}>
      {config.label}
    </Badge>
  )
}
