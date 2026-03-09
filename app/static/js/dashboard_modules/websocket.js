import { dom, state } from './config.js';
import { renderSummaryCards, renderCharts, renderActiveStreamsDashboard, prependTerminationLog } from './ui.js';
import { 
    resetBulkNotificationUI, 
    handleBulkProgressUpdate, 
    handleBulkProgressEnd, 
    handleBulkProgressError 
} from './handlers.js';
import { showToast, fetchAPI } from '../utils.js';

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
 * Agora ligado diretamente ao handlers.js para sincronia perfeita e animação!
 */
const setupBulkNotificationHandlers = (socket, i18n) => {
    // Liga a transição suave do CSS para que a barra tenha animação ao encher
    if (dom.progressBar) {
        dom.progressBar.style.transition = 'width 0.4s ease-in-out';
    }

    socket.on('bulk_notification_start', (data) => {
        if (dom.progressContainer) dom.progressContainer.classList.remove('hidden');
        handleBulkProgressUpdate(0, data.total);
    });

    socket.on('bulk_notification_progress', (data) => {
        if (dom.progressContainer) dom.progressContainer.classList.remove('hidden');
        handleBulkProgressUpdate(data.current, data.total);
    });

    socket.on('bulk_notification_end', (data) => {
        handleBulkProgressEnd(data.message || i18n.bulkSendComplete);
    });

    socket.on('bulk_notification_error', (data) => {
        handleBulkProgressError(data.message || i18n.bulkSendError);
    });
    socket.on('bulk_console_log', (data) => {
        console.log(`%c[SERVIDOR] %c${data.msg}`, "color: #3B82F6; font-weight: bold", "color: inherit");
    });
};

/**
 * Ponto de entrada principal para a inicialização do Socket.IO no lado do cliente.
 */
export function setupWebSocket() {
    const { i18n } = state;
    let streamUpdateTimeout = null;
    let fastUpdateTimeout = null; // Guarda o timer rápido
    let fetchCounter = 0; // Previne atropelamento de dados (Race Condition)
    
    // Conexão Inteligente: Usa Polling inicial e depois atualiza para WebSocket
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
        if (fastUpdateTimeout) clearTimeout(fastUpdateTimeout);
        
        console.log("⚡ Sinal Plex detetado! A atualizar instantaneamente...");

        const fetchAndUpdate = async () => {
            const currentFetch = ++fetchCounter; 
            
            try {
                const scriptTag = document.getElementById('dashboard-script');
                const activeStreamsUrl = scriptTag ? scriptTag.dataset.activeStreamsUrl : '/api/system/active-streams';
                
                const data = await fetchAPI(activeStreamsUrl);
                
                if (currentFetch !== fetchCounter) return;
                
                if (data && data.success && typeof renderActiveStreamsDashboard === 'function') {
                    renderActiveStreamsDashboard(data.sessions);
                }
            } catch (error) {
                console.error('WS: Erro ao buscar os streams SSE:', error);
            }
        };

        // 1ª Chamada Rápida (400ms)
        fastUpdateTimeout = setTimeout(fetchAndUpdate, 400);

        // 2ª Chamada Tardia (3000ms)
        streamUpdateTimeout = setTimeout(fetchAndUpdate, 3000); 
    });

    // --- LOGS DE AUDITORIA ---
    socket.on('new_termination_log', (log) => {
        if (log && typeof prependTerminationLog === 'function') {
            prependTerminationLog(log);
        }
    });

    // Anexa os eventos de Bulk Notifications configurados acima
    setupBulkNotificationHandlers(socket, i18n);

    // --- LOOP DE SEGURANÇA (FALLBACK) ---
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
            // Falha silenciosa
        }
    }, 15000);
}
