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

const initDashboard = async () => {
    console.log("🚀 [Dashboard] A inicializar o sistema central...");

    // 1. Popula urls, variáveis de tradução (i18n) e referências do DOM
    initConfig();

    // 2. Prepara a Interface (Mostra Loading, Esconde Painel)
    dom.loadingIndicator.style.display = ''; // Limpa para permitir que a classe 'flex' do Tailwind atue
    dom.dashboardContainer.classList.add('hidden');
    dom.errorContainer.classList.add('hidden');

    try {
        console.log("📦 [Dashboard] A transferir dados do servidor...");
        
        // 3. Carrega todos os dados iniciais da API em paralelo
        const data = await loadDashboardData();

        // 4. Tolerância a Falhas (Graceful Degradation)
        // Tentamos renderizar cada bloco individualmente. Se um falhar, o resto continua a funcionar!
        
        // Resumos e Gráficos
        if (data.summaryData && data.summaryData.success) {
            renderSummaryCards(data.summaryData.summary);
            renderCharts(data.summaryData.summary);
        } else {
            console.warn("⚠️ [Dashboard] Falha parcial: Não foi possível carregar os resumos/gráficos.", data.summaryData?.message);
        }

        // Saúde do Sistema
        if (data.healthData && data.healthData.success) {
            renderSystemHealth(data.healthData.health);
        } else {
            console.warn("⚠️ [Dashboard] Falha parcial: Não foi possível carregar a saúde do sistema.");
        }

        // Streams em Tempo Real
        if (data.streamsData && data.streamsData.success) {
            renderActiveStreamsDashboard(data.streamsData.sessions);
        }

        // Logs de Auditoria
        if (data.auditData && data.auditData.success) {
            renderTerminationLogs(data.auditData.logs);
        }

        // Tudo carregado (ou a maior parte), exibe o painel com transição suave
        dom.dashboardContainer.classList.remove('hidden');
        console.log("✅ [Dashboard] Interface carregada com sucesso.");

    } catch (error) {
        // Erro Crítico (Ex: falha total de rede ou API offline)
        console.error("❌ [Dashboard] Erro crítico na inicialização:", error);
        dom.errorMessage.textContent = error.message || "Falha grave na comunicação com o servidor.";
        dom.errorContainer.classList.remove('hidden');
    } finally {
        // Desliga o spinner de carregamento independentemente do resultado
        dom.loadingIndicator.style.display = 'none';
    }

    // 5. Anexa os event listeners (Cliques, Envios de Formulário, Modais)
    attachEventListeners();
    
    // 6. Inicia o Motor de Tempo Real (Plex SSE + Socket.IO)
    setupWebSocket();
};

// Dispara o arranque assim que o HTML do navegador estiver pronto
document.addEventListener('DOMContentLoaded', initDashboard);
