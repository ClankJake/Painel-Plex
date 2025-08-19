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

document.addEventListener('DOMContentLoaded', () => {
    
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
                state.setViewState({ searchTerm: e.target.value });
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
    }

    // --- Ponto de Entrada ---
    initializeView();
    initializeEventListeners();
    ui.loadStatus(); // Carrega os dados iniciais
    
    // Inicia a verificação periódica de convites
    const intervalId = setInterval(() => ui.loadInvites(true), 10000);
    state.setInviteCheckInterval(intervalId);
});
