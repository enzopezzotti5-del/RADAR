import { readFile } from 'node:fs/promises'

const contract = JSON.parse(await readFile(new URL('../docs/api-contract.json', import.meta.url)))
const source = await readFile(new URL('../src/services/legacyCompat.ts', import.meta.url), 'utf8')
const expectedPaths = ['/tasks', '/dashboard', '/runs/live', '/runs/history', '/calendar/summary', '/schedules', '/login']
const contractPaths = new Set(contract.endpoints.map((endpoint) => endpoint.path))

for (const path of expectedPaths) {
  if (!source.includes(path)) throw new Error(`Rota ausente no cliente: ${path}`)
  const fullPath = path === '/login' ? path : `/api${path}`
  if (path !== '/login' && !contractPaths.has(fullPath)) {
    throw new Error(`Rota ausente no contrato: ${fullPath}`)
  }
}

if (contract.read_only_frontend.enabled_by !== 'VITE_RADAR_READ_ONLY=true') {
  throw new Error('Contrato nao declara o modo somente leitura esperado.')
}

console.log(`API contract OK: ${contract.endpoints.length} endpoints`)
