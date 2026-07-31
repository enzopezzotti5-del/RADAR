export type CalendarStatus = 'concluido' | 'falhou' | 'cancelado' | 'executando' | 'aguardando' | 'parando' | 'outros'

export const calendarStatusOrder: CalendarStatus[] = [
  'concluido',
  'falhou',
  'cancelado',
  'executando',
  'aguardando',
  'parando',
  'outros',
]

export const calendarStatusLabel: Record<CalendarStatus, string> = {
  concluido: 'Concluido', falhou: 'Falhou', cancelado: 'Cancelado', executando: 'Em execucao',
  aguardando: 'Aguardando', parando: 'Parando', outros: 'Outros',
}

export function toCalendarStatus(value: string | undefined): CalendarStatus {
  switch ((value || '').toLowerCase()) {
    case 'completed': case 'success': case 'concluido': return 'concluido'
    case 'failed': case 'error': case 'falhou': return 'falhou'
    case 'stopped': case 'cancelled': case 'canceled': case 'cancelado': return 'cancelado'
    case 'running': case 'executando': return 'executando'
    case 'queued': case 'aguardando': return 'aguardando'
    case 'stopping': case 'parando': return 'parando'
    default: return 'outros'
  }
}
