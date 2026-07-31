# Contrato de API do Radar React

O frontend usa o backend Flask canônico com base relativa `/api` e envia o
cookie de sessão com `credentials: include`. O login usa `POST /login` com
formulário `username`, `password` e `next`; o logout usa `POST /logout`.

O contrato estruturado e canônico está em `docs/api-contract.json`. Ele
descreve as rotas de saúde, catálogo, painel, execuções, logs, agendamentos e
o resumo do Calendário. A versão publicada deste frontend é somente leitura:
`VITE_RADAR_DATA_SOURCE=flask`, `VITE_RADAR_READ_ONLY=true` e
`VITE_RADAR_API_BASE=/api`.

As rotas mutáveis permanecem documentadas para compatibilidade com o backend,
mas são bloqueadas localmente no modo somente leitura. Não há URL absoluta da
VM, PocketBase operacional ou credencial no contrato.
