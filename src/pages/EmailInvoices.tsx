import { useState } from 'react'
import { Mail, RefreshCw } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { usePolling } from '@/hooks/use-polling'
import { emailApi, type EmailCategory, type EmailEvent } from '@/services/email'

const labels: Record<EmailCategory, string> = { SUCCESS: 'CAPTURADO', DUPLICATE: 'DUPLICADO', LINK_PENDING: 'LINK_PENDENTE', IGNORED_NON_INVOICE: 'IGNORADO', CORRECTION: 'CORRECAO', ERROR: 'ERRO' }
const tones: Record<EmailCategory, string> = { SUCCESS: 'bg-emerald-100 text-emerald-800', DUPLICATE: 'bg-slate-100 text-slate-700', LINK_PENDING: 'bg-amber-100 text-amber-800', IGNORED_NON_INVOICE: 'bg-slate-100 text-slate-700', CORRECTION: 'bg-blue-100 text-blue-800', ERROR: 'bg-red-100 text-red-800' }

export default function EmailInvoices() {
  const [events, setEvents] = useState<EmailEvent[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const load = async () => {
    try { const result = await emailApi.history(); setEvents(result.events); setTotal(result.count) } finally { setLoading(false) }
  }
  usePolling(load, 30000)
  return <div className="space-y-6 animate-fade-in">
    <div className="flex items-center justify-between"><div><h2 className="text-2xl font-bold">Faturas por E-mail</h2><p className="text-muted-foreground">Capturas importadas do manifesto, sem acesso a credenciais ou conteúdo de e-mail.</p></div><Button variant="outline" onClick={() => void load()} disabled={loading}><RefreshCw className={`mr-2 h-4 w-4 ${loading ? 'animate-spin' : ''}`} />Atualizar</Button></div>
    <Card><CardHeader className="flex flex-row items-center justify-between"><CardTitle className="text-lg">Historico de capturas</CardTitle><Mail className="h-5 w-5 text-blue-500" /></CardHeader><CardContent><Table><TableHeader><TableRow><TableHead>Data/hora</TableHead><TableHead>Concessionaria</TableHead><TableHead>UC</TableHead><TableHead>Referencia</TableHead><TableHead>Status</TableHead><TableHead>Arquivo</TableHead><TableHead>UID IMAP</TableHead><TableHead>Resultado</TableHead></TableRow></TableHeader><TableBody>{events.map((event, index) => <TableRow key={`${event.imap_uid || 'event'}-${index}`}><TableCell>{event.captured_at ? new Date(event.captured_at).toLocaleString() : '-'}</TableCell><TableCell>{event.provider || '-'}</TableCell><TableCell>{event.uc || '-'}</TableCell><TableCell>{event.reference || '-'}</TableCell><TableCell><Badge className={tones[event.category]}>{labels[event.category]}</Badge></TableCell><TableCell className="max-w-44 truncate">{event.original_filename || '-'}</TableCell><TableCell className="font-mono text-xs">{event.imap_uid || '-'}</TableCell><TableCell className="max-w-56 truncate">{event.pending_reason || event.document_type || '-'}</TableCell></TableRow>)}{!loading && events.length === 0 && <TableRow><TableCell colSpan={8} className="py-8 text-center text-muted-foreground">Nenhuma captura encontrada.</TableCell></TableRow>}</TableBody></Table><p className="mt-4 text-xs text-muted-foreground">Exibindo {events.length} de {total} eventos.</p></CardContent></Card>
  </div>
}
