// Funções para buscar e enviar dados para a API
import { fetchAPI } from '../utils.js';
import { state } from './config.js';

/**
 * Carrega todos os dados iniciais do dashboard concorrentemente.
 * @returns {Promise<object>} - Uma promessa que resolve com os dados.
 */
export async function loadDashboardData() {
    const { urls } = state;
    const summaryPromise = fetchAPI(`${urls.summaryUrl}?force=true`); // Força na carga inicial
    const healthPromise = fetchAPI(urls.healthUrl);
    const streamsPromise = fetchAPI(urls.activeStreamsUrl);
    const auditPromise = fetchAPI(urls.auditLogsUrl);

    const [summaryData, healthData, streamsData, auditData] = await Promise.all([
        summaryPromise,
        healthPromise,
        streamsPromise,
        auditPromise
    ]);

    return { summaryData, healthData, streamsData, auditData };
}

/**
 * Busca a lista de usuários para o modal de seleção, usando cache se disponível.
 * @returns {Promise<Array>} - Uma promessa que resolve com a lista de usuários.
 */
export async function getUsersForSelection() {
    const { urls, i18n } = state;
    if (state.allUsersForSelection.length === 0) {
        const result = await fetchAPI(urls.listUsersUrl);
        if (result.success && result.users) {
            state.allUsersForSelection = result.users;
        } else {
            throw new Error(result.message || i18n.errorLoadingUsers);
        }
    }
    return state.allUsersForSelection;
}

/**
 * Envia uma requisição para apagar um log de auditoria.
 * @param {string} logId - O ID do log a ser apagado.
 * @returns {Promise<object>} - Resposta da API.
 */
export async function deleteLog(logId) {
    const { urls } = state;
    const url = urls.deleteLogBaseUrl.replace('0', logId);
    return await fetchAPI(url, 'DELETE');
}

/**
 * Envia uma requisição para limpar todos os logs de auditoria.
 * @returns {Promise<object>} - Resposta da API.
 */
export async function clearAllLogs() {
    const { urls } = state;
    return await fetchAPI(urls.clearAllLogsUrl, 'POST');
}

/**
 * Envia uma requisição para iniciar a tarefa de notificação em massa.
 * @param {object} payload - O corpo da requisição (mensagem, público, user_ids).
 * @returns {Promise<object>} - Resposta da API.
 */
export async function sendBulkNotification(payload) {
    const { urls } = state;
    return await fetchAPI(urls.bulkNotifyUrl, 'POST', payload);
}

