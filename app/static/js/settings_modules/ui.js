/**
 * Módulo de UI (Interface do Utilizador)
 * Responsável por todas as manipulações diretas do DOM, como preencher formulários,
 * atualizar a exibição de logs e gerir a navegação por abas.
 */

import * as dom from './dom.js';
import * as api from './api.js';
import { i18n, fieldMap } from './config.js';
import { settingsData } from './handlers.js';
import { showToast } from '../utils.js';

let logIntervalId = null;

/**
 * Carrega as configurações da API e preenche o formulário.
 */
export async function loadSettings() {
    try {
        const config = await api.getSettings();
        populateForm(config);
    } catch (error) {
        showToast(`Falha ao carregar configurações: ${error.message}`, 'error');
    }
}

export function populateForm(config) {
    for (const [id, field] of Object.entries(fieldMap)) {
        const el = document.getElementById(id);
        if (el) {
            const key = field.key || id;
            if (field.type === 'price') {
                el.value = (config.SCREEN_PRICES && config.SCREEN_PRICES[field.key]) || '';
            } else if (field.type === 'checkbox') {
                el.checked = config[key] !== undefined ? config[key] : field.default;
            } else if (field.type === 'password') {
                const secretInfo = config[key];
                if (secretInfo && secretInfo.is_set && secretInfo.length > 0) {
                    el.value = '*'.repeat(secretInfo.length);
                    el.dataset.originalLength = secretInfo.length;
                } else {
                    el.value = '';
                    el.dataset.originalLength = '0';
                }
            } else {
                el.value = config[key] !== undefined ? config[key] : field.default;
            }
        }
    }
    if (dom.logLevelSelector) dom.logLevelSelector.value = config.LOG_LEVEL || 'INFO';
    toggleHmacSection();
}

export async function fetchAndDisplayPlexServers() {
    dom.serverSelectionContainer.classList.remove('hidden');
    dom.serverSelectionContainer.innerHTML = `<p class="text-sm text-gray-400">${i18n.fetchingServers}</p>`;

    try {
        const data = await api.getPlexServers();
        if (data.success && data.servers.length > 0) {
            settingsData.plex_token = data.token;
            let serverHtml = `<label class="block text-sm font-medium text-gray-500 dark:text-gray-400">${i18n.selectNewServer}</label>
                              <select id="server-selector" class="mt-1 block w-full p-2.5 text-sm rounded-lg border bg-gray-50 border-gray-300 text-gray-900 focus:ring-yellow-500 focus:border-yellow-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white">`;
            data.servers.forEach(server => {
                server.connections.forEach(conn => {
                    const type = conn.local ? i18n.local : i18n.remote;
                    const protocol = conn.uri.startsWith('https://') ? 'SSL' : 'HTTP';
                    serverHtml += `<option value="${conn.uri}">${server.name} - ${conn.uri} [${type} / ${protocol}]</option>`;
                });
            });
            serverHtml += `</select><button type="button" id="confirm-server-selection" class="btn bg-green-600 hover:bg-green-500 text-white mt-2">${i18n.confirmServer}</button>`;
            dom.serverSelectionContainer.innerHTML = serverHtml;

            const serverSelector = document.getElementById('server-selector');
            const confirmButton = document.getElementById('confirm-server-selection');

            const updateSettingsData = (uri) => {
                document.getElementById('plex_url_display').value = uri;
                settingsData.plex_url = uri;
            };

            if (serverSelector.options.length > 0) {
                updateSettingsData(serverSelector.value);
            }

            serverSelector.addEventListener('change', (e) => updateSettingsData(e.target.value));

            confirmButton.addEventListener('click', async () => {
                confirmButton.disabled = true;
                confirmButton.textContent = i18n.saving;
                try {
                    const result = await api.saveSettings({ plex_url: settingsData.plex_url, plex_token: settingsData.plex_token });
                    showToast(result.message, result.success ? 'success' : 'error');
                    if (result.success) {
                        dom.serverSelectionContainer.innerHTML = `<p class="text-sm text-green-500">${i18n.serverUpdated}</p>`;
                        setTimeout(() => {
                            dom.serverSelectionContainer.classList.add('hidden');
                            dom.reauthPlexButton.disabled = false;
                            dom.reauthPlexButton.innerHTML = `<svg class="w-5 h-5 mr-2" xmlns="http://www.w3.org/2000/svg" fill="currentColor" viewBox="0 0 24 24"><path d="M11.64,12.02L9.36,7.66L9.35,7.63L11.64,12L9.35,16.38L9.36,16.35L11.64,12.02M12,2C6.48,2,2,6.48,2,12C2,17.52,6.48,22,12,22C17.52,22,22,17.52,22,12C22,6.48,17.52,2,12,2M14.65,16.37H12.44L12.44,12.03L14.65,7.64H17L13.8,12.01L17,16.37H14.65Z" /></svg> ${i18n.reauthText || 'Autenticar para Buscar Servidores'}`;
                        }, 3000);
                    }
                } catch (error) {
                    showToast(error.message, 'error');
                } finally {
                    confirmButton.disabled = false;
                    confirmButton.textContent = i18n.confirmServer;
                }
            });
        } else {
            dom.serverSelectionContainer.innerHTML = `<p class="text-sm text-yellow-400">${data.message || i18n.noServersFound}</p>`;
        }
    } catch (e) {
        dom.serverSelectionContainer.innerHTML = `<p class="text-sm text-red-400">${i18n.errorGeneric} ${e.message}</p>`;
    }
}

