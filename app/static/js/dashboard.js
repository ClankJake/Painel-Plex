import { initConfig, dom } from './dashboard_modules/config.js';
import { startDashboardRequests } from './dashboard_modules/api.js';
import {
    renderSummaryCards,
    renderSummaryCardsSkeleton,
    renderCharts,
    renderSystemHealth,
    renderSystemHealthSkeleton,
    renderActiveStreamsDashboard,
    renderActiveStreamsLoading,
    renderActiveStreamsUnavailable,
    renderTerminationLogs
} from './dashboard_modules/ui.js';
import { setupWebSocket } from './dashboard_modules/websocket.js';
import { attachEventListeners } from './dashboard_modules/listeners.js';

const initDashboard = () => {
    console.log("🚀 [Dashboard] A inicializar o sistema central...");

    // 1. Popula urls, variáveis de tradução (i18n) e referências do DOM
    initConfig();

    // 2. Mostra o painel IMEDIATAMENTE, com esqueletos no lugar de cada bloco.
    //
    // Antes, a página inteira ficava atrás de um spinner até que os QUATRO
    // pedidos iniciais respondessem — e o das sessões ativas depende do servidor
    // Plex, que é a parte mais lenta e a que mais falha. Bastava um Plex a
    // responder devagar para o administrador não ver a receita, os utilizadores
    // nem a auditoria, que já estavam prontos. Agora cada secção aparece assim
    // que os dados DELA chegam.
    dom.loadingIndicator.style.display = 'none';
    dom.errorContainer.classList.add('hidden');
    dom.dashboardContainer.classList.remove('hidden');

    renderSummaryCardsSkeleton();
    renderSystemHealthSkeleton();
    renderActiveStreamsLoading();

    // 3. Dispara os pedidos em paralelo (sem esperar por nenhum)
    console.log("📦 [Dashboard] A transferir dados do servidor...");
    const requests = startDashboardRequests();

    // 4. Tolerância a falhas: cada bloco é tratado por si. Um bloco que falhe
    //    não impede os restantes de aparecer.
    const summaryTask = requests.summary
        .then(data => {
            if (!data?.success) throw new Error(data?.message || 'Resposta inválida do resumo.');
            renderSummaryCards(data.summary);
            renderCharts(data.summary);
            return true;
        })
        .catch(error => {
            console.warn("⚠️ [Dashboard] Falha parcial: resumos/gráficos indisponíveis.", error);
            return false;
        });

    const healthTask = requests.health
        .then(data => {
            if (!data?.success) throw new Error('Resposta inválida da saúde do sistema.');
            renderSystemHealth(data.health);
            return true;
        })
        .catch(error => {
            console.warn("⚠️ [Dashboard] Falha parcial: saúde do sistema indisponível.", error);
            return false;
        });

    const auditTask = requests.audit
        .then(data => {
            if (!data?.success) throw new Error('Resposta inválida dos logs de auditoria.');
            renderTerminationLogs(data.logs);
            return true;
        })
        .catch(error => {
            console.warn("⚠️ [Dashboard] Falha parcial: logs de auditoria indisponíveis.", error);
            return false;
        });

    // As sessões ativas entram quando entrarem: são o bloco mais lento e nada
    // no resto da página espera por elas.
    const streamsTask = requests.streams
        .then(data => {
            if (!data?.success) throw new Error(data?.message || 'Resposta inválida das sessões ativas.');
            renderActiveStreamsDashboard(data.sessions);
            return true;
        })
        .catch(error => {
            console.warn("⚠️ [Dashboard] Falha parcial: sessões ativas indisponíveis.", error);
            renderActiveStreamsUnavailable();
            return false;
        });

    // 5. O ecrã de erro só aparece se TUDO falhou (API em baixo, sessão expirada,
    //    rede cortada). Com qualquer bloco de pé, o painel é útil.
    Promise.all([summaryTask, healthTask, auditTask, streamsTask]).then(results => {
        if (results.some(Boolean)) {
            console.log("✅ [Dashboard] Interface carregada.");
            return;
        }
        console.error("❌ [Dashboard] Nenhum bloco de dados pôde ser carregado.");
        dom.dashboardContainer.classList.add('hidden');
        dom.errorMessage.textContent = "Falha grave na comunicação com o servidor.";
        dom.errorContainer.classList.remove('hidden');
    });

    // 6. Anexa os event listeners (Cliques, Envios de Formulário, Modais)
    attachEventListeners();

    // 7. Inicia o Motor de Tempo Real (Plex SSE + Socket.IO)
    setupWebSocket();
};

// Dispara o arranque assim que o HTML do navegador estiver pronto
document.addEventListener('DOMContentLoaded', initDashboard);
