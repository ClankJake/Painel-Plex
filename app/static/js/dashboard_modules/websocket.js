// Lógica de configuração e manipulação do WebSocket
import { dom, state } from './config.js'; // **NOVO**: importado 'state'
import { renderSummaryCards, renderCharts, renderActiveStreamsDashboard, prependTerminationLog } from './ui.js';
import { resetBulkNotificationUI } from './handlers.js';
import { showToast } from '../utils.js';

export function setupWebSocket() {
    const { i18n } = state; // i18n já estava a ser usado, importado via 'state'
    const socket = io('/dashboard', { reconnectionAttempts: 5, transports: ['websocket'] });

    const setStatus = (status, text) => {
        if (!dom.realtimeStatus) return;
        const dot = dom.realtimeStatus.querySelector('div');
        const span = dom.realtimeStatus.querySelector('span');
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
        span.textContent = text;
    };

    setStatus('reconnecting', i18n.connecting);

    socket.on('connect', () => {
        console.log('Conectado ao dashboard em tempo real!');
        setStatus('connected', i18n.connected);
    });

    socket.on('dashboard_update', (data) => {
        if (data.summary) {
            renderSummaryCards(data.summary);
            renderCharts(data.summary);
        }
    });

    socket.on('active_streams_update', (data) => {
        renderActiveStreamsDashboard(data.sessions);
    });

    socket.on('new_termination_log', (log) => {
        prependTerminationLog(log);
    });

    socket.on('disconnect', () => {
        console.warn('Desconectado do dashboard em tempo real.');
        setStatus('disconnected', i18n.disconnected);
    });

    socket.on('reconnect_attempt', () => {
        console.log('Tentando reconectar...');
        setStatus('reconnecting', i18n.reconnecting);
    });

    socket.on('connect_error', (error) => {
        console.error('Erro de conexão com o WebSocket:', error);
        setStatus('disconnected', i18n.disconnected);
    });

    // Handlers de notificação em massa
    if (dom.progressContainer && dom.progressBar && dom.progressText && dom.progressPercent) {
        socket.on('bulk_notification_start', (data) => {
            // **NOVO**: Limpa o temporizador de segurança
            if (state.safetyTimeout) {
                clearTimeout(state.safetyTimeout);
                state.safetyTimeout = null;
            }
            
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

        socket.on('bulk_notification_end', (data) => {
            // **NOVO**: Limpa o temporizador de segurança (por precaução)
             if (state.safetyTimeout) {
                clearTimeout(state.safetyTimeout);
                state.safetyTimeout = null;
            }

            dom.progressBar.style.width = '100%';
            dom.progressBar.classList.remove('bg-blue-600');
            dom.progressBar.classList.add('bg-green-600');
            dom.progressPercent.textContent = '100%';
            dom.progressText.textContent = i18n.bulkSendComplete;
            showToast(i18n.bulkSendComplete, 'success');
            resetBulkNotificationUI(3000); // Reseta após 3s
        });

        socket.on('bulk_notification_error', (data) => {
            // **NOVO**: Limpa o temporizador de segurança
             if (state.safetyTimeout) {
                clearTimeout(state.safetyTimeout);
                state.safetyTimeout = null;
            }

            showToast(`${i18n.bulkSendError}: ${data.message}`, 'error');
            dom.progressText.textContent = i18n.bulkSendError;
            dom.progressBar.classList.remove('bg-blue-600');
            dom.progressBar.classList.add('bg-red-600');
            resetBulkNotificationUI(5000); // Reseta após 5s
        });
    } else {
         console.error("Elementos da barra de progresso não encontrados. Handlers de progresso do Socket.IO não funcionarão.");
    }
}