async function fetchLogs() {
    try {
        const data = await api.getLogs();
        if (data.success) {
            dom.logDisplay.textContent = data.logs;
            dom.logDisplay.scrollTop = dom.logDisplay.scrollHeight;
        } else {
            dom.logDisplay.textContent = `${i18n.errorLoadingLogs}: ${data.message}`;
        }
    } catch (e) {
        dom.logDisplay.textContent = `${i18n.connectionError}: ${e.message}`;
    }
}

export function toggleLogUpdates() {
    if (logIntervalId) {
        clearInterval(logIntervalId);
        logIntervalId = null;
        dom.toggleLogsButton.textContent = i18n.startUpdates;
        dom.toggleLogsButton.classList.replace('bg-yellow-600', 'bg-green-600');
    } else {
        fetchLogs();
        logIntervalId = setInterval(fetchLogs, 5000);
        dom.toggleLogsButton.textContent = i18n.stopUpdates;
        dom.toggleLogsButton.classList.replace('bg-green-600', 'bg-yellow-600');
    }
}

export async function clearLogs() {
    dom.toggleLogsButton.disabled = true;
    try {
        const result = await api.clearLogs();
        if (result.success) dom.logDisplay.textContent = '';
        showToast(result.message, result.success ? 'success' : 'error');
    } catch (error) {
        showToast(`${i18n.errorGeneric}: ${error.message}`, 'error');
    } finally {
        dom.toggleLogsButton.disabled = false;
    }
}

export function toggleHmacSection() {
    if (dom.efiUseMtlsCheckbox && dom.efiHmacSection) {
        dom.efiHmacSection.classList.toggle('hidden', dom.efiUseMtlsCheckbox.checked);
    }
}

export function setupTabNavigation(navElement, contentContainer, contentSelector) {
    if (!navElement || !contentContainer) return;
    navElement.addEventListener('click', (e) => {
        const button = e.target.closest('button[data-tab], button[data-subtab]');
        if (button) {
            const isSubtab = button.dataset.subtab;
            const tabId = isSubtab || button.dataset.tab;
            const prefix = isSubtab ? 'subtab-' : 'tab-';

            navElement.querySelectorAll('.tab-button').forEach(btn => btn.classList.remove('active'));
            button.classList.add('active');
            contentContainer.querySelectorAll(contentSelector).forEach(content => content.classList.remove('active'));
            document.getElementById(`${prefix}${tabId}`).classList.add('active');

            if (tabId === 'logs' && !isSubtab) {
                if (!logIntervalId) toggleLogUpdates(); // Inicia se não estiver a correr
            } else if (!isSubtab) {
                if (logIntervalId) toggleLogUpdates(); // Para se estiver a correr e mudamos de aba principal
            }
        }
    });
}

