# API

O contrato versionado é [`api-contract.json`](api-contract.json). O cliente
React usa `src/services/legacyCompat.ts`; valide a consistência estática com:

```powershell
npm run check:api-contract
```

Rotas públicas: `GET /health`, `GET /api/session`, `GET/POST /login` e assets
necessários em `/assets/*` e `/static/*`. As rotas `/api/*` restantes requerem
sessão autenticada e retornam `401` sem ela. Ações de tarefas, execuções e
agendamentos são operacionais e nunca devem ser expostas sem autenticação.

O calendário devolve `has_metrics=false` quando não há métrica persistida; isso
não representa zero nem permite inferir que uma fatura inexiste.
