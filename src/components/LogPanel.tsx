import { useEffect, useRef, useState } from 'react'
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from '@/components/ui/sheet'
import { Badge } from '@/components/ui/badge'
import { runsApi } from '@/lib/api'

interface LogPanelProps {
  runId: string | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function LogPanel({ runId, open, onOpenChange }: LogPanelProps) {
  const [logs, setLogs] = useState<string[]>([])
  const [statusText, setStatusText] = useState('')
  const [isRunning, setIsRunning] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const nextLineRef = useRef(0)

  useEffect(() => {
    if (!open || !runId) return
    setLogs([])
    setStatusText('')
    setIsRunning(false)
    nextLineRef.current = 0

    let cancelled = false
    let interval: ReturnType<typeof setInterval> | undefined

    const poll = async () => {
      if (cancelled || !runId) return
      try {
        const data = await runsApi.logs(runId, nextLineRef.current)
        if (cancelled) return
        if (data.log) setLogs((prev) => [...prev, data.log])
        nextLineRef.current = data.next_line
        setStatusText(data.status_text)
        setIsRunning(data.is_running)
        if (!data.is_live && interval) {
          clearInterval(interval)
          interval = undefined
        }
      } catch {
        /* intentionally ignored */
      }
    }

    poll()
    interval = setInterval(poll, 1000)
    return () => {
      cancelled = true
      if (interval) clearInterval(interval)
    }
  }, [open, runId])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [logs])

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-[600px] sm:max-w-[600px] flex flex-col p-0">
        <SheetHeader className="border-b border-border px-4 py-3">
          <SheetTitle className="flex items-center gap-2">
            Logs — Run #{runId}
            {statusText && (
              <Badge className={isRunning ? 'bg-blue-500 animate-pulse' : 'bg-gray-500'}>
                {statusText}
              </Badge>
            )}
          </SheetTitle>
          <SheetDescription>
            {isRunning ? 'Execução em andamento...' : 'Execução finalizada.'}
          </SheetDescription>
        </SheetHeader>
        <div className="flex-1 overflow-auto bg-[#1e1e1e] p-4 font-mono text-sm">
          {logs.map((chunk, i) => (
            <pre key={i} className="text-gray-300 whitespace-pre-wrap break-all">
              {chunk}
            </pre>
          ))}
          {logs.length === 0 && <p className="text-gray-500 italic">Aguardando logs...</p>}
          <div ref={bottomRef} />
        </div>
      </SheetContent>
    </Sheet>
  )
}
