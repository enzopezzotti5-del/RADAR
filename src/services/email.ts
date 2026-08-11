import { radarRead } from '@/services/legacyCompat'

export type EmailCategory = 'SUCCESS' | 'DUPLICATE' | 'LINK_PENDING' | 'IGNORED_NON_INVOICE' | 'CORRECTION' | 'ERROR'

export interface EmailEvent {
  captured_at: string | null
  category: EmailCategory
  document_type: string | null
  imap_uid: string | null
  original_filename: string | null
  pending_reason: string | null
  provider: string | null
  reference: string | null
  sha256: string | null
  subject: string | null
  uc: string | null
}

export interface EmailSummary {
  by_category: Record<EmailCategory, number>
  total_imported: number
  watermark_line: number
}

export const emailApi = {
  summary: () => radarRead<EmailSummary>('/email/summary'),
  history: (limit = 1000) => radarRead<{ events: EmailEvent[]; count: number }>(`/email/history?limit=${limit}`),
}
