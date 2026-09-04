/**
 * Módulo da API
 * Agrupa todas as funções que fazem chamadas à API do backend para a página de configurações.
 */

import { fetchAPI, buildPinCheckUrl } from '../utils.js';
import { urls } from './config.js';

export const getSettings = () => fetchAPI(urls.apiSettings);
export const saveSettings = (config) => fetchAPI(urls.apiSettings, 'POST', config);
export const clearLogs = () => fetchAPI(urls.clearLogs, 'POST');
export const getLogs = () => fetchAPI(urls.getLogs);
export const testTautulli = (payload) => fetchAPI(urls.testTautulli, 'POST', payload);
export const testOverseerr = (payload) => fetchAPI(urls.testOverseerr, 'POST', payload);
export const getPlexAuthContext = () => fetchAPI(`${urls.getPlexAuthContext}?from_settings=true`);
export const checkPlexPin = (clientId, pinId) => fetchAPI(buildPinCheckUrl(urls.checkPlexPin, clientId, pinId));
export const getPlexServers = () => fetchAPI(`${urls.getPlexServers}?from_settings=true`);
export const getOnlineMediaSources = () => fetchAPI(urls.onlineMediaSources);

// --- Backup e Restauro ---
export const syncProfiles = (payload) => fetchAPI(urls.syncProfiles, 'POST', payload);
export const testGates2b = (payload) => fetchAPI(urls.testGates2b, 'POST', payload);
export const testWhatsapp = (payload) => fetchAPI(urls.testWhatsapp, 'POST', payload);
export const backupList = () => fetchAPI(urls.backupList);
export const backupDelete = (filename) => fetchAPI(urls.backupDelete.replace('__FILENAME__', encodeURIComponent(filename)), 'DELETE');
