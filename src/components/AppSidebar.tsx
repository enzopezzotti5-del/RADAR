import { Link, useLocation } from 'react-router-dom'
import { Activity, CalendarDays, CalendarClock, DownloadCloud, LayoutDashboard, LogOut, Mail, Settings } from 'lucide-react'

import logoUrl from '@/assets/chatgpt-image-24-de-jul.de-2026-104525-83e13.png'
import { useAuth } from '@/hooks/use-auth'
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from '@/components/ui/sidebar'

const items = [
  { title: 'Dashboard', url: '/', icon: LayoutDashboard },
  { title: 'Catalogo', url: '/downloaders', icon: DownloadCloud },
  { title: 'Agendamentos', url: '/schedules', icon: CalendarClock },
  { title: 'Calendario', url: '/calendario', icon: CalendarDays },
  { title: 'Faturas por E-mail', url: '/emails', icon: Mail },
  { title: 'Execucoes', url: '/executions', icon: Activity },
  { title: 'Configuracoes', url: '/settings', icon: Settings },
]

export function AppSidebar() {
  const location = useLocation()
  const { signOut, authEnabled } = useAuth()

  return (
    <Sidebar variant="inset" className="border-r border-border">
      <SidebarHeader className="flex h-16 items-center justify-center overflow-hidden border-b border-border px-4">
        <Link to="/" className="flex h-full w-full items-center justify-center py-2">
          <img src={logoUrl} alt="Acao Engenharia Logo" className="h-full max-h-[40px] w-auto object-contain" />
        </Link>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu>
              {items.map((item) => (
                <SidebarMenuItem key={item.title}>
                  <SidebarMenuButton asChild isActive={location.pathname === item.url}>
                    <Link to={item.url}>
                      <item.icon />
                      <span>{item.title}</span>
                    </Link>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
      {authEnabled && (
        <SidebarFooter>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton onClick={() => {
                void signOut()
              }}>
                <LogOut />
                <span>Sair</span>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarFooter>
      )}
    </Sidebar>
  )
}
