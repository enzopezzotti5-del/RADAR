import { useMemo, useState } from 'react'
import { Mail } from 'lucide-react'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { usePolling } from '@/hooks/use-polling'
import { emailApi, type EmailEvent } from '@/services/email'

type WindowKey = 'Hoje' | '7 dias' | 'Mes'

function inWindow(event: EmailEvent, window: WindowKey) {
  if (!event.captured_at) return false
  const date = new Date(event.captured_at)
  if (Number.isNaN(date.getTime())) return false
  const now = new Date()
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  if (window === 'Hoje') return date >= start
  if (window === '7 dias') return date >= new Date(start.getTime() - 6 * 86400000)
  return date.getFullYear() === now.getFullYear() && date.getMonth() === now.getMonth()
}

export function EmailInvoiceSummary() {
  const [events, setEvents] = useState<EmailEvent[]>([])
  const load = async () => {
    try { setEvents((await emailApi.history()).events) } catch { setEvents([]) }
  }
  usePolling(load, 30000)
  const rows = useMemo(() => (['Hoje', '7 dias', 'Mes'] as WindowKey[]).map((window) => {
    const scoped = events.filter((event) => inWindow(event, window))
    return { window, captured: scoped.filter((event) => event.category === 'SUCCESS').length,
      duplicate: scoped.filter((event) => event.category === 'DUPLICATE').length,
      pending: scoped.filter((event) => event.category === 'LINK_PENDING').length,
      error: scoped.filter((event) => event.category === 'ERROR').length }
  }), [events])

  return <Card>
    <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
      <CardTitle className="text-lg">Faturas por E-mail</CardTitle><Mail className="h-5 w-5 text-blue-500" />
    </CardHeader>
    <CardContent>
      <div className="grid gap-3 md:grid-cols-3">
        {rows.map((row) => <div key={row.window} className="rounded-lg border bg-muted/20 p-3">
          <p className="mb-2 text-sm font-semibold">{row.window}</p>
          <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs"><dt className="text-muted-foreground">Capturadas</dt><dd className="font-medium text-emerald-600">{row.captured}</dd><dt className="text-muted-foreground">Duplicadas</dt><dd>{row.duplicate}</dd><dt className="text-muted-foreground">Pendentes</dt><dd className="text-amber-600">{row.pending}</dd><dt className="text-muted-foreground">Erros</dt><dd className="text-destructive">{row.error}</dd></dl>
        </div>)}
      </div>
    </CardContent>
  </Card>
}
