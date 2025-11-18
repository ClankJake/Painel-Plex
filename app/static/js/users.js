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

// MELHORIA DE SEGURANÇA: Função para sanitizar entradas de texto no frontend
function sanitizeHTML(str) {
    const temp = document.createElement('div');
    temp.textContent = str;
    return temp.innerHTML;
}


document.addEventListener('DOMContentLoaded', () => {
    
    // MELHORIA: Limpa o termo de pesquisa guardado sempre que a página é carregada
    // para evitar que um filtro antigo seja aplicado ao voltar para a página.
    state.setViewState({ searchTerm: '' });

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
            dom.searchInput.addEventListener('input', (e) => {
                // MELHORIA DE SEGURANÇA: Sanitiza a entrada antes de a usar para filtrar
                const searchTerm = sanitizeHTML(e.target.value);
                state.setViewState({ searchTerm: searchTerm });
                ui.renderUserGrid();
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
                    dom.userTabs.querySelectorAll('.user-tab').forEach(tab => tab.classList.remove('active'));
                    button.classList.add('active');
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
            if (activeTab) {
                dom.userTabs.querySelectorAll('.user-tab').forEach(tab => tab.classList.remove('active'));
                activeTab.classList.add('active');
            }
        }
        // CORREÇÃO: Garante que o campo de pesquisa visual reflete o estado guardado no carregamento da página.
        if (dom.searchInput) {
            dom.searchInput.value = state.viewState.searchTerm;
        }
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
    
    // Inicia a verificação periódica de convites
    const intervalId = setInterval(() => ui.loadInvites(true), 10000);
    state.setInviteCheckInterval(intervalId);

    // Inicia a escuta por atualizações em tempo real
    setupWebSocket();
});
