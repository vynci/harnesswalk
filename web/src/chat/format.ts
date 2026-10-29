export function formatCountdown(ms: number): string {
  if (ms <= 0) return 'expired'
  const total = Math.ceil(ms / 1000)
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${m}:${String(s).padStart(2, '0')}`
}
