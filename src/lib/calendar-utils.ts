import { format, parse, startOfMonth } from 'date-fns'
import { ptBR } from 'date-fns/locale'

export function parseRadarDate(value: string): Date | null {
  const match = String(value || '').match(/^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?/)
  if (!match) return null
  const [, year, month, day, hour = '0', minute = '0', second = '0'] = match
  return new Date(Number(year), Number(month) - 1, Number(day), Number(hour), Number(minute), Number(second))
}

export function todayLocal(): Date {
  const now = new Date()
  return new Date(now.getFullYear(), now.getMonth(), now.getDate())
}

export function monthKey(date: Date): string { return format(date, 'yyyy-MM') }
export function dateKey(date: Date): string { return format(date, 'yyyy-MM-dd') }
export function dateKeyFromRadar(value: string): string | null {
  const parsed = parseRadarDate(value)
  return parsed ? dateKey(parsed) : null
}
export function formatMonth(date: Date): string { return format(date, 'MMMM yyyy', { locale: ptBR }) }
export function formatDay(date: Date): string { return format(date, "dd 'de' MMMM 'de' yyyy", { locale: ptBR }) }
export function formatShortDate(value: string): string {
  const parsed = parseRadarDate(value)
  return parsed ? format(parsed, 'dd/MM/yyyy HH:mm') : '-'
}
export function monthStart(date: Date): Date { return startOfMonth(date) }
export function parseMonthKey(value: string): Date { return parse(`${value}-01`, 'yyyy-MM-dd', new Date()) }
export function dateFromKey(value: string): Date | null { return parseRadarDate(`${value} 00:00:00`) }

// The task catalog does not expose a dedicated distributor field. This mapping
// only recognizes names that actually exist in the catalog; all others remain explicit.
export function concessionariaFromTask(taskId: string, taskName: string): string {
  const text = `${taskId} ${taskName}`.toLowerCase()
  if (text.includes('neoenergia')) return 'Neoenergia'
  if (text.includes('cpfl')) return 'CPFL'
  if (text.includes('rge')) return 'RGE'
  if (text.includes('cemig')) return 'CEMIG'
  if (text.includes('copel')) return 'COPEL'
  if (text.includes('enel')) return 'ENEL'
  if (text.includes('celesc')) return 'CELESC'
  if (text.includes('equatorial')) return 'Equatorial'
  if (text.includes('light')) return 'Light'
  return 'Nao identificada'
}
