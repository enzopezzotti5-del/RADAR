export function formatDuration(
  totalSeconds?: number | null,
): string {
  if (
    typeof totalSeconds !== 'number' ||
    !Number.isFinite(totalSeconds) ||
    totalSeconds <= 0
  ) {
    return '-'
  }

  if (totalSeconds < 60) {
    return `${totalSeconds.toFixed(1)}s`
  }

  if (totalSeconds < 3600) {
    return `${Math.floor(totalSeconds / 60)}min`
  }

  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)

  if (minutes === 0) {
    return `${hours}h`
  }

  return `${hours}h${minutes}min`
}
