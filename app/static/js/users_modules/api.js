// app/static/js/users_modules/api.js

import { fetchAPI } from '../utils.js';
import { urls } from './config.js';

/**
 * Módulo da API
 * * Agrupa todas as funções que fazem chamadas à API do backend.
 * Cada função corresponde a um endpoint, tornando o código mais limpo
 * e fácil de entender onde e como a comunicação com o servidor acontece.
 */

export const fetchStatus = (force = false) => fetchAPI(`${urls.apiStatus}?force=${force}`);
export const listInvites = () => fetchAPI(urls.apiInvitesList);
export const deleteInvite = (code) => fetchAPI(urls.apiInvitesDelete, 'POST', { code });
export const createInvite = (payload) => fetchAPI(urls.apiInvitesCreate, 'POST', payload);
export const updateUserLimit = (email, screens) => fetchAPI(urls.apiUsersUpdateLimit, 'POST', { email, screens });
export const renewSubscription = (username, payload) => fetchAPI(urls.apiUsersRenewBase.replace('__USERNAME__', username), 'POST', payload);
export const fetchUserProfile = (username) => fetchAPI(urls.apiUsersProfileBase.replace('__USERNAME__', username));
export const updateUserProfile = (username, payload) => fetchAPI(urls.apiUsersProfileSetBase.replace('__USERNAME__', username), 'POST', payload);
export const notifyUser = (username) => fetchAPI(urls.apiUsersNotifyBase.replace('__USERNAME__', username), 'POST');
export const fetchUserLibraries = (email) => fetchAPI(urls.apiUsersLibrariesBase.replace('__EMAIL__', email));
export const updateUserLibraries = (email, libraries) => fetchAPI(urls.apiUsersUpdateLibraries, 'POST', { email, libraries });
export const removeUser = (email) => fetchAPI(urls.apiUsersRemove, 'POST', { email });
export const blockUser = (email) => fetchAPI(urls.apiUsersBlock, 'POST', { email });
export const unblockUser = (email) => fetchAPI(urls.apiUsersUnblock, 'POST', { email });
export const toggleOverseerr = (email, access) => fetchAPI(urls.apiUsersToggleOverseerr, 'POST', { email, access });
export const updateAllLimits = (screens) => fetchAPI(urls.apiUsersUpdateAllLimits, 'POST', { screens });
export const fetchPaymentHistory = (username) => fetchAPI(urls.apiUsersPaymentsBase.replace('__USERNAME__', username));

/**
 * Atualiza as bibliotecas para todos os utilizadores.
 * CORREÇÃO: Adiciona um fallback para a URL, caso ela não seja fornecida pelo template HTML.
 * @param {string[]} libraries - A lista de títulos de bibliotecas.
 * @returns {Promise<any>}
 */
export const updateAllLibraries = (libraries) => {
    const endpoint = urls.apiUsersUpdateAllLibraries || '/api/users/update-all-libraries';
    return fetchAPI(endpoint, 'POST', { libraries });
};
