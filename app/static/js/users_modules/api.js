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
export const updateUserLimit = (plexUserId, screens) => fetchAPI(urls.apiUsersUpdateLimit, 'POST', { plex_user_id: plexUserId, screens });
export const renewSubscription = (plexUserId, payload) => fetchAPI(urls.apiUsersRenewBase.replace('0', plexUserId), 'POST', payload);
export const fetchUserProfile = (plexUserId) => fetchAPI(urls.apiUsersProfileBase.replace('0', plexUserId));
export const updateUserProfile = (plexUserId, payload) => fetchAPI(urls.apiUsersProfileSetBase.replace('0', plexUserId), 'POST', payload);
export const notifyUser = (plexUserId) => fetchAPI(urls.apiUsersNotifyBase.replace('0', plexUserId), 'POST');
export const fetchUserLibraries = (plexUserId) => fetchAPI(urls.apiUsersLibrariesBase.replace('0', plexUserId));
export const updateUserLibraries = (plexUserId, libraries) => fetchAPI(urls.apiUsersUpdateLibraries, 'POST', { plex_user_id: plexUserId, libraries });
export const removeUser = (plexUserId) => fetchAPI(urls.apiUsersRemove, 'POST', { plex_user_id: plexUserId });
export const blockUser = (plexUserId) => fetchAPI(urls.apiUsersBlock, 'POST', { plex_user_id: plexUserId });
export const unblockUser = (plexUserId) => fetchAPI(urls.apiUsersUnblock, 'POST', { plex_user_id: plexUserId });
export const reactivateUser = (plexUserId, libraries) => fetchAPI(urls.apiUsersReactivate, 'POST', { plex_user_id: plexUserId, libraries: libraries });
export const deleteUserPermanently = (plexUserId) => fetchAPI(urls.apiUsersDeletePermanently, 'POST', { plex_user_id: plexUserId });
export const toggleOverseerr = (plexUserId, access) => fetchAPI(urls.apiUsersToggleOverseerr, 'POST', { plex_user_id: plexUserId, access });
export const updateAllLimits = (screens) => fetchAPI(urls.apiUsersUpdateAllLimits, 'POST', { screens });
export const fetchPaymentHistory = (plexUserId) => fetchAPI(urls.apiUsersPaymentsBase.replace('0', plexUserId));
export const updateAllLibraries = (libraries) => fetchAPI(urls.apiUsersUpdateAllLibraries, 'POST', { libraries });
