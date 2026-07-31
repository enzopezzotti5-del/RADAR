(function () {
    const root = document.getElementById('watcher-page');
    if (!root) return;

    const PARAMS = new URLSearchParams(window.location.search);
    const MOCK_ENABLED = window.WATCHER_USE_MOCK === true || PARAMS.get('mock') === '1';
    const REPORT_MODE = MOCK_ENABLED ? 'mock' : 'api';
    const LOG_LIMITS = [100, 250, 500, 1000];

    const state = {
        abaAtiva: 'overview',
        resumo: { carregando: false, erro: null, payload: null },
        tarefa: { carregando: false, erro: null, payload: null },
        sessoes: { carregando: false, erro: null, payload: [], total: 0, meta: null },
        concessionarias: [],
        arquivos: { carregando: false, erro: null, payload: { entrada: [], staging: [] } },
        alertas: { carregando: false, erro: null, payload: [] },
        sessaoSelecionada: null,
        filtrosSessoes: { status: 'todas', concessionaria: '', grupo: '', referencia: '', session_id: '', q: '' },
        filtrosLogs: { session_id: '', carimbo: '', concessionaria: '', nivel: '', limit: 100, offset: 0 },
        timers: { resumo: null, alertas: null, sessoes: null, arquivos: null },
        controllers: new Map(),
        ultimaAtualizacao: null,
    };

    const elements = {
        watcherNoticeRow: root.querySelector('#watcherNoticeRow'),
        watcherLastUpdated: root.querySelector('#watcherLastUpdated'),
        watcherConnectionStatus: root.querySelector('#watcherConnectionStatus'),
        watcherStaleIndicator: root.querySelector('#watcherStaleIndicator'),
        watcherRefreshBtns: Array.from(root.querySelectorAll('#watcherRefreshBtn, #watcherManualRefreshBtn')),
        tabs: Array.from(root.querySelectorAll('.watcher-tab')),
        panels: Array.from(root.querySelectorAll('.watcher-tab-panel')),
        sessionsForm: root.querySelector('#watcherSessionsFilters'),
        sessionsStatus: root.querySelector('#watcherSessionsStatus'),
        sessionsConcessionaria: root.querySelector('#watcherSessionsConcessionaria'),
        sessionsGrupo: root.querySelector('#watcherSessionsGrupo'),
        sessionsReferencia: root.querySelector('#watcherSessionsReferencia'),
        sessionsSearch: root.querySelector('#watcherSessionsSearch'),
        sessionsClear: root.querySelector('#watcherSessionsClear'),
        sessionsBody: root.querySelector('#watcherSessionsBody'),
        sessionsMessage: root.querySelector('#watcherSessionsMessage'),
        filesMessage: root.querySelector('#watcherFilesMessage'),
        entradaFilesBody: root.querySelector('#watcherEntradaFilesBody'),
        stagingFilesBody: root.querySelector('#watcherStagingFilesBody'),
        overviewMessage: root.querySelector('#watcherOverviewMessage'),
        alertsCountCriticos: root.querySelector('#alertsCountCriticos'),
        alertsCountAtencao: root.querySelector('#alertsCountAtencao'),
        alertsCountInformativos: root.querySelector('#alertsCountInformativos'),
        alertsCountNaoReconhecidos: root.querySelector('#alertsCountNaoReconhecidos'),
        alertList: root.querySelector('#watcherAlertList'),
        detailDrawer: root.querySelector('#watcherDetailDrawer'),
        detailColumn: root.querySelector('#watcherDetailColumn'),
        sessionDetail: root.querySelector('#watcherSessionDetail'),
        detailClose: root.querySelector('#watcherDetailClose'),
        actionModal: root.querySelector('#watcherActionModal'),
        actionTitle: root.querySelector('#watcherActionTitle'),
        actionMessage: root.querySelector('#watcherActionMessage'),
        actionNote: root.querySelector('#watcherActionNote'),
        actionForm: root.querySelector('#watcherActionForm'),
        actionCancel: root.querySelector('#watcherActionCancel'),
        actionCancelSecondary: root.querySelector('#watcherActionCancelSecondary'),
        actionConfirm: root.querySelector('#watcherActionConfirm'),
        logModal: root.querySelector('#watcherLogModal'),
        logClose: root.querySelector('#watcherLogClose'),
        logForm: root.querySelector('#watcherLogFiltersForm'),
        logFilterSession: root.querySelector('#watcherLogFilterSession'),
        logFilterCarimbo: root.querySelector('#watcherLogFilterCarimbo'),
        logFilterConcessionaria: root.querySelector('#watcherLogFilterConcessionaria'),
        logFilterNivel: root.querySelector('#watcherLogFilterNivel'),
        logFilterLimit: root.querySelector('#watcherLogFilterLimit'),
        logClear: root.querySelector('#watcherLogClear'),
        logPrev: root.querySelector('#watcherLogPrev'),
        logNext: root.querySelector('#watcherLogNext'),
        logCopy: root.querySelector('#watcherLogCopy'),
        logMeta: root.querySelector('#watcherLogMeta'),
        logOffset: root.querySelector('#watcherLogOffset'),
        logText: root.querySelector('#watcherLogText'),
    };

    function isMockMode() {
        return MOCK_ENABLED && window.WatcherMockData && window.WatcherMockData.isEnabled();
    }

    function buildQuery(params) {
        const query = new URLSearchParams();
        Object.entries(params).forEach(([key, value]) => {
            if (value !== undefined && value !== null && value !== '') {
                query.set(key, String(value));
            }
        });
        return query.toString();
    }

    function normalizeSessionsPayload(payload) {
        if (Array.isArray(payload)) {
            return { sessoes: payload, total: payload.length, meta: null };
        }
        if (payload && typeof payload === 'object') {
            const sessoes = Array.isArray(payload.sessoes) ? payload.sessoes : Array.isArray(payload.items) ? payload.items : [];
            const total = Number.isFinite(Number(payload.total)) ? Number(payload.total) : sessoes.length;
            return { sessoes, total, meta: payload };
        }
        return { sessoes: [], total: 0, meta: null };
    }

    function formatDateTime(value) {
        if (!value) return 'NÃƒÂ£o informado';
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return String(value);
        return date.toLocaleString('pt-BR', {
            day: '2-digit', month: '2-digit', year: 'numeric',
            hour: '2-digit', minute: '2-digit', hour12: false,
        }).replace(',', '');
    }

    function formatAge(minutes) {
        if (minutes == null || Number.isNaN(minutes)) return 'NÃƒÂ£o informado';
        if (minutes < 1) return 'agora';
        if (minutes < 60) return `${Math.round(minutes)} min`;
        if (minutes < 1440) {
            const h = Math.floor(minutes / 60);
            const m = Math.round(minutes % 60);
            return m ? `${h} h ${m} min` : `${h} h`;
        }
        const days = Math.floor(minutes / 1440);
        return `${days} dia${days > 1 ? 's' : ''}`;
    }

    function formatFileSize(bytes) {
        if (bytes == null || Number.isNaN(bytes)) return 'NÃƒÂ£o informado';
        const size = Number(bytes);
        if (size < 1024) return `${size} B`;
        if (size < 1024 ** 2) return `${(size / 1024).toFixed(0)} KB`;
        return `${(size / 1024 ** 2).toFixed(1).replace('.', ',')} MB`;
    }

    function safeText(value) {
        if (value === null || value === undefined) return 'NÃƒÂ£o informado';
        return String(value);
    }

    function clearChildren(node) {
        while (node.firstChild) node.removeChild(node.firstChild);
    }

    function safeToggleClass(element, className, shouldAdd) {
        if (!element || !element.classList) return;
        element.classList.toggle(className, !!shouldAdd);
    }

    function createElement(name, options = {}) {
        const el = document.createElement(name);
        if (options.className) el.className = options.className;
        if (options.text) el.textContent = options.text;
        if (options.html) el.innerHTML = options.html;
        if (options.attrs) {
            Object.entries(options.attrs).forEach(([key, value]) => {
                if (value !== false && value !== null && value !== undefined) {
                    el.setAttribute(key, String(value));
                }
            });
        }
        return el;
    }

    function setText(id, text) {
        const el = document.getElementById(id);
        if (el) el.textContent = safeText(text);
    }

    function setStateError(section, message) {
        state[section].erro = message;
    }

    function dispatcherError(error, section) {
        if (error.name === 'AbortError') return;
        if (error.message === 'NAO_AUTENTICADO') return;
        setStateError(section, error.message || 'Falha de comunicaÃƒÂ§ÃƒÂ£o');
    }

    async function fetchJson(url, options = {}, requestKey) {
        const controller = new AbortController();
        if (requestKey) {
            const previous = state.controllers.get(requestKey);
            if (previous) previous.abort();
            state.controllers.set(requestKey, controller);
        }
        const init = { headers: { 'Content-Type': 'application/json' }, signal: controller.signal, ...options };
        try {
            if (!window.RadarApp || typeof window.RadarApp.apiJson !== 'function') {
                throw new Error('API do Radar indisponÃ­vel');
            }
            return await window.RadarApp.apiJson(url, init);
        } finally {
            if (requestKey) state.controllers.delete(requestKey);
        }
    }

    const WatcherAPI = {
        getResumo: async function () {
            if (isMockMode()) return window.WatcherMockData.getResumo();
            return fetchJson('/api/watcher/resumo', {}, 'resumo');
        },
        getTarefa: async function () {
            if (isMockMode()) return window.WatcherMockData.getTarefa();
            return fetchJson('/api/watcher/tarefa', {}, 'tarefa');
        },
        getSessoes: async function (filters) {
            if (isMockMode()) return window.WatcherMockData.getSessoes();
            const query = buildQuery(filters);
            return fetchJson(`/api/watcher/sessoes?${query}`, {}, 'sessoes');
        },
        getSessao: async function (id) {
            if (isMockMode()) return window.WatcherMockData.getSessao(id);
            return fetchJson(`/api/watcher/sessoes/${encodeURIComponent(id)}`, {}, `sessao-${id}`);
        },
        getConcessionarias: async function () {
            if (isMockMode()) return window.WatcherMockData.getConcessionarias();
            return fetchJson('/api/watcher/concessionarias', {}, 'concessionarias');
        },
        getArquivos: async function () {
            if (isMockMode()) return window.WatcherMockData.getArquivos();
            return fetchJson('/api/watcher/arquivos', {}, 'arquivos');
        },
        getAlertas: async function () {
            if (isMockMode()) return window.WatcherMockData.getAlertas();
            return fetchJson('/api/watcher/alertas', {}, 'alertas');
        },
        getLogs: async function (filters) {
            if (isMockMode()) return window.WatcherMockData.getLogs(filters);
            const query = buildQuery(filters);
            return fetchJson(`/api/watcher/logs?${query}`, {}, 'logs');
        },
        reconhecerAlerta: async function (alertaId, observacao) {
            if (isMockMode()) return window.WatcherMockData.reconhecerAlerta(alertaId, { observacao });
            return fetchJson(`/api/watcher/alertas/${encodeURIComponent(alertaId)}/reconhecer`, {
                method: 'POST',
                body: JSON.stringify({ observacao }),
            }, `reconhecer-${alertaId}`);
        },
        adicionarObservacao: async function (payload) {
            if (isMockMode()) return window.WatcherMockData.adicionarObservacao(payload);
            return fetchJson('/api/watcher/observacoes', {
                method: 'POST',
                body: JSON.stringify(payload),
            }, 'observacao');
        },
    };

    const WatcherUI = {
        renderResumo: function () {
            const payload = state.resumo.payload || {};
            const disponivel = payload.disponivel !== false;
            const stale = payload.dados_obsoletos === true;
            setText('watcherLastUpdated', payload.ultima_atualizacao ? formatDateTime(payload.ultima_atualizacao) : 'NÃ£o informado');
            setText('watcherConnectionStatus', disponivel ? 'DisponÃ­vel' : 'IndisponÃ­vel');
            safeToggleClass(elements.watcherStaleIndicator, 'hidden', !stale);
            if (!disponivel) {
                WatcherUI.renderOverviewMessage(payload.erro || 'Componente indisponÃ­vel', 'warning');
            } else {
                if (payload.erro) {
                    WatcherUI.renderOverviewMessage(payload.erro, 'warning');
                } else {
                    WatcherUI.renderOverviewMessage('');
                }
                setPageNotice('', 'info');
            }
        },
        renderTarefa: function () {
            const payload = state.tarefa.payload || {};
            setText('tarefaEstado', payload.estado || 'NÃ£o informado');
            setText('tarefaUltimaExecucao', formatDateTime(payload.ultima_execucao));
            setText('tarefaProximaExecucao', formatDateTime(payload.proxima_execucao));
            setText('tarefaUltimoResultado', payload.ultimo_resultado || 'NÃ£o informado');
            setText('tarefaUsuario', payload.usuario || 'NÃ£o informado');
            setText('tarefaModoExecucao', payload.modo_execucao || 'NÃ£o informado');
            setText('tarefaComando', payload.comando || 'NÃ£o informado');
            setText('tarefaDisponibilidade', payload.disponivel === false ? 'IndisponÃ­vel' : 'Real');
        },
        renderWatcherCard: function () {
            const payload = state.resumo.payload || {};
            const watcher = payload.watcher || {};
            setText('watcherStatus', watcher.status || 'NÃ£o informado');
            setText('watcherUltimaAtividade', formatDateTime(watcher.ultima_atividade));
            setText('watcherUltimoInicio', formatDateTime(watcher.ultimo_inicio));
            setText('watcherUltimoFim', formatDateTime(watcher.ultimo_fim));
            setText('watcherProximaExecucaoWatcher', formatDateTime(watcher.proxima_execucao));
            setText('watcherFonteProxima', watcher.fonte_proxima || 'NÃ£o informado');
            setText('watcherLock', watcher.lock || 'NÃ£o informado');
            setText('watcherPid', watcher.pid != null ? watcher.pid : 'Livre');
            setText('watcherLockAge', watcher.idade_lock != null ? formatAge(watcher.idade_lock / 60) : 'NÃ£o informado');
        },
        renderPipelines: function () {
            const payload = state.resumo.payload || {};
            const pipelines = payload.pipelines || {};
            setText('pipelinesSessoesAtivas', pipelines.sessoes_ativas ?? 'NÃƒÂ£o informado');
            setText('pipelinesSessoesPendentes', pipelines.sessoes_pendentes ?? 'NÃƒÂ£o informado');
            setText('pipelinesSessoesInterrompidas', pipelines.sessoes_interrompidas ?? 'NÃƒÂ£o informado');
            setText('pipelinesSessoesRetomaveis', pipelines.sessoes_retornaveis ?? pipelines.sessoes_retomaveis ?? 'NÃƒÂ£o informado');
            setText('pipelinesSessoesTotais', pipelines.sessoes_totais ?? 'NÃƒÂ£o informado');
            setText('pipelinesPdfsEntrada', pipelines.pdfs_entrada ?? 'NÃƒÂ£o informado');
            setText('pipelinesPdfsStaging', pipelines.pdfs_staging ?? 'NÃƒÂ£o informado');
            setText('pipelinesPdfsParadosEntrada', pipelines.pdfs_parados_entrada ?? 'NÃƒÂ£o informado');
            setText('pipelinesPdfsParadosStaging', pipelines.pdfs_parados_staging ?? 'NÃƒÂ£o informado');
            setText('pipelinesAlertasCriticos', pipelines.alertas_criticos ?? 'NÃƒÂ£o informado');
            setText('pipelinesAlertasAtencao', pipelines.alertas_atencao ?? 'NÃƒÂ£o informado');
            setText('pipelinesAlertasNaoReconhecidos', pipelines.alertas_nao_reconhecidos ?? 'NÃƒÂ£o informado');
        },
        renderOverviewMessage: function (message, type) {
            if (!elements.overviewMessage) return;
            elements.overviewMessage.textContent = message || '';
            elements.overviewMessage.className = message ? `watcher-panel-message watcher-panel-message-${type || 'info'}` : 'watcher-panel-message';
        },
        renderSessoes: function () {
            clearChildren(elements.sessionsBody);
            const rows = state.sessoes.payload || [];
            if (!rows.length) {
                const emptyRow = createElement('tr', { className: 'empty-row' });
                emptyRow.appendChild(createElement('td', { attrs: { colspan: '12' }, text: 'Nenhuma sessÃƒÂ£o encontrada.' }));
                elements.sessionsBody.appendChild(emptyRow);
                return;
            }
            rows.forEach((session) => {
                const tr = createElement('tr');
                tr.tabIndex = 0;
                tr.dataset.sessionId = session.session_id || '';
                tr.addEventListener('click', () => showSessionDetail(session.session_id));
                tr.addEventListener('keydown', (event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        showSessionDetail(session.session_id);
                    }
                });

                tr.appendChild(createElement('td', { text: session.session_id || 'NÃƒÂ£o informado' }));
                tr.appendChild(createElement('td', { text: session.concessionaria || 'NÃƒÂ£o informado' }));
                tr.appendChild(createElement('td', { text: session.grupo || 'NÃƒÂ£o informado' }));
                tr.appendChild(createElement('td', { text: session.referencia || 'NÃƒÂ£o informado' }));
                tr.appendChild(createElement('td', { text: session.pdfs != null ? String(session.pdfs) : 'NÃƒÂ£o informado' }));
                tr.appendChild(createElement('td', { html: '', attrs: { role: 'cell' } })).appendChild(createBadge(session.execucao_status || 'nÃƒÂ£o informado'));
                tr.appendChild(createElement('td', { html: '', attrs: { role: 'cell' } })).appendChild(createBadge(session.reconciliacao_status || 'nÃƒÂ£o informado', 'reconciliacao'));
                tr.appendChild(createElement('td', { text: session.etapa_atual || 'NÃƒÂ£o informado' }));
                tr.appendChild(createElement('td', { text: formatDateTime(session.inicio || session.created_at || session.criado_em) }));
                tr.appendChild(createElement('td', { text: formatDateTime(session.fim || session.updated_at || session.atualizado_em) }));
                tr.appendChild(createElement('td', { text: session.retomavel ? 'Sim' : 'NÃ£o' }));
                tr.appendChild(createElement('td', { text: formatDateTime(session.atualizacao) }));

                elements.sessionsBody.appendChild(tr);
            });
        },
        renderSessionsMessage: function (message, type) {
            if (!elements.sessionsMessage) return;
            elements.sessionsMessage.textContent = message || '';
            elements.sessionsMessage.className = message ? `watcher-panel-message watcher-panel-message-${type || 'info'}` : 'watcher-panel-message';
        },
        renderArquivos: function () {
            clearChildren(elements.entradaFilesBody);
            clearChildren(elements.stagingFilesBody);
            const data = state.arquivos.payload || { entrada: [], staging: [] };
            const entrada = data.entrada || [];
            const staging = data.staging || [];
            if (!entrada.length) {
                const row = createElement('tr');
                row.appendChild(createElement('td', { attrs: { colspan: '9' }, text: 'Nenhum PDF parado encontrado.' }));
                elements.entradaFilesBody.appendChild(row);
            } else {
                entrada.forEach((item) => elements.entradaFilesBody.appendChild(createFileRow(item)));
            }
            if (!staging.length) {
                const row = createElement('tr');
                row.appendChild(createElement('td', { attrs: { colspan: '9' }, text: 'Nenhum PDF parado encontrado.' }));
                elements.stagingFilesBody.appendChild(row);
            } else {
                staging.forEach((item) => elements.stagingFilesBody.appendChild(createFileRow(item)));
            }
        },
        renderFilesMessage: function (message, type) {
            if (!elements.filesMessage) return;
            elements.filesMessage.textContent = message || '';
            elements.filesMessage.className = message ? `watcher-panel-message watcher-panel-message-${type || 'info'}` : 'watcher-panel-message';
        },
        renderAlertas: function () {
            clearChildren(elements.alertList);
            const alerts = state.alertas.payload || [];
            if (!alerts.length) {
                elements.alertList.appendChild(createElement('div', { className: 'watcher-empty-state', text: 'Nenhum alerta disponÃƒÂ­vel.' }));
                return;
            }
            alerts.forEach((alerta) => {
                const card = createElement('article', { className: 'watcher-alert-card' });
                card.dataset.alertId = alerta.id || '';
                card.appendChild(createElement('div', { className: 'watcher-alert-level', text: alerta.nivel || 'NÃƒÂ£o informado' }));
                card.appendChild(createElement('h3', { text: alerta.titulo || 'Sem tÃƒÂ­tulo' }));
                card.appendChild(createElement('p', { className: 'watcher-alert-reason', text: alerta.motivo || 'Sem motivo informado' }));

                const meta = createElement('div', { className: 'watcher-alert-meta' });
                meta.appendChild(createMetaPair('SessÃƒÂ£o', alerta.session_id || 'NÃƒÂ£o informado'));
                meta.appendChild(createMetaPair('Carimbo', alerta.carimbo || 'NÃƒÂ£o informado'));
                meta.appendChild(createMetaPair('Arquivo', alerta.arquivo || 'NÃƒÂ£o informado'));
                meta.appendChild(createMetaPair('HorÃƒÂ¡rio do problema', formatDateTime(alerta.horario_problema)));
                meta.appendChild(createMetaPair('HorÃƒÂ¡rio de detecÃƒÂ§ÃƒÂ£o', formatDateTime(alerta.horario_deteccao)));
                meta.appendChild(createMetaPair('AÃƒÂ§ÃƒÂ£o recomendada', alerta.acao_recomendada || 'NÃƒÂ£o informado'));
                meta.appendChild(createMetaPair('Reconhecido', alerta.reconhecido ? 'Sim' : 'NÃƒÂ£o'));
                card.appendChild(meta);

                const actions = createElement('div', { className: 'watcher-alert-actions' });
                const recognizeBtn = createElement('button', { className: 'btn btn-secondary watcher-alert-action', text: 'Reconhecer', attrs: { type: 'button', 'data-action': 'recognize', 'data-alert-id': alerta.id } } );
                const noteBtn = createElement('button', { className: 'btn btn-ghost watcher-alert-action', text: 'Adicionar observaÃƒÂ§ÃƒÂ£o', attrs: { type: 'button', 'data-action': 'note', 'data-alert-id': alerta.id } });
                const sessionBtn = createElement('button', { className: 'btn btn-ghost watcher-alert-action', text: 'Abrir sessÃƒÂ£o', attrs: { type: 'button', 'data-action': 'open-session', 'data-session-id': alerta.session_id } });
                const logsBtn = createElement('button', { className: 'btn btn-ghost watcher-alert-action', text: 'Filtrar logs', attrs: { type: 'button', 'data-action': 'filter-logs', 'data-session-id': alerta.session_id, 'data-carimbo': alerta.carimbo, 'data-concessionaria': alerta.concessionaria } });
                actions.appendChild(recognizeBtn);
                actions.appendChild(noteBtn);
                actions.appendChild(sessionBtn);
                actions.appendChild(logsBtn);
                card.appendChild(actions);
                elements.alertList.appendChild(card);
            });
        },
        renderAlertCounts: function () {
            const alerts = state.alertas.payload || [];
            const totals = alerts.reduce((acc, alerta) => {
                const level = String(alerta.nivel || 'informativo').toLowerCase();
                if (level.includes('crÃƒÂ­tico') || level.includes('critico')) acc.criticos += 1;
                else if (level.includes('atenÃƒÂ§ÃƒÂ£o') || level.includes('atencao')) acc.atencao += 1;
                else acc.informativos += 1;
                if (!alerta.reconhecido) acc.naoReconhecidos += 1;
                return acc;
            }, { criticos: 0, atencao: 0, informativos: 0, naoReconhecidos: 0 });
            setText('alertsCountCriticos', totals.criticos);
            setText('alertsCountAtencao', totals.atencao);
            setText('alertsCountInformativos', totals.informativos);
            setText('alertsCountNaoReconhecidos', totals.naoReconhecidos);
        },
        renderSessaoDetalhe: async function () {
            clearChildren(elements.sessionDetail);
            const sessionId = state.sessaoSelecionada;
            if (!sessionId) {
                elements.sessionDetail.appendChild(createElement('div', { className: 'watcher-empty-state', text: 'Selecione uma sessÃ£o para abrir o painel de detalhes.' }));
                return;
            }
            const session = state.sessoes.payload.find((item) => item.session_id === sessionId) || await WatcherAPI.getSessao(sessionId);
            if (!session) {
                elements.sessionDetail.appendChild(createElement('div', { className: 'watcher-error-state', text: 'Detalhes da sessÃ£o nÃ£o encontrados.' }));
                return;
            }
            const meta = createElement('div', { className: 'watcher-detail-meta' });
            meta.appendChild(createMetaPair('Session ID', session.session_id));
            meta.appendChild(createMetaPair('ConcessionÃ¡ria', session.concessionaria));
            meta.appendChild(createMetaPair('Grupo', session.grupo));
            meta.appendChild(createMetaPair('ReferÃªncia', session.referencia));
            meta.appendChild(createMetaPair('Data de inÃ­cio', formatDateTime(session.inicio || session.created_at || session.criado_em)));
            meta.appendChild(createMetaPair('Ãšltima atualizaÃ§Ã£o', formatDateTime(session.fim || session.updated_at || session.atualizado_em)));
            meta.appendChild(createMetaPair('DuraÃ§Ã£o', session.duracao_s != null ? formatDuration(session.duracao_s) : 'NÃ£o informado'));
            meta.appendChild(createMetaPair('Etapa atual', session.etapa_atual));
            meta.appendChild(createMetaPair('Status da execuÃ§Ã£o', session.execucao_status));
            meta.appendChild(createMetaPair('Status da reconciliaÃ§Ã£o', session.reconciliacao_status));
            meta.appendChild(createMetaPair('RetomÃ¡vel', session.retomavel ? 'Sim' : 'NÃ£o'));
            meta.appendChild(createMetaPair('Motivo da parada', session.motivo_parada));
            meta.appendChild(createMetaPair('Return code', session.return_code != null ? String(session.return_code) : 'NÃ£o informado'));
            meta.appendChild(createMetaPair('Qtd. PDFs', session.quantidade_pdfs != null ? String(session.quantidade_pdfs) : String(session.pdfs ?? 'NÃ£o informado')));
            meta.appendChild(createMetaPair('Qtd. concluÃ­dos', session.quantidade_concluidos != null ? String(session.quantidade_concluidos) : 'NÃ£o informado'));
            meta.appendChild(createMetaPair('Qtd. pendentes', session.quantidade_pendentes != null ? String(session.quantidade_pendentes) : 'NÃ£o informado'));
            elements.sessionDetail.appendChild(meta);

            if (session.timeline && session.timeline.length) {
                const timeline = createElement('div', { className: 'watcher-timeline' });
                timeline.appendChild(createElement('h3', { text: 'Linha do tempo' }));
                session.timeline.forEach((step) => {
                    const entry = createElement('div', { className: 'watcher-timeline-entry' });
                    entry.appendChild(createElement('strong', { text: step.label || step.etapa || 'NÃ£o informado' }));
                    entry.appendChild(createElement('span', { className: `watcher-timeline-status watcher-timeline-status-${String(step.status || 'nao informada').toLowerCase().replace(/\s+/g, '-')}`, text: step.status || 'NÃ£o informada' }));
                    entry.appendChild(createElement('small', { text: formatDateTime(step.timestamp || step.fim || step.inicio) }));
                    if (step.quantidade != null) {
                        entry.appendChild(createElement('small', { text: `Quantidade: ${step.quantidade}` }));
                    }
                    timeline.appendChild(entry);
                });
                elements.sessionDetail.appendChild(timeline);
            }

            if (session.arquivos && session.arquivos.length) {
                const tableWrap = createElement('div', { className: 'watcher-table-wrap' });
                const table = createElement('table', { className: 'watcher-table' });
                const thead = createElement('thead');
                thead.appendChild(createElement('tr')).append(...[
                    'Original', 'BB', 'Carimbo', 'InstalaÃ§Ã£o', 'ReferÃªncia', 'Grupo', 'Status', 'Ãšltima etapa', 'LocalizaÃ§Ã£o', 'Destino', 'Erro',
                ].map((text) => createElement('th', { text })));
                table.appendChild(thead);
                const tbody = createElement('tbody');
                session.arquivos.forEach((file) => {
                    const row = createElement('tr');
                    row.appendChild(createElement('td', { text: file.arquivo_original || file.nome_original || file.arquivo || 'NÃ£o informado' }));
                    row.appendChild(createElement('td', { text: file.arquivo_bb || file.nome_carimbado || 'NÃ£o informado' }));
                    row.appendChild(createElement('td', { text: file.carimbo || 'NÃ£o informado' }));
                    row.appendChild(createElement('td', { text: file.instalacao || 'NÃ£o informado' }));
                    row.appendChild(createElement('td', { text: file.referencia || 'NÃ£o informado' }));
                    row.appendChild(createElement('td', { text: file.grupo || 'NÃ£o informado' }));
                    row.appendChild(createElement('td', { text: file.status || 'NÃ£o informado' }));
                    row.appendChild(createElement('td', { text: file.ultima_etapa || 'NÃ£o informado' }));
                    row.appendChild(createElement('td', { text: file.localizacao || 'NÃ£o informado' }));
                    row.appendChild(createElement('td', { text: file.destino || 'NÃ£o informado' }));
                    row.appendChild(createElement('td', { text: file.erro || 'NÃ£o informado' }));
                    tbody.appendChild(row);
                });
                table.appendChild(tbody);
                elements.sessionDetail.appendChild(createElement('div', { className: 'watcher-detail-table' })).appendChild(tableWrap).appendChild(table);
            }

            if (session.fontes_divergentes && Array.isArray(session.detalhes_fontes) && session.detalhes_fontes.length) {
                const divergence = createElement('div', { className: 'watcher-divergence' });
                divergence.appendChild(createElement('h3', { text: 'DivergÃªncia detectada' }));
                session.detalhes_fontes.forEach((item) => {
                    const detail = createElement('div', { className: 'watcher-divergence-item' });
                    detail.appendChild(createMetaPair('Ãndice', item.indice));
                    detail.appendChild(createMetaPair('SessÃ£o', item.sessao));
                    detail.appendChild(createMetaPair('LocalizaÃ§Ã£o fÃ­sica', item.localizacao));
                    detail.appendChild(createMetaPair('Motivo', item.motivo));
                    divergence.appendChild(detail);
                });
                elements.sessionDetail.appendChild(divergence);
            }
        },

        renderLogs: function (payload) {
            const text = payload?.texto || payload?.log || payload?.logs || '';
            elements.logText.textContent = text || 'Nenhum log carregado.';
            const total = payload?.total != null ? String(payload.total) : 'Ã¢â‚¬â€';
            const offset = payload?.offset != null ? String(payload.offset) : String(state.filtrosLogs.offset);
            elements.logMeta.textContent = `Total conhecido: ${total}`;
            elements.logOffset.textContent = `Offset: ${offset}`;
        },
        notify: function (message, type = 'info') {
            if (typeof showToast === 'function') {
                showToast(message, type);
                return;
            }
            const alert = createElement('div', { className: `watcher-toast watcher-toast-${type}`, text: message });
            document.body.appendChild(alert);
            setTimeout(() => alert.remove(), 3200);
        },
    };

    function createBadge(text, variant = 'execucao') {
        const span = createElement('span', { className: 'watcher-badge', text: safeText(text) });
        if (variant === 'reconciliacao') {
            span.classList.add('watcher-badge-secondary');
        }
        return span;
    }

    function createMetaPair(label, value) {
        const wrapper = createElement('div', { className: 'watcher-meta-pair' });
        wrapper.appendChild(createElement('span', { className: 'watcher-meta-label', text: label }));
        wrapper.appendChild(createElement('strong', { className: 'watcher-meta-value', text: safeText(value) }));
        return wrapper;
    }

    function createFileRow(item) {
        const row = createElement('tr');
        row.appendChild(createElement('td', { text: item.arquivo || 'NÃƒÂ£o informado' }));
        row.appendChild(createElement('td', { text: item.concessionaria || 'NÃƒÂ£o informado' }));
        row.appendChild(createElement('td', { text: item.tipo || 'NÃƒÂ£o informado' }));
        row.appendChild(createElement('td', { text: formatAge(item.idade_minutos) }));
        row.appendChild(createElement('td', { text: item.ciclos != null ? String(item.ciclos) : 'NÃƒÂ£o informado' }));
        row.appendChild(createElement('td', { text: formatFileSize(item.tamanho_bytes) }));
        row.appendChild(createElement('td', { text: item.prefixo_bb ? 'Sim' : 'NÃƒÂ£o' }));
        row.appendChild(createElement('td', { text: item.nivel || 'NÃƒÂ£o informado' }));
        const pathCell = createElement('td');
        const pathButton = createElement('button', { className: 'btn btn-ghost btn-copy-path', text: 'Copiar caminho', attrs: { type: 'button' } });
        pathButton.addEventListener('click', () => {
            navigator.clipboard?.writeText(item.caminho || '').then(() => WatcherUI.notify('Caminho copiado.'), () => WatcherUI.notify('NÃƒÂ£o foi possÃƒÂ­vel copiar.', 'error'));
        });
        const pathText = createElement('div', { className: 'watcher-file-path', text: item.caminho || 'NÃƒÂ£o informado' });
        pathCell.appendChild(pathText);
        pathCell.appendChild(pathButton);
        row.appendChild(pathCell);
        return row;
    }

    function setActiveTab(tabKey) {
        state.abaAtiva = tabKey;
        elements.tabs.forEach((button) => {
            const active = button.dataset.tab === tabKey;
            button.setAttribute('aria-selected', active ? 'true' : 'false');
            button.classList.toggle('active', active);
        });
        elements.panels.forEach((panel) => {
            panel.classList.toggle('active', panel.id === `tab-${tabKey}`);
        });
        ensureTabPolling();
        loadTabData(tabKey).catch((error) => {
            WatcherUI.notify(error.message, 'error');
        });
    }

    async function loadTabData(tabKey) {
        if (tabKey === 'overview') {
            await refreshResumo();
            await refreshTarefa();
        }
        if (tabKey === 'sessions') {
            await refreshConcessionarias();
            await refreshSessoes();
        }
        if (tabKey === 'files') {
            await refreshArquivos();
        }
        if (tabKey === 'alerts') {
            await refreshAlertas();
        }
    }

    function ensureTabPolling() {
        clearInterval(state.timers.sessoes);
        clearInterval(state.timers.arquivos);
        state.timers.sessoes = null;
        state.timers.arquivos = null;
        if (document.hidden) return;
        if (state.abaAtiva === 'sessions') {
            state.timers.sessoes = setInterval(() => refreshSessoes().catch(() => {}), 60000);
        }
        if (state.abaAtiva === 'files') {
            state.timers.arquivos = setInterval(() => refreshArquivos().catch(() => {}), 60000);
        }
    }

    function startMainPolling() {
        state.timers.resumo = setInterval(() => refreshResumo().catch(() => {}), 30000);
        state.timers.alertas = setInterval(() => refreshAlertas().catch(() => {}), 30000);
    }

    function stopAllPolling() {
        Object.keys(state.timers).forEach((key) => {
            if (state.timers[key]) {
                clearInterval(state.timers[key]);
                state.timers[key] = null;
            }
        });
    }

    async function refreshResumo() {
        state.resumo.carregando = true;
        state.resumo.erro = null;
        try {
            const payload = await WatcherAPI.getResumo();
            state.resumo.payload = payload;
            state.ultimaAtualizacao = payload.ultima_atualizacao || state.ultimaAtualizacao;
            WatcherUI.renderResumo();
            if (state.resumo.payload.disponivel === false) {
                WatcherUI.renderOverviewMessage(state.resumo.payload.erro || 'Componente indisponÃƒÂ­vel', 'warning');
            }
        } catch (error) {
            dispatcherError(error, 'resumo');
            WatcherUI.renderOverviewMessage(error.message, 'error');
        } finally {
            state.resumo.carregando = false;
        }
    }

    async function refreshTarefa() {
        state.tarefa.carregando = true;
        state.tarefa.erro = null;
        try {
            state.tarefa.payload = await WatcherAPI.getTarefa();
            WatcherUI.renderTarefa();
        } catch (error) {
            dispatcherError(error, 'tarefa');
        } finally {
            state.tarefa.carregando = false;
            WatcherUI.renderTarefa();
        }
    }

    async function refreshConcessionarias() {
        try {
            const items = await WatcherAPI.getConcessionarias();
            state.concessionarias = Array.isArray(items) ? items : [];
            populateConcessionariaFilter();
        } catch (error) {
            dispatcherError(error, 'concessionarias');
        }
    }

    function populateConcessionariaFilter() {
        if (!elements.sessionsConcessionaria) return;
        clearChildren(elements.sessionsConcessionaria);
        elements.sessionsConcessionaria.appendChild(createElement('option', { attrs: { value: '' }, text: 'Todas' }));
        state.concessionarias.forEach((item) => {
            elements.sessionsConcessionaria.appendChild(createElement('option', { attrs: { value: item }, text: item }));
        });
        elements.sessionsConcessionaria.value = state.filtrosSessoes.concessionaria;
    }

    async function refreshSessoes() {
        state.sessoes.carregando = true;
        state.sessoes.erro = null;
        WatcherUI.renderSessionsMessage('Carregando sessÃƒÂµes...', 'info');
        try {
            const payload = await WatcherAPI.getSessoes(state.filtrosSessoes);
            const normalized = normalizeSessionsPayload(payload);
            state.sessoes.payload = normalized.sessoes;
            state.sessoes.total = normalized.total;
            state.sessoes.meta = normalized.meta;
            WatcherUI.renderSessoes();
            WatcherUI.renderSessionsMessage(normalized.total ? `Mostrando ${normalized.total} sessões.` : '');
        } catch (error) {
            dispatcherError(error, 'sessoes');
            WatcherUI.renderSessionsMessage(error.message, 'error');
        } finally {
            state.sessoes.carregando = false;
        }
    }

    async function refreshArquivos() {
        state.arquivos.carregando = true;
        state.arquivos.erro = null;
        WatcherUI.renderFilesMessage('Carregando arquivos...', 'info');
        try {
            const payload = await WatcherAPI.getArquivos();
            state.arquivos.payload = payload || { entrada: [], staging: [] };
            WatcherUI.renderArquivos();
            WatcherUI.renderFilesMessage('');
        } catch (error) {
            dispatcherError(error, 'arquivos');
            WatcherUI.renderFilesMessage(error.message, 'error');
        } finally {
            state.arquivos.carregando = false;
        }
    }

    async function refreshAlertas() {
        state.alertas.carregando = true;
        state.alertas.erro = null;
        try {
            const payload = await WatcherAPI.getAlertas();
            state.alertas.payload = Array.isArray(payload) ? payload : [];
            WatcherUI.renderAlertCounts();
            WatcherUI.renderAlertas();
        } catch (error) {
            dispatcherError(error, 'alertas');
            WatcherUI.notify(error.message, 'error');
        } finally {
            state.alertas.carregando = false;
        }
    }

    async function refreshLogs() {
        try {
            const payload = await WatcherAPI.getLogs(state.filtrosLogs);
            if (payload) {
                state.filtrosLogs.offset = payload.offset || state.filtrosLogs.offset;
                state.filtrosLogs.limit = payload.limit || state.filtrosLogs.limit;
                WatcherUI.renderLogs(payload);
            }
        } catch (error) {
            dispatcherError(error, 'logs');
            elements.logText.textContent = `Erro ao carregar logs: ${error.message}`;
        }
    }

    function openDetailDrawer() {
        if (elements.detailColumn) {
            elements.detailColumn.classList.remove('hidden');
        }
        if (!elements.detailDrawer) return;
        elements.detailDrawer.classList.remove('hidden');
        elements.detailDrawer.classList.add('open');
    }

    function closeDetailDrawer() {
        if (!elements.detailDrawer) return;
        elements.detailDrawer.classList.remove('open');
        elements.detailDrawer.classList.add('hidden');
        elements.detailColumn?.classList.add('hidden');
        state.sessaoSelecionada = null;
    }

    function showSessionDetail(sessionId) {
        state.sessaoSelecionada = sessionId;
        openDetailDrawer();
        WatcherUI.renderSessaoDetalhe();
    }

    function setPageNotice(message, type = 'info') {
        if (!elements.watcherNoticeRow) return;
        elements.watcherNoticeRow.textContent = message || '';
        elements.watcherNoticeRow.className = message ? `watcher-notice-row watcher-notice-${type}` : 'watcher-notice-row';
    }

    function openModal(modal) {
        if (!modal) return;
        modal.classList.remove('hidden');
        const firstInput = modal.querySelector('textarea, input, button');
        firstInput?.focus();
        document.body.style.overflow = 'hidden';
    }

    function closeModal(modal) {
        if (!modal) return;
        modal.classList.add('hidden');
        document.body.style.overflow = '';
    }

    function handleActionModalClose() {
        closeModal(elements.actionModal);
        elements.actionForm.reset();
    }

    function handleLogModalClose() {
        closeModal(elements.logModal);
    }

    async function handleRecognizeAlert(alertId) {
        const alerta = state.alertas.payload.find((item) => item.id === alertId);
        if (!alerta) return;
        elements.actionTitle.textContent = 'Reconhecer alerta';
        elements.actionMessage.textContent = `Deseja reconhecer o alerta ${alerta.id}? Adicione uma observaÃƒÂ§ÃƒÂ£o opcional.`;
        elements.actionNote.value = '';
        elements.actionForm.dataset.mode = 'recognize';
        elements.actionForm.dataset.alertId = alertId;
        openModal(elements.actionModal);
    }

    async function handleNoteAlert(alertId) {
        const alerta = state.alertas.payload.find((item) => item.id === alertId);
        if (!alerta) return;
        elements.actionTitle.textContent = 'Adicionar observaÃƒÂ§ÃƒÂ£o';
        elements.actionMessage.textContent = `Adicione uma observaÃƒÂ§ÃƒÂ£o para o alerta ${alerta.id}.`; 
        elements.actionNote.value = '';
        elements.actionForm.dataset.mode = 'note';
        elements.actionForm.dataset.alertId = alertId;
        openModal(elements.actionModal);
    }

    function handleOpenSession(sessionId) {
        if (!sessionId) return;
        setActiveTab('sessions');
        state.filtrosSessoes.session_id = sessionId;
        state.filtrosSessoes.q = sessionId;
        if (elements.sessionsSearch) elements.sessionsSearch.value = sessionId;
        state.sessaoSelecionada = sessionId;
        refreshSessoes();
        WatcherUI.renderSessaoDetalhe();
    }

    function handleFilterLogs(sessionId, carimbo, concessionaria) {
        state.filtrosLogs.session_id = sessionId || '';
        state.filtrosLogs.carimbo = carimbo || '';
        state.filtrosLogs.concessionaria = concessionaria || '';
        state.filtrosLogs.offset = 0;
        elements.logFilterSession.value = state.filtrosLogs.session_id;
        elements.logFilterCarimbo.value = state.filtrosLogs.carimbo;
        elements.logFilterConcessionaria.value = state.filtrosLogs.concessionaria;
        elements.logFilterNivel.value = state.filtrosLogs.nivel;
        elements.logFilterLimit.value = String(state.filtrosLogs.limit);
        openModal(elements.logModal);
        refreshLogs();
    }

    function attachEvents() {
        elements.watcherRefreshBtns.forEach((button) => {
            button.addEventListener('click', () => {
                refreshCurrentTab();
            });
        });

        elements.tabs.forEach((button) => {
            button.addEventListener('click', () => setActiveTab(button.dataset.tab));
        });

        if (elements.detailClose) {
            elements.detailClose.addEventListener('click', closeDetailDrawer);
        }

        if (elements.sessionsForm) {
            elements.sessionsForm.addEventListener('submit', (event) => {
                event.preventDefault();
                state.filtrosSessoes = {
                    status: elements.sessionsStatus.value,
                    concessionaria: elements.sessionsConcessionaria.value,
                    grupo: elements.sessionsGrupo.value.trim(),
                    referencia: elements.sessionsReferencia.value.trim(),
                    q: elements.sessionsSearch?.value.trim() || '',
                    session_id: '',
                };
                refreshSessoes();
            });
        }

        if (elements.sessionsClear) {
            elements.sessionsClear.addEventListener('click', () => {
                state.filtrosSessoes = { status: 'todas', concessionaria: '', grupo: '', referencia: '', session_id: '', q: '' };
                elements.sessionsStatus.value = 'todas';
                elements.sessionsConcessionaria.value = '';
                elements.sessionsGrupo.value = '';
                elements.sessionsReferencia.value = '';
                if (elements.sessionsSearch) elements.sessionsSearch.value = '';
                refreshSessoes();
            });
        }

        if (elements.actionCancel) elements.actionCancel.addEventListener('click', handleActionModalClose);
        if (elements.actionCancelSecondary) elements.actionCancelSecondary.addEventListener('click', handleActionModalClose);
        if (elements.actionForm) {
            elements.actionForm.addEventListener('submit', async (event) => {
                event.preventDefault();
                const mode = elements.actionForm.dataset.mode;
                const alertId = elements.actionForm.dataset.alertId;
                const observacao = elements.actionNote.value.trim();
                if (mode === 'recognize') {
                    try {
                        await WatcherAPI.reconhecerAlerta(alertId, observacao);
                        const alerta = state.alertas.payload.find((item) => item.id === alertId);
                        if (alerta) alerta.reconhecido = true;
                        WatcherUI.renderAlertCounts();
                        WatcherUI.renderAlertas();
                        WatcherUI.notify('Alerta reconhecido com sucesso.', 'success');
                    } catch (error) {
                        WatcherUI.notify(error.message, 'error');
                    }
                }
                if (mode === 'note') {
                    const alerta = state.alertas.payload.find((item) => item.id === alertId);
                    const payload = { session_id: alerta?.session_id || '', arquivo: alerta?.arquivo || '', texto: observacao };
                    if (!observacao) {
                        WatcherUI.notify('ObservaÃƒÂ§ÃƒÂ£o nÃƒÂ£o pode ficar vazia.', 'error');
                        return;
                    }
                    try {
                        await WatcherAPI.adicionarObservacao(payload);
                        WatcherUI.notify('ObservaÃƒÂ§ÃƒÂ£o enviada com sucesso.', 'success');
                    } catch (error) {
                        WatcherUI.notify(error.message, 'error');
                    }
                }
                handleActionModalClose();
            });
        }

        if (elements.logClose) elements.logClose.addEventListener('click', handleLogModalClose);
        if (elements.logModal) {
            elements.logModal.querySelectorAll('[data-modal-close]').forEach((item) => {
                item.addEventListener('click', handleLogModalClose);
            });
        }
        if (elements.logForm) {
            elements.logForm.addEventListener('submit', (event) => {
                event.preventDefault();
                state.filtrosLogs.session_id = elements.logFilterSession.value.trim();
                state.filtrosLogs.carimbo = elements.logFilterCarimbo.value.trim();
                state.filtrosLogs.concessionaria = elements.logFilterConcessionaria.value.trim();
                state.filtrosLogs.nivel = elements.logFilterNivel.value.trim();
                state.filtrosLogs.limit = Number(elements.logFilterLimit.value) || 100;
                state.filtrosLogs.offset = 0;
                refreshLogs();
            });
        }
        if (elements.logClear) {
            elements.logClear.addEventListener('click', () => {
                state.filtrosLogs = { session_id: '', carimbo: '', concessionaria: '', nivel: '', limit: 100, offset: 0 };
                elements.logFilterSession.value = '';
                elements.logFilterCarimbo.value = '';
                elements.logFilterConcessionaria.value = '';
                elements.logFilterNivel.value = '';
                elements.logFilterLimit.value = '100';
                refreshLogs();
            });
        }
        if (elements.logPrev) {
            elements.logPrev.addEventListener('click', () => {
                state.filtrosLogs.offset = Math.max(0, state.filtrosLogs.offset - state.filtrosLogs.limit);
                refreshLogs();
            });
        }
        if (elements.logNext) {
            elements.logNext.addEventListener('click', () => {
                state.filtrosLogs.offset += state.filtrosLogs.limit;
                refreshLogs();
            });
        }
        if (elements.logCopy) {
            elements.logCopy.addEventListener('click', () => {
                const text = elements.logText.textContent || '';
                navigator.clipboard?.writeText(text).then(() => WatcherUI.notify('Trecho copiado.', 'success'), () => WatcherUI.notify('NÃƒÂ£o foi possÃƒÂ­vel copiar.', 'error'));
            });
        }

        root.addEventListener('click', (event) => {
            const action = event.target.closest('[data-action]');
            if (!action) return;
            const type = action.dataset.action;
            const alertId = action.dataset.alertId;
            const sessionId = action.dataset.sessionId;
            const carimbo = action.dataset.carimbo;
            const concessionaria = action.dataset.concessionaria;
            if (type === 'recognize') handleRecognizeAlert(alertId);
            if (type === 'note') handleNoteAlert(alertId);
            if (type === 'open-session') handleOpenSession(sessionId);
            if (type === 'filter-logs') handleFilterLogs(sessionId, carimbo, concessionaria);
        });
    }

    async function refreshCurrentTab() {
        await refreshResumo();
        if (state.abaAtiva === 'overview') {
            await refreshTarefa();
        }
        if (state.abaAtiva === 'sessions') {
            await refreshSessoes();
        }
        if (state.abaAtiva === 'files') {
            await refreshArquivos();
        }
        if (state.abaAtiva === 'alerts') {
            await refreshAlertas();
        }
    }

    function handleVisibilityChange() {
        if (document.hidden) {
            stopAllPolling();
        } else {
            refreshResumo().catch(() => {});
            refreshAlertas().catch(() => {});
            ensureTabPolling();
        }
    }

    function init() {
        if (PARAMS.get('tab')) {
            const tab = PARAMS.get('tab');
            if (['overview', 'sessions', 'files', 'alerts'].includes(tab)) {
                state.abaAtiva = tab;
            }
        }
        closeDetailDrawer();
        setActiveTab(state.abaAtiva);
        attachEvents();
        startMainPolling();
        document.addEventListener('visibilitychange', handleVisibilityChange);
        if (!isMockMode()) {
            setPageNotice('Conectando ÃƒÂ  API real.', 'info');
        } else {
            setPageNotice(`Modo mock ativado (${PARAMS.get('scenario') || 'operacional'}).`, 'info');
        }
    }

    init();
})();

