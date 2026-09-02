// Funções puras de formatação de dados
import { state } from './config.js';

/**
 * Obtém as cores do gráfico com base no tema (claro/escuro).
 * @returns {object} - Objeto com cores para texto, grades, etc.
 */
export function getChartColors() {
    const isDark = document.documentElement.classList.contains('dark');
    return {
        textColor: isDark ? '#E5E7EB' : '#1F2937',
        gridColor: isDark ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.1)',
        tooltipBg: isDark ? '#1F2937' : '#FFFFFF',
        barColor: 'rgba(34, 197, 94, 0.6)',
        barBorderColor: 'rgba(22, 163, 74, 1)',
        doughnutColors: ['#22C55E', '#EF4444'], // green, red
    };
}

/**
 * Formata um número como moeda BRL.
 * @param {number} value - O valor a ser formatado.
 * @returns {string} - O valor formatado como R$ 0,00.
 */
export function formatCurrency(value) {
    return (value || 0).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
}

/**
 * Formata milissegundos em uma string de tempo HH:MM:SS ou MM:SS.
 * @param {number} ms - Milissegundos.
 * @returns {string} - Tempo formatado.
 */
export function formatTime(ms) {
    if (typeof ms !== 'number' || ms < 0) {
        return '00:00';
    }
    const totalSeconds = Math.floor(ms / 1000);
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const seconds = totalSeconds % 60;

    if (hours > 0) {
        return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
    }
    return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}

/**
 * Formata uma data para "tempo atrás" (ex: "5min atrás").
 * @param {Date} date - O objeto Date.
 * @returns {string} - String de tempo relativo.
 */
export function formatTimeAgo(date) {
    const now = new Date();
    const seconds = Math.floor((now - date) / 1000);

    let interval = seconds / 31536000;
    if (interval > 1) return `${Math.floor(interval)}a atrás`;
    interval = seconds / 2592000;
    if (interval > 1) return `${Math.floor(interval)}m atrás`;
    interval = seconds / 86400;
    if (interval > 1) return `${Math.floor(interval)}d atrás`;
    interval = seconds / 3600;
    if (interval > 1) return `${Math.floor(interval)}h atrás`;
    interval = seconds / 60;
    if (interval > 1) return `${Math.floor(interval)}min atrás`;
    if (seconds < 5) return `agora mesmo`;
    return `${Math.floor(seconds)}s atrás`;
}

/**
 * Formata uma data como "dd/mm/aaaa hh:mm" no idioma da interface.
 * Usada na Auditoria de Cortes: o "há X minutos" diz há quanto tempo foi, mas
 * não diz QUANDO — e era essa a informação que se perdia quando o título do
 * filme/série era longo e empurrava a hora para fora do ecrã.
 * @param {Date} date - O objeto Date.
 * @returns {string} - Data e hora formatadas.
 */
export function formatDateTime(date) {
    if (!(date instanceof Date) || isNaN(date.getTime())) return '';
    const locale = document.documentElement.lang || navigator.language || 'pt-BR';
    const day = date.toLocaleDateString(locale, { day: '2-digit', month: '2-digit', year: 'numeric' });
    const time = date.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' });
    return `${day} ${time}`;
}

/**
 * Escapa texto vindo do Plex (nomes de utilizador, títulos, plataformas) antes
 * de o injetar via innerHTML.
 * @param {string} text - O texto a escapar.
 * @returns {string} - Texto seguro para HTML.
 */
export function escapeHtml(text) {
    if (text === null || text === undefined) return '';
    return String(text).replace(/[&<>"']/g, m => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'
    })[m]);
}

/**
 * Mapeia códigos de razão de terminação para texto legível.
 * @param {string} reason - O código da razão.
 * @returns {string} - O texto legível.
 */
export function getReasonText(reason) {
    const { i18n } = state;
    switch (reason) {
        case 'limit_exceeded':
            return i18n.reasonLimitExceeded;
        case 'blocked_manual':
            return i18n.reasonBlockedManual;
        case 'blocked_expired':
            return i18n.reasonBlockedExpired;
        case 'blocked_trial_expired':
            return i18n.reasonBlockedTrialExpired;
        default:
            if (reason && reason.startsWith('blocked')) { // Catch-all
                return i18n.reasonBlocked;
            }
            return reason || 'Desconhecido'; // Fallback
    }
}

