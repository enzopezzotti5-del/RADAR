(function () {
    const query = new URLSearchParams(window.location.search);
    const useMock = window.WATCHER_USE_MOCK === true || query.get('mock') === '1';
    const scenario = query.get('scenario') || 'operacional';

    function clone(value) {
        return JSON.parse(JSON.stringify(value));
    }

    function delay(ms) {
        return new Promise((resolve) => setTimeout(resolve, ms));
    }

    function timelineEvent(label, status, timestamp) {
        return { label, status, timestamp };
    }

    const scenarios = {
        operacional: {
            resumo: {
                disponivel: true,
                dados_obsoletos: false,
                ultima_atualizacao: '2026-07-14T11:02:33',
                erro: null,
            },
            tarefa: {
                estado: 'Ready',
                ultima_execucao: '2026-07-14T10:30:00',
                proxima_execucao: '2026-07-14T11:30:00',
                ultimo_resultado: 'OK',
                usuario: 'watcher',
                modo_execucao: 'autonomo',
                comando: 'python -m radar_v2.watcher_service',
                disponivel: true,
            },
            watcher: {
                status: 'operacional',
                ultima_atividade: '2026-07-14T11:01:50',
                ultimo_inicio: '2026-07-14T10:10:00',
                ultimo_fim: '2026-07-14T10:11:10',
                proxima_execucao: '2026-07-14T11:10:00',
                fonte_proxima: 'cron',
                lock: 'livre',
                pid: null,
                idade_lock: null,
            },
            pipelines: {
                sessoes_ativas: 1,
                sessoes_pendentes: 2,
                sessoes_interrompidas: 0,
                sessoes_retornaveis: 1,
                sessoes_totais: 12,
                pdfs_entrada: 3,
                pdfs_staging: 4,
                pdfs_parados_entrada: 1,
                pdfs_parados_staging: 2,
                alertas_criticos: 1,
                alertas_atencao: 2,
                alertas_nao_reconhecidos: 3,
            },
            sessoes: [
                {
                    session_id: 'SES-20260714-01',
                    concessionaria: 'COELBA',
                    grupo: 'BT',
                    referencia: '07-2026',
                    pdfs: 4,
                    execucao_status: 'pipeline_ok',
                    reconciliacao_status: 'inconsistente',
                    etapa_atual: 'Validação PDF × XLSX',
                    atualizacao: '2026-07-14T10:59:10',
                    origem: 'entrada/COELBA',
                    staging: 'staging/COELBA',
                    xlsx: 'sessao_01.xlsx',
                    auditoria: 'Relatório disponível',
                    created_at: '2026-07-14T10:15:00',
                    updated_at: '2026-07-14T10:59:10',
                    retomavel: true,
                    motivo_parada: 'Falha de OCR',
                    return_code: 125,
                    timeline: [
                        timelineEvent('PDF detectado', 'concluída', '2026-07-14T10:15:01'),
                        timelineEvent('Carimbo atribuído', 'concluída', '2026-07-14T10:15:54'),
                        timelineEvent('OCR iniciado', 'concluída', '2026-07-14T10:16:20'),
                        timelineEvent('OCR concluído', 'concluída', '2026-07-14T10:18:14'),
                        timelineEvent('Validação PDF × XLSX', 'erro', '2026-07-14T10:59:10'),
                    ],
                    arquivos: [
                        { arquivo: 'BB_2008391.pdf', carimbo: '2008391', instalacao: '000116994011', referencia: '07-2026', grupo: 'BT', status: 'Parado', local: 'entrada/COELBA', destino: 'staging/COELBA' },
                    ],
                    fontes_divergentes: true,
                    detalhes_fontes: [
                        { indice: 'DIGITADO', sessao: 'digitado', localizacao: 'Investigar', motivo: 'STATUS=DIGITADO, mas o PDF está em Investigar/' },
                    ],
                },
                {
                    session_id: 'SES-20260630-15',
                    concessionaria: 'ENEL',
                    grupo: 'MT',
                    referencia: '06-2026',
                    pdfs: 2,
                    execucao_status: 'concluido',
                    reconciliacao_status: 'confirmada',
                    etapa_atual: 'Filtro executado',
                    atualizacao: '2026-07-14T09:00:12',
                    origem: 'entrada/ENEL',
                    staging: 'staging/ENEL',
                    xlsx: 'sessao_15.xlsx',
                    auditoria: 'Sem divergências',
                    created_at: '2026-06-30T08:35:00',
                    updated_at: '2026-07-14T09:00:12',
                    retomavel: false,
                    motivo_parada: null,
                    return_code: 0,
                    timeline: [
                        timelineEvent('PDF detectado', 'concluída', '2026-06-30T08:35:10'),
                        timelineEvent('Carimbo atribuído', 'concluída', '2026-06-30T08:35:54'),
                        timelineEvent('OCR iniciado', 'concluída', '2026-06-30T08:36:20'),
                        timelineEvent('OCR concluído', 'concluída', '2026-06-30T08:38:14'),
                        timelineEvent('Digitação concluída', 'concluída', '2026-06-30T08:45:00'),
                        timelineEvent('Filtro executado', 'concluída', '2026-07-14T09:00:12'),
                        timelineEvent('Destino final', 'concluída', '2026-07-14T09:00:30'),
                    ],
                    arquivos: [
                        { arquivo: 'BB_2008380.pdf', carimbo: '2008380', instalacao: '000116994011', referencia: '06-2026', grupo: 'MT', status: 'Concluído', local: 'staging/ENEL', destino: 'Digitadas' },
                    ],
                    fontes_divergentes: false,
                    detalhes_fontes: [],
                },
                {
                    session_id: 'SES-LEGADO-12',
                    concessionaria: 'CELESC',
                    grupo: 'BT',
                    referencia: '05-2025',
                    pdfs: 1,
                    execucao_status: 'desconhecido',
                    reconciliacao_status: 'resultado_desconhecido',
                    etapa_atual: null,
                    atualizacao: null,
                    origem: null,
                    staging: null,
                    xlsx: null,
                    auditoria: null,
                    created_at: '2025-05-16T11:20:00',
                    updated_at: null,
                    retomavel: false,
                    motivo_parada: null,
                    return_code: null,
                    timeline: [],
                    arquivos: [],
                    fontes_divergentes: false,
                    detalhes_fontes: [],
                    legado: true,
                },
            ],
            concessionarias: ['COELBA', 'ENEL', 'CELESC'],
            arquivos: {
                entrada: [
                    { arquivo: 'BB_2008400.pdf', concessionaria: 'COELBA', tipo: 'entrada_flat', idade_minutos: 12, ciclos: 1, tamanho_bytes: 974000, prefixo_bb: true, nivel: 'atenção', caminho: 'D:\\entrada\\BB_2008400.pdf' },
                    { arquivo: 'entrada_1.pdf', concessionaria: 'ENEL', tipo: 'subpasta_interna', idade_minutos: 35, ciclos: 2, tamanho_bytes: 1204000, prefixo_bb: false, nivel: 'atencao', caminho: 'D:\\entrada\\enel\\entrada_1.pdf' },
                ],
                staging: [
                    { arquivo: 'BB_2008399.pdf', concessionaria: 'COELBA', tipo: 'staging', idade_minutos: 8, ciclos: 1, tamanho_bytes: 830000, prefixo_bb: true, nivel: 'normal', caminho: 'D:\\staging\\BB_2008399.pdf' },
                    { arquivo: 'BB_2008388.pdf', concessionaria: 'ENEL', tipo: 'staging', idade_minutos: 72, ciclos: 3, tamanho_bytes: 1930000, prefixo_bb: true, nivel: 'critico', caminho: 'D:\\staging\\BB_2008388.pdf' },
                ],
            },
            alertas: [
                {
                    id: 'ALERT-001',
                    nivel: 'Crítico',
                    titulo: 'Falha de reconciliação',
                    motivo: 'Sessão com PDF em Investigar mas status DIGITADO',
                    session_id: 'SES-20260714-01',
                    carimbo: '2008391',
                    arquivo: 'BB_2008391.pdf',
                    horario_problema: '2026-07-14T10:59:20',
                    horario_deteccao: '2026-07-14T11:00:05',
                    acao_recomendada: 'Verificar índice e local físico',
                    reconhecido: false,
                },
                {
                    id: 'ALERT-002',
                    nivel: 'Atenção',
                    titulo: 'Session antiga',
                    motivo: 'Sessão legada sem dados completos',
                    session_id: 'SES-LEGADO-12',
                    carimbo: null,
                    arquivo: null,
                    horario_problema: '2025-05-16T11:21:00',
                    horario_deteccao: '2025-05-16T11:25:00',
                    acao_recomendada: 'Manter para análise histórica',
                    reconhecido: true,
                },
            ],
            logs: {
                total: 184,
                offset: 0,
                limit: 100,
                texto: '2026-07-14 10:15:00 [INFO] Iniciando vigilância de watcher...\n2026-07-14 10:15:03 [INFO] Encontrado 3 arquivos novos...\n2026-07-14 10:15:10 [WARN] Lock estará disponível em 15 seg...\n2026-07-14 10:15:25 [INFO] Watcher operacional.\n2026-07-14 10:59:10 [ERROR] Divergência detectada em SES-20260714-01.\n2026-07-14 11:01:50 [INFO] Polling finalizado com sucesso.',
            },
        },
        atrasado: {
            resumo: {
                disponivel: true,
                dados_obsoletos: true,
                ultima_atualizacao: '2026-07-14T09:40:00',
                erro: 'A fonte de dados está atrasada',
            },
            tarefa: {
                estado: 'Ready',
                ultima_execucao: '2026-07-14T09:28:00',
                proxima_execucao: '2026-07-14T10:28:00',
                ultimo_resultado: 'OK',
                usuario: 'watcher',
                modo_execucao: 'agendado',
                comando: 'python -m radar_v2.watcher_service',
                disponivel: true,
            },
            watcher: {
                status: 'atrasado',
                ultima_atividade: '2026-07-14T09:40:00',
                ultimo_inicio: '2026-07-14T09:00:00',
                ultimo_fim: '2026-07-14T09:01:10',
                proxima_execucao: '2026-07-14T10:00:00',
                fonte_proxima: 'cron',
                lock: 'ativado',
                pid: 7224,
                idade_lock: 1800,
            },
            pipelines: {
                sessoes_ativas: 0,
                sessoes_pendentes: 5,
                sessoes_interrompidas: 1,
                sessoes_retornaveis: 0,
                sessoes_totais: 6,
                pdfs_entrada: 5,
                pdfs_staging: 2,
                pdfs_parados_entrada: 2,
                pdfs_parados_staging: 0,
                alertas_criticos: 2,
                alertas_atencao: 4,
                alertas_nao_reconhecidos: 2,
            },
            sessoes: [],
            concessionarias: ['COELBA', 'ENEL', 'CELESC'],
            arquivos: { entrada: [], staging: [] },
            alertas: [],
            logs: { total: 0, offset: 0, limit: 100, texto: '2026-07-14 09:40:00 [WARN] Fonte de dados atrasada.\n2026-07-14 09:40:00 [INFO] Mantendo cache local.' },
        },
        indisponivel: {
            resumo: {
                disponivel: false,
                dados_obsoletos: false,
                ultima_atualizacao: null,
                erro: 'Pasta de rede indisponível',
            },
            tarefa: null,
            watcher: null,
            pipelines: null,
            sessoes: [],
            concessionarias: [],
            arquivos: { entrada: [], staging: [] },
            alertas: [],
            logs: { total: 0, offset: 0, limit: 100, texto: '' },
        },
        inconsistente: {
            resumo: {
                disponivel: true,
                dados_obsoletos: false,
                ultima_atualizacao: '2026-07-14T11:05:00',
                erro: 'Dados conflitantes detectados',
            },
            tarefa: {
                estado: 'Running',
                ultima_execucao: '2026-07-14T11:00:00',
                proxima_execucao: '2026-07-14T11:30:00',
                ultimo_resultado: 'Aguardando',
                usuario: 'watcher',
                modo_execucao: 'autonomo',
                comando: 'python -m radar_v2.watcher_service --scan',
                disponivel: true,
            },
            watcher: {
                status: 'possivelmente_parado',
                ultima_atividade: '2026-07-14T11:05:00',
                ultimo_inicio: '2026-07-14T10:45:00',
                ultimo_fim: null,
                proxima_execucao: '2026-07-14T11:15:00',
                fonte_proxima: 'fila interna',
                lock: 'ativo',
                pid: 8001,
                idade_lock: 7200,
            },
            pipelines: {
                sessoes_ativas: 2,
                sessoes_pendentes: 3,
                sessoes_interrompidas: 1,
                sessoes_retornaveis: 1,
                sessoes_totais: 9,
                pdfs_entrada: 4,
                pdfs_staging: 3,
                pdfs_parados_entrada: 2,
                pdfs_parados_staging: 1,
                alertas_criticos: 3,
                alertas_atencao: 1,
                alertas_nao_reconhecidos: 2,
            },
            sessoes: [
                {
                    session_id: 'SES-20260714-02',
                    concessionaria: 'COELBA',
                    grupo: 'BT',
                    referencia: '07-2026',
                    pdfs: 1,
                    execucao_status: 'erro',
                    reconciliacao_status: 'inconsistente',
                    etapa_atual: 'Digitação',
                    atualizacao: '2026-07-14T11:05:00',
                    origem: 'entrada/COELBA',
                    staging: 'staging/COELBA',
                    xlsx: 'sessao_02.xlsx',
                    auditoria: 'Divergência de status',
                    created_at: '2026-07-14T10:55:00',
                    updated_at: '2026-07-14T11:05:00',
                    retomavel: true,
                    motivo_parada: 'Timeout no Chrome',
                    return_code: 143,
                    timeline: [
                        timelineEvent('PDF detectado', 'concluída', '2026-07-14T10:55:00'),
                        timelineEvent('OCR iniciado', 'concluída', '2026-07-14T10:56:00'),
                        timelineEvent('Digitação iniciada', 'erro', '2026-07-14T11:05:00'),
                    ],
                    arquivos: [
                        { arquivo: 'BB_2008401.pdf', carimbo: '2008401', instalacao: '000116994011', referencia: '07-2026', grupo: 'BT', status: 'Erro', local: 'entrada/COELBA', destino: 'staging/COELBA' },
                    ],
                    fontes_divergentes: true,
                    detalhes_fontes: [
                        { indice: 'PENDENTE', sessao: 'pendente', localizacao: 'entrada', motivo: 'STATUS=PENDENTE, mas o arquivo foi movido para entrada/COELBA' },
                    ],
                },
            ],
            concessionarias: ['COELBA', 'ENEL', 'CELESC'],
            arquivos: { entrada: [], staging: [] },
            alertas: [
                {
                    id: 'ALERT-101',
                    nivel: 'Crítico',
                    titulo: 'Watcher possivelmente parado',
                    motivo: 'Lock ativo por mais de 2 horas',
                    session_id: 'SES-20260714-02',
                    carimbo: '2008401',
                    arquivo: 'BB_2008401.pdf',
                    horario_problema: '2026-07-14T11:05:00',
                    horario_deteccao: '2026-07-14T11:06:00',
                    acao_recomendada: 'Validar processo manualmente',
                    reconhecido: false,
                },
            ],
            logs: { total: 15, offset: 0, limit: 100, texto: '2026-07-14 11:05:00 [ERROR] Watcher não respondeu ao cron.\n2026-07-14 11:06:00 [INFO] Tentando reconectar...' },
        },
    };

    const getScenarioData = () => scenarios[scenario] || scenarios.operacional;

    const mock = {
        enabled: !!useMock,
        scenario: scenario,
        getResumo: async function () {
            await delay(240);
            return clone(getScenarioData().resumo);
        },
        getTarefa: async function () {
            await delay(240);
            return clone(getScenarioData().tarefa || {});
        },
        getSessoes: async function () {
            await delay(300);
            return clone(getScenarioData().sessoes || []);
        },
        getSessao: async function (sessionId) {
            await delay(220);
            const item = (getScenarioData().sessoes || []).find((row) => row.session_id === sessionId);
            return clone(item || null);
        },
        getConcessionarias: async function () {
            await delay(200);
            return clone(getScenarioData().concessionarias || []);
        },
        getArquivos: async function () {
            await delay(240);
            return clone(getScenarioData().arquivos || { entrada: [], staging: [] });
        },
        getAlertas: async function () {
            await delay(260);
            return clone(getScenarioData().alertas || []);
        },
        getLogs: async function () {
            await delay(220);
            return clone(getScenarioData().logs || { total: 0, offset: 0, limit: 100, texto: '' });
        },
        reconhecerAlerta: async function (alertaId, payload) {
            await delay(240);
            return { ok: true, alerta_id: alertaId, observacao: payload.observacao || null };
        },
        adicionarObservacao: async function (payload) {
            await delay(240);
            return { ok: true, observacao: payload.texto || null };
        },
    };

    window.WatcherMockData = {
        isEnabled: () => mock.enabled,
        scenario: mock.scenario,
        getResumo: mock.getResumo,
        getTarefa: mock.getTarefa,
        getSessoes: mock.getSessoes,
        getSessao: mock.getSessao,
        getConcessionarias: mock.getConcessionarias,
        getArquivos: mock.getArquivos,
        getAlertas: mock.getAlertas,
        getLogs: mock.getLogs,
        reconhecerAlerta: mock.reconhecerAlerta,
        adicionarObservacao: mock.adicionarObservacao,
    };
})();
