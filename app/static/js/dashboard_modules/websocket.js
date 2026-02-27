// IMPORTAÇÕES CORRIGIDAS: Como este ficheiro já está em 'dashboard_modules', 
// usamos './' para os ficheiros irmãos e '../' para o utils.js que está na pasta anterior.
import { dom, state } from './config.js';
import { renderSummaryCards, renderCharts, renderActiveStreamsDashboard, prependTerminationLog } from './ui.js';
import { resetBulkNotificationUI } from './handlers.js';
import { showToast, fetchAPI } from '../utils.js';

/**
 * Função utilitária para limpar temporizadores de segurança pendentes
 * e evitar o congelamento do UI.
 */
const clearSafetyTimeout = () => {
    if (state.safetyTimeout) {
        clearTimeout(state.safetyTimeout);
        state.safetyTimeout = null;
    }
};

/**
 * Atualiza o ícone e o texto do indicador de ligação no cabeçalho do Dashboard.
 */
const updateRealtimeIndicator = (status, text, i18n) => {
    if (!dom.realtimeStatus) return;
    
    const dot = dom.realtimeStatus.querySelector('div');
    const span = dom.realtimeStatus.querySelector('span');
    
    if (!dot || !span) return;

    dot.className = 'w-2 h-2 rounded-full';
    dom.realtimeStatus.className = 'flex items-center gap-2 text-sm font-semibold px-3 py-1 rounded-full transition-colors duration-300';

    switch (status) {
        case 'connected':
            dot.classList.add('bg-green-500');
            dom.realtimeStatus.classList.add('bg-green-200', 'text-green-800', 'dark:bg-green-900', 'dark:text-green-200');
            break;
        case 'reconnecting':
            dot.classList.add('bg-yellow-500', 'animate-pulse');
            dom.realtimeStatus.classList.add('bg-yellow-200', 'text-yellow-800', 'dark:bg-yellow-900', 'dark:text-yellow-200');
            break;
        case 'disconnected':
            dot.classList.add('bg-red-500');
            dom.realtimeStatus.classList.add('bg-red-200', 'text-red-800', 'dark:bg-red-900', 'dark:text-red-200');
            break;
    }
    
    span.textContent = text || i18n[status] || status;
};

/**
 * Regista os event listeners relacionados com o envio em massa (Bulk Notifications).
 */
const setupBulkNotificationHandlers = (socket, i18n) => {
    if (!dom.progressContainer || !dom.progressBar || !dom.progressText || !dom.progressPercent) {
        return;
    }

    socket.on('bulk_notification_start', (data) => {
        clearSafetyTimeout();
        dom.progressContainer.classList.remove('hidden');
        dom.progressBar.style.width = '0%';
        dom.progressBar.classList.remove('bg-red-600', 'bg-green-600');
        dom.progressBar.classList.add('bg-blue-600');
        dom.progressPercent.textContent = '0%';
        dom.progressText.textContent = i18n.bulkSendProgress.replace('{current}', 0).replace('{total}', data.total);
    });

    socket.on('bulk_notification_progress', (data) => {
        const percent = data.total > 0 ? Math.round((data.current / data.total) * 100) : 0;
        dom.progressBar.style.width = `${percent}%`;
        dom.progressPercent.textContent = `${percent}%`;
        dom.progressText.textContent = i18n.bulkSendProgress.replace('{current}', data.current).replace('{total}', data.total);
    });

    socket.on('bulk_notification_end', () => {
        clearSafetyTimeout();
        dom.progressBar.style.width = '100%';
        dom.progressBar.classList.remove('bg-blue-600');
        dom.progressBar.classList.add('bg-green-600');
        dom.progressPercent.textContent = '100%';
        dom.progressText.textContent = i18n.bulkSendComplete;
        showToast(i18n.bulkSendComplete, 'success');
        resetBulkNotificationUI(3000); 
    });

    socket.on('bulk_notification_error', (data) => {
        clearSafetyTimeout();
        showToast(`${i18n.bulkSendError}: ${data.message}`, 'error');
        dom.progressText.textContent = i18n.bulkSendError;
        dom.progressBar.classList.remove('bg-blue-600');
        dom.progressBar.classList.add('bg-red-600');
        resetBulkNotificationUI(5000); 
    });
};

/**
 * Ponto de entrada principal para a inicialização do Socket.IO no lado do cliente.
 */
