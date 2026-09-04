// Funções para buscar e enviar dados para a API
import { fetchAPI } from '../utils.js';
import { state } from './config.js';

/**
 * Dispara todos os pedidos iniciais do dashboard em paralelo e devolve as
 * promessas SEM esperar por elas.
 *
 * Devolver as promessas soltas (em vez de um Promise.all já resolvido) é o que
 * permite ao dashboard.js pintar cada secção no momento em que a resposta dela
 * chega. Antes, a página inteira ficava no ecrã de carregamento até à última
 * resposta — e a última era quase sempre a das sessões ativas, que depende do
 * servidor Plex responder.
 *
 * @returns {{summary: Promise, health: Promise, streams: Promise, audit: Promise}}
 */
export function startDashboardRequests() {
    const { urls } = state;
    return {
        summary: fetchAPI(`${urls.summaryUrl}?force=true`), // Força na carga inicial
        health: fetchAPI(urls.healthUrl),
        streams: fetchAPI(urls.activeStreamsUrl),
        audit: fetchAPI(urls.auditLogsUrl)
    };
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

