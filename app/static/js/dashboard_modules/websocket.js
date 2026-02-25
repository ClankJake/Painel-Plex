import { dom, state } from './config.js';
import { renderSummaryCards, renderCharts, renderActiveStreamsDashboard, prependTerminationLog } from './ui.js';
import { resetBulkNotificationUI } from './handlers.js';
import { showToast } from '../utils.js';

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

    // Reset base classes
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
 * Extraído para uma função separada para manter o setup principal limpo.
 */
const setupBulkNotificationHandlers = (socket, i18n) => {
    if (!dom.progressContainer || !dom.progressBar || !dom.progressText || !dom.progressPercent) {
        console.warn("WS: Elementos da barra de progresso ausentes. O acompanhamento visual do envio em massa não funcionará.");
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
    
    // Conexão apenas via WebSocket para evitar os delays e polling do long-polling nativo
    const socket = io('/dashboard', { 
        reconnectionAttempts: 5, 
        transports: ['websocket'] 
    });

    updateRealtimeIndicator('reconnecting', i18n.connecting, i18n);

    // --- EVENTOS DE CONEXÃO ---
    socket.on('connect', () => {
        console.log('WS: Conectado ao dashboard.');
        updateRealtimeIndicator('connected', i18n.connected, i18n);
    });

    socket.on('disconnect', () => {
        console.warn('WS: Desconectado do servidor.');
        updateRealtimeIndicator('disconnected', i18n.disconnected, i18n);
    });

    socket.on('reconnect_attempt', () => {
        console.log('WS: A tentar reconectar...');
        updateRealtimeIndicator('reconnecting', i18n.reconnecting, i18n);
    });

    socket.on('connect_error', (error) => {
        console.error('WS: Erro de ligação:', error);
        updateRealtimeIndicator('disconnected', i18n.disconnected, i18n);
    });

    // --- EVENTOS DE ATUALIZAÇÃO DO DASHBOARD ---
    socket.on('dashboard_update', (data) => {
        if (!data || !data.summary) return;
        
        // Verificações de segurança adicionais antes de tentar renderizar a interface
        if (typeof renderSummaryCards === 'function') renderSummaryCards(data.summary);
        if (typeof renderCharts === 'function') renderCharts(data.summary);
    });

    socket.on('active_streams_update', (data) => {
        if (data && data.sessions && typeof renderActiveStreamsDashboard === 'function') {
            renderActiveStreamsDashboard(data.sessions);
        }
    });

    socket.on('new_termination_log', (log) => {
        if (log && typeof prependTerminationLog === 'function') {
            prependTerminationLog(log);
        }
    });

    // Inicializa a lógica de Progresso (se os elementos DOM existirem)
    setupBulkNotificationHandlers(socket, i18n);
}
