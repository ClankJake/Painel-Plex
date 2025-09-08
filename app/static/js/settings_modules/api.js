/**
 * Módulo da API
 * Agrupa todas as funções que fazem chamadas à API do backend para a página de configurações.
 */

import { fetchAPI } from '../utils.js';
import { urls } from './config.js';

export const getSettings = () => fetchAPI(urls.apiSettings);
export const saveSettings = (config) => fetchAPI(urls.apiSettings, 'POST', config);
export const clearLogs = () => fetchAPI(urls.clearLogs, 'POST');
export const getLogs = () => fetchAPI(urls.getLogs);
export const testTautulli = (payload) => fetchAPI(urls.testTautulli, 'POST', payload);
export const testOverseerr = (payload) => fetchAPI(urls.testOverseerr, 'POST', payload);
export const getPlexAuthContext = () => fetchAPI(`${urls.getPlexAuthContext}?from_settings=true`);
export const checkPlexPin = (clientId, pinId) => fetchAPI(urls.checkPlexPin.replace('__CLIENT_ID__', clientId).replace('999999', pinId));
export const getPlexServers = () => fetchAPI(`${urls.getPlexServers}?from_settings=true`);
