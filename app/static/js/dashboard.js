import { initConfig, dom } from './dashboard_modules/config.js';
import { loadDashboardData } from './dashboard_modules/api.js';
import {
    renderSummaryCards,
    renderCharts,
    renderSystemHealth,
    renderActiveStreamsDashboard,
    renderTerminationLogs
} from './dashboard_modules/ui.js';
import { setupWebSocket } from './dashboard_modules/websocket.js';
import { attachEventListeners } from './dashboard_modules/listeners.js';

document.addEventListener('DOMContentLoaded', async () => {
    // 1. Popula urls, i18n, e referências do DOM
    initConfig();

    dom.loadingIndicator.style.display = 'block';
    dom.dashboardContainer.classList.add('hidden');
    dom.errorContainer.classList.add('hidden');

    try {
        // 2. Carrega todos os dados iniciais da API
        const data = await loadDashboardData();

        // 3. Renderiza os dados iniciais
        if (data.summaryData.success) {
            renderSummaryCards(data.summaryData.summary);
            renderCharts(data.summaryData.summary);
        } else {
            throw new Error(data.summaryData.message);
        }

        if (data.healthData.success) {
            renderSystemHealth(data.healthData.health);
        }

        if (data.streamsData.success) {
            renderActiveStreamsDashboard(data.streamsData.sessions);
        }

        if (data.auditData.success) {
            renderTerminationLogs(data.auditData.logs);
        }

        // Exibe o dashboard
        dom.dashboardContainer.classList.remove('hidden');

    } catch (error) {
        // Exibe o erro
        dom.errorMessage.textContent = error.message;
        dom.errorContainer.classList.remove('hidden');
    } finally {
        dom.loadingIndicator.style.display = 'none';
    }

    // 4. Anexa os event listeners (cliques, etc.)
    attachEventListeners();
    
    // 5. Inicia a conexão WebSocket
    setupWebSocket();
});

