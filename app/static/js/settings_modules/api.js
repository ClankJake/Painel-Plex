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

// A auditoria só se lê: não há rota para a apagar, e é essa a diferença que a
// faz existir ao lado dos Logs do Sistema.
export const getAuditLogs = ({ limit, offset, action } = {}) => {
    const parametros = new URLSearchParams();
    if (limit) parametros.set('limit', limit);
    if (offset) parametros.set('offset', offset);
    if (action) parametros.set('action', action);
    const consulta = parametros.toString();
    return fetchAPI(consulta ? `${urls.auditLogs}?${consulta}` : urls.auditLogs);
};
// A aba "Sobre": a versão e o fuso vêm de uma rota que não fala com ninguém de
// fora (abre sempre); a release é um pedido ao GitHub e vai à parte, porque
// pode não haver rede de saída.
export const getAbout = () => fetchAPI(urls.about);
export const getLatestRelease = (forcar = false) =>
    fetchAPI(forcar ? `${urls.latestRelease}?forcar=1` : urls.latestRelease);
export const testTautulli = (payload) => fetchAPI(urls.testTautulli, 'POST', payload);
export const testJellyfin = (payload) => fetchAPI(urls.testJellyfin, 'POST', payload);
export const testOverseerr = (payload) => fetchAPI(urls.testOverseerr, 'POST', payload);
export const getPlexAuthContext = () => fetchAPI(`${urls.getPlexAuthContext}?from_settings=true`);
export const checkPlexPin = (clientId, pinId) => fetchAPI(buildPinCheckUrl(urls.checkPlexPin, clientId, pinId));
export const getPlexServers = () => fetchAPI(`${urls.getPlexServers}?from_settings=true`);
export const getOnlineMediaSources = () => fetchAPI(urls.onlineMediaSources);

// --- Backup e Restauro ---
export const syncProfiles = (payload) => fetchAPI(urls.syncProfiles, 'POST', payload);
export const testGates2b = (payload) => fetchAPI(urls.testGates2b, 'POST', payload);
export const testWhatsapp = (payload) => fetchAPI(urls.testWhatsapp, 'POST', payload);
export const testPush = () => fetchAPI(urls.testPush, 'POST');
export const backupList = () => fetchAPI(urls.backupList);
export const backupDelete = (filename) => fetchAPI(urls.backupDelete.replace('__FILENAME__', encodeURIComponent(filename)), 'DELETE');
