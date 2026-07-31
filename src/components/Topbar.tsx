import { Search } from 'lucide-react'
import { useLocation } from 'react-router-dom'

import { ThemeToggle } from '@/components/ThemeToggle'
import { Input } from '@/components/ui/input'
import { SidebarTrigger } from '@/components/ui/sidebar'

export function Topbar() {
  const location = useLocation()
  const title =
    {
      '/': 'Dashboard',
      '/downloaders': 'Catalogo do Radar',
      '/schedules': 'Agendamentos',
      '/executions': 'Execucoes',
      '/settings': 'Configuracoes',
    }[location.pathname] || 'Radar de Energia'

  return (
    <header className="flex h-16 shrink-0 items-center gap-2 border-b border-border bg-card px-6 shadow-sm">
      <SidebarTrigger className="-ml-1 text-muted-foreground hover:text-foreground" />
      <h1 className="ml-2 text-lg font-semibold text-card-foreground">{title}</h1>
      <div className="flex-1" />
      <div className="relative hidden w-full max-w-sm md:block">
        <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
        <Input type="search" placeholder="Buscar tarefas e execucoes..." className="border-none bg-muted/50 pl-8" />
      </div>
      <ThemeToggle />
    </header>
  )
}
