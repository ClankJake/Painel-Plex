// app/static/js/users.js

/**
 * Ponto de Entrada Principal para a Página de Usuários
 * * Este ficheiro orquestra a inicialização da página. Ele importa os módulos
 * necessários, configura os manipuladores de eventos para os elementos estáticos
 * da página (como a barra de pesquisa e os botões principais) e inicia o
 * carregamento inicial dos dados.
 */

import * as dom from './users_modules/dom.js';
import * as state from './users_modules/state.js';
import * as ui from './users_modules/ui.js';

// Intervalo entre verificações automáticas de convites (ms).
const INVITE_POLL_INTERVAL_MS = 10000;

// Tempo de espera antes de filtrar a grelha enquanto se escreve (ms). Sem isto,
// cada tecla reconstruía todos os cartões — bem visível em servidores grandes.
const SEARCH_DEBOUNCE_MS = 200;

document.addEventListener('DOMContentLoaded', () => {

    // MELHORIA: Limpa o termo de pesquisa guardado sempre que a página é carregada
    // para evitar que um filtro antigo seja aplicado ao voltar para a página.
    state.setViewState({ searchTerm: '' });

    /**
     * Mantém os atributos ARIA das abas em sincronia com o estado visual, para
     * que um leitor de ecrã anuncie qual o filtro selecionado.
     */
    function markActiveTab(activeButton) {
        if (!dom.userTabs) return;
        dom.userTabs.querySelectorAll('.user-tab').forEach(tab => {
            const isActive = tab === activeButton;
            tab.classList.toggle('active', isActive);
            tab.setAttribute('aria-selected', String(isActive));
        });
    }

    /**
     * Inicializa todos os manipuladores de eventos para os elementos estáticos da página.
     */
    function initializeEventListeners() {
        if (dom.createInviteButton) {
            dom.createInviteButton.addEventListener('click', ui.showCreateInviteModal);
        }
        if (dom.refreshButton) {
            dom.refreshButton.addEventListener('click', () => ui.loadStatus(true));
        }
        if (dom.bulkActionsButton) {
            dom.bulkActionsButton.addEventListener('click', ui.showBulkActionsModal);
        }
        if (dom.searchInput) {
            let searchDebounceId = null;
            dom.searchInput.addEventListener('input', (e) => {
                // 🐛 CORREÇÃO: o termo era passado por uma função de escape de HTML
                // antes de ser guardado. Como só é usado em `String.includes` (nunca
                // injetado no DOM), o escape apenas corrompia a pesquisa: procurar
                // por "R&B" tornava-se "R&amp;B" e nunca encontrava nada.
                const searchTerm = e.target.value;
                clearTimeout(searchDebounceId);
                searchDebounceId = setTimeout(() => {
                    state.setViewState({ searchTerm });
                    ui.renderUserGrid();
                }, SEARCH_DEBOUNCE_MS);
            });
        }
        if (dom.sortSelect) {
            dom.sortSelect.addEventListener('change', (e) => {
                state.setViewState({ sortBy: e.target.value });
                ui.renderUserGrid();
            });
        }
        if (dom.userTabs) {
            dom.userTabs.addEventListener('click', (e) => {
                const button = e.target.closest('button');
                if (button && button.dataset.filter) {
                    markActiveTab(button);
                    state.setViewState({ filter: button.dataset.filter });
                    ui.renderUserGrid();
                }
            });
        }

        // **NOVO**: Listeners para as abas de convites
        if (dom.inviteTabActive) {
            dom.inviteTabActive.addEventListener('click', () => ui.handleInviteTabChange('active'));
        }
        if (dom.inviteTabHistory) {
            dom.inviteTabHistory.addEventListener('click', () => ui.handleInviteTabChange('history'));
        }

        // Listener para o evento de atualização de dados
        document.addEventListener('data-refresh-requested', () => {
            ui.loadStatus(true);
        });
    }

    /**
     * Configura a visualização inicial com base no estado guardado.
     */
    function initializeView() {
        if (dom.sortSelect) {
            dom.sortSelect.value = state.viewState.sortBy;
        }
        if (dom.userTabs) {
            const activeTab = dom.userTabs.querySelector(`.user-tab[data-filter="${state.viewState.filter}"]`);
            if (activeTab) markActiveTab(activeTab);
        }
        // CORREÇÃO: Garante que o campo de pesquisa visual reflete o estado guardado no carregamento da página.
        if (dom.searchInput) {
            dom.searchInput.value = state.viewState.searchTerm;
        }
    }

    /**
     * Verificação periódica de convites.
     *
     * 🐛 CORREÇÃO: o intervalo corria sempre, mesmo com o separador em segundo
     * plano, e nunca era cancelado. Isso mantinha um pedido a cada 10 s por cada
     * separador aberto e esquecido. Agora só sonda com a página visível, faz uma
     * verificação imediata ao voltar ao separador, e para ao sair da página.
     */
    function startInvitePolling() {
        const tick = () => {
            if (!document.hidden) ui.loadInvites(true);
        };
        state.setInviteCheckInterval(setInterval(tick, INVITE_POLL_INTERVAL_MS));

        document.addEventListener('visibilitychange', () => {
            if (!document.hidden) ui.loadInvites(true);
        });

        window.addEventListener('pagehide', () => state.setInviteCheckInterval(null));
    }

    /**
     * Configura a conexão WebSocket para receber atualizações em tempo real.
     */
    function setupWebSocket() {
        const socket = io('/dashboard', { reconnectionAttempts: 5, transports: ['websocket'] });

        socket.on('connect', () => {
            console.log('Conectado para atualizações em tempo real da lista de usuários.');
        });

        socket.on('user_list_updated', (data) => {
            console.log('Evento de atualização da lista de usuários recebido:', data);

            // Exibe uma notificação para o administrador com a mensagem do servidor
            const toastMessage = data.message || 'A lista de usuários foi atualizada automaticamente.';
            ui.showToast(toastMessage, 'info');

            // Força a atualização da lista de usuários na UI
            ui.loadStatus(true);
        });

        socket.on('connect_error', (error) => {
            console.error('Erro de conexão com o WebSocket para atualizações de usuários:', error);
        });
    }

    // --- Ponto de Entrada ---
    initializeView();
    initializeEventListeners();
    ui.loadStatus(); // Carrega os dados iniciais

    startInvitePolling();

    // Inicia a escuta por atualizações em tempo real
    setupWebSocket();
});
