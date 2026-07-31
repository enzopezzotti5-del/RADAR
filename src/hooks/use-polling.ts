import { useEffect, useRef } from 'react'

export function usePolling(callback: () => void, intervalMs: number, enabled = true) {
  const callbackRef = useRef(callback)
  callbackRef.current = callback

  useEffect(() => {
    if (!enabled) return
    callbackRef.current()
    const interval = setInterval(() => callbackRef.current(), intervalMs)
    return () => clearInterval(interval)
  }, [intervalMs, enabled])
}