export function setupWebSocket() {
    const { i18n } = state;
    let streamUpdateTimeout = null;
    
    // Conexão Inteligente: Usa Polling inicial e depois atualiza para WebSocket (Evita erros na Cloudflare/Nginx)
    const socket = io('/dashboard', {
        transports: ['polling', 'websocket'],
        reconnection: true,
        reconnectionAttempts: Infinity,
        reconnectionDelay: 1000,
        reconnectionDelayMax: 5000,
        timeout: 20000
    });

    updateRealtimeIndicator('reconnecting', i18n.connecting, i18n);

    // --- EVENTOS DE CONEXÃO ---
    socket.on('connect', () => {
        console.log('⚡ WS: Conectado ao dashboard.');
        updateRealtimeIndicator('connected', i18n.connected, i18n);
    });

    socket.on('disconnect', (reason) => {
        if (reason !== 'ping timeout' && reason !== 'transport close') {
            console.warn(`WS: Desconectado do servidor. Motivo: ${reason}`);
        }
        updateRealtimeIndicator('reconnecting', i18n.reconnecting || 'A reconectar...', i18n);
    });

    socket.on('reconnect_attempt', () => {
        updateRealtimeIndicator('reconnecting', i18n.reconnecting, i18n);
    });

    socket.on('connect_error', (error) => {
        console.error('WS: Erro de ligação:', error.message);
        updateRealtimeIndicator('disconnected', i18n.disconnected, i18n);
    });

    // --- EVENTOS DE ATUALIZAÇÃO DO DASHBOARD ---
    socket.on('dashboard_update', (data) => {
        if (!data || !data.summary) return;
        if (typeof renderSummaryCards === 'function') renderSummaryCards(data.summary);
        if (typeof renderCharts === 'function') renderCharts(data.summary);
    });

    socket.on('active_streams_update', (data) => {
        if (data && data.sessions && typeof renderActiveStreamsDashboard === 'function') {
            renderActiveStreamsDashboard(data.sessions);
        }
    });

    // --- PLEX SSE: O MILAGRE DO TEMPO REAL ⚡ ---
    socket.on('dashboard_update_streams', () => {
        if (streamUpdateTimeout) clearTimeout(streamUpdateTimeout);
        
        console.log("⚡ Sinal Plex detetado! A atualizar instantaneamente...");

        const fetchAndUpdate = async () => {
            try {
                const scriptTag = document.getElementById('dashboard-script');
                const activeStreamsUrl = scriptTag ? scriptTag.dataset.activeStreamsUrl : '/api/system/active-streams';
                
                const data = await fetchAPI(activeStreamsUrl);
                
                if (data && data.success && typeof renderActiveStreamsDashboard === 'function') {
                    renderActiveStreamsDashboard(data.sessions);
                }
            } catch (error) {
                console.error('WS: Erro ao buscar os streams SSE:', error);
            }
        };

        // 1ª Chamada Rápida (400ms): Limpa imediatamente os streams quando o utilizador faz "Stop"
        setTimeout(fetchAndUpdate, 400);

        // 2ª Chamada Tardia (3000ms): Atualiza o ecrã com os novos filmes (Plex demora a gerar a sessão no arranque)
        streamUpdateTimeout = setTimeout(fetchAndUpdate, 3000); 
    });

    // --- LOGS DE AUDITORIA ---
    socket.on('new_termination_log', (log) => {
        if (log && typeof prependTerminationLog === 'function') {
            prependTerminationLog(log);
        }
    });

    setupBulkNotificationHandlers(socket, i18n);

    // --- LOOP DE SEGURANÇA (FALLBACK) ---
    // Garante que o painel é atualizado a cada 15 segundos mesmo que os websockets falhem,
    // mas SÓ SE a aba estiver aberta e visível.
    setInterval(async () => {
        if (document.hidden) return; 

        try {
            const scriptTag = document.getElementById('dashboard-script');
            const activeStreamsUrl = scriptTag ? scriptTag.dataset.activeStreamsUrl : '/api/system/active-streams';
            const data = await fetchAPI(activeStreamsUrl);
            
            if (data && data.success && typeof renderActiveStreamsDashboard === 'function') {
                renderActiveStreamsDashboard(data.sessions);
            }
        } catch (e) {
            // Falha silenciosa no background
        }
    }, 15000);
}
