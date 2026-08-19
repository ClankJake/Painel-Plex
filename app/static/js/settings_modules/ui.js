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
import { renderLevelEditor, renderResetMonthsGrid } from './gamification.js';

let logIntervalId = null;
let lastLogContent = ""; // Evita re-renderizações desnecessárias e pulos no scroll

/**
 * Carrega as configurações da API e preenche o formulário.
 */
export async function loadSettings() {
    try {
        const config = await api.getSettings();
        populateForm(config);
        populateTabsSelect();
        syncTabsSelect();
        renderLevelEditor(config.XP_LEVEL_TABLE);
        renderResetMonthsGrid(config.XP_RESET_MONTHS);
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
    dom.serverSelectionContainer.innerHTML = `
        <div class="flex items-center justify-center py-4 text-yellow-500">
            <svg class="animate-spin h-5 w-5 mr-3" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" fill="none"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
            <p class="text-sm font-medium text-gray-500 dark:text-gray-400">${i18n.fetchingServers || 'A procurar servidores...'}</p>
        </div>`;

    try {
        const data = await api.getPlexServers();
        if (data.success && data.servers.length > 0) {
            settingsData.plex_token = data.token;
            
            let serverHtml = `
                <div class="bg-gray-50 dark:bg-gray-800/50 p-4 rounded-xl border border-gray-200 dark:border-gray-700/50 mt-4">
                    <label class="block text-sm font-bold text-gray-700 dark:text-gray-300 mb-2">${i18n.selectNewServer || 'Selecione o Servidor'}</label>
                    <div class="relative">
                        <select id="server-selector" class="block w-full pl-3 pr-10 py-2.5 text-base rounded-lg border-gray-300 focus:outline-none focus:ring-2 focus:ring-yellow-500 focus:border-yellow-500 sm:text-sm dark:bg-gray-900 dark:border-gray-600 dark:text-white transition-shadow cursor-pointer appearance-none">`;
            
            data.servers.forEach(server => {
                server.connections.forEach(conn => {
                    const type = conn.local ? 'LAN' : 'WAN';
                    const protocol = conn.uri.startsWith('https://') ? 'SSL' : 'HTTP';
                    serverHtml += `<option value="${conn.uri}">${server.name} — ${conn.uri} [${type} / ${protocol}]</option>`;
                });
            });
            
            serverHtml += `
                        </select>
                        <div class="pointer-events-none absolute inset-y-0 right-0 flex items-center px-3 text-gray-500">
                            <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path></svg>
                        </div>
                    </div>
                    <button type="button" id="confirm-server-selection" class="mt-4 w-full sm:w-auto btn bg-green-600 hover:bg-green-500 text-white font-bold py-2 px-4 rounded-lg shadow-sm transition-colors">
                        ${i18n.confirmServer || 'Confirmar Servidor'}
                    </button>
                </div>`;
                
            dom.serverSelectionContainer.innerHTML = serverHtml;

            const serverSelector = document.getElementById('server-selector');
            const confirmButton = document.getElementById('confirm-server-selection');

            const updateSettingsData = (uri) => {
                document.getElementById('plex_url_display').value = uri;
                settingsData.plex_url = uri;
            };

            if (serverSelector.options.length > 0) updateSettingsData(serverSelector.value);
            serverSelector.addEventListener('change', (e) => updateSettingsData(e.target.value));

            confirmButton.addEventListener('click', async () => {
                confirmButton.disabled = true;
                confirmButton.innerHTML = `<svg class="animate-spin h-4 w-4 mr-2 inline" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" fill="none"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg> ${i18n.saving || 'A gravar...'}`;
                try {
                    const result = await api.saveSettings({ plex_url: settingsData.plex_url, plex_token: settingsData.plex_token });
                    showToast(result.message, result.success ? 'success' : 'error');
                    if (result.success) {
                        dom.serverSelectionContainer.innerHTML = `<p class="text-sm font-medium text-green-500 flex items-center"><svg class="w-5 h-5 mr-1" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd"></path></svg> ${i18n.serverUpdated || 'Servidor Atualizado'}</p>`;
                        setTimeout(() => {
                            dom.serverSelectionContainer.classList.add('hidden');
                            dom.reauthPlexButton.disabled = false;
                            dom.reauthPlexButton.innerHTML = `<svg class="w-5 h-5 mr-2" xmlns="http://www.w3.org/2000/svg" fill="currentColor" viewBox="0 0 24 24"><path d="M11.64,12.02L9.36,7.66L9.35,7.63L11.64,12L9.35,16.38L9.36,16.35L11.64,12.02M12,2C6.48,2,2,6.48,2,12C2,17.52,6.48,22,12,22C17.52,22,22,17.52,22,12C22,6.48,17.52,2,12,2M14.65,16.37H12.44L12.44,12.03L14.65,7.64H17L13.8,12.01L17,16.37H14.65Z" /></svg> ${i18n.reauthText || 'Autenticar para Buscar Servidores'}`;
                        }, 2500);
                    }
                } catch (error) {
                    showToast(error.message, 'error');
                } finally {
                    if (dom.serverSelectionContainer.querySelector('#confirm-server-selection')) {
                        confirmButton.disabled = false;
                        confirmButton.textContent = i18n.confirmServer || 'Confirmar Servidor';
                    }
                }
            });
        } else {
            dom.serverSelectionContainer.innerHTML = `
                <div class="bg-yellow-50 dark:bg-yellow-900/20 p-4 rounded-xl border border-yellow-200 dark:border-yellow-800/50 mt-4 text-yellow-700 dark:text-yellow-400 text-sm">
                    ${data.message || i18n.noServersFound}
                </div>`;
        }
    } catch (e) {
        dom.serverSelectionContainer.innerHTML = `
            <div class="bg-red-50 dark:bg-red-900/20 p-4 rounded-xl border border-red-200 dark:border-red-800/50 mt-4 text-red-600 dark:text-red-400 text-sm">
                ${i18n.errorGeneric} ${e.message}
            </div>`;
    }
}

/**
 * Formata uma linha de log para adicionar cores baseadas no nível de severidade e syntax highlighting.
 */
function formatLogLine(line) {
    if (!line) return '';
    
    // Sanitização rápida contra XSS
    let escapedLine = line.replace(/[&<>"']/g, m => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'
    })[m]);

    let wrapperClass = 'text-gray-300'; 
    let badgeClass = '';

    // Syntax Highlighting Avançado
    if (escapedLine.includes('CRITICAL')) {
        wrapperClass = 'text-red-400';
        badgeClass = 'bg-red-900/50 text-red-200 px-1.5 py-0.5 rounded-md font-bold text-xs mr-2';
        escapedLine = escapedLine.replace('CRITICAL', `<span class="${badgeClass}">CRITICAL</span>`);
    } else if (escapedLine.includes('ERROR')) {
        wrapperClass = 'text-red-400/90';
        badgeClass = 'text-red-500 font-bold';
        escapedLine = escapedLine.replace('ERROR', `<span class="${badgeClass}">ERROR</span>`);
    } else if (escapedLine.includes('WARNING')) {
        wrapperClass = 'text-yellow-200/80';
        badgeClass = 'text-yellow-400 font-bold';
        escapedLine = escapedLine.replace('WARNING', `<span class="${badgeClass}">WARNING</span>`);
    } else if (escapedLine.includes('INFO')) {
        wrapperClass = 'text-gray-300';
        badgeClass = 'text-blue-400 font-bold';
        escapedLine = escapedLine.replace('INFO', `<span class="${badgeClass}">INFO</span>`);
    } else if (escapedLine.includes('DEBUG')) {
        wrapperClass = 'text-gray-500';
        badgeClass = 'text-gray-400 font-semibold';
        escapedLine = escapedLine.replace('DEBUG', `<span class="${badgeClass}">DEBUG</span>`);
    }

    // Realça os timestamps (ex: 2026-02-26 13:09:00,135)
    escapedLine = escapedLine.replace(/^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})/, 
        '<span class="text-gray-500/80 mr-2 border-r border-gray-700/50 pr-2">$1</span>');

    return `<div class="${wrapperClass} font-mono text-sm leading-relaxed mb-0.5 break-words">${escapedLine}</div>`;
}

async function fetchLogs() {
    try {
        const data = await api.getLogs();
        if (data.success) {
            // OPTIMIZAÇÃO: Só atualiza o DOM e força o scroll se o texto do log for diferente.
            // Isso permite que o utilizador consiga ler/fazer scroll para cima sem o texto saltar a cada 5s.
            if (data.logs !== lastLogContent) {
                lastLogContent = data.logs;
                const formattedLogs = data.logs.split('\n').map(formatLogLine).join('');
                
                dom.logDisplay.innerHTML = formattedLogs;
                dom.logDisplay.scrollTop = dom.logDisplay.scrollHeight;
            }
        } else {
            dom.logDisplay.innerHTML = `<span class="text-red-400">${i18n.errorLoadingLogs || 'Erro'}: ${data.message}</span>`;
        }
    } catch (e) {
        dom.logDisplay.innerHTML = `<span class="text-red-400">${i18n.connectionError || 'Falha de Conexão'}: ${e.message}</span>`;
    }
}

export function toggleLogUpdates() {
    if (logIntervalId) {
        clearInterval(logIntervalId);
        logIntervalId = null;
        dom.toggleLogsButton.textContent = i18n.startUpdates || 'Iniciar Atualizações Auto';
        dom.toggleLogsButton.classList.replace('bg-yellow-600', 'bg-green-600');
        dom.toggleLogsButton.classList.replace('hover:bg-yellow-500', 'hover:bg-green-500');
    } else {
        fetchLogs();
        logIntervalId = setInterval(fetchLogs, 5000);
        dom.toggleLogsButton.textContent = i18n.stopUpdates || 'Parar Atualizações Auto';
        dom.toggleLogsButton.classList.replace('bg-green-600', 'bg-yellow-600');
        dom.toggleLogsButton.classList.replace('hover:bg-green-500', 'hover:bg-yellow-500');
    }
}

export async function clearLogs() {
    dom.clearLogsButton.disabled = true;
    dom.toggleLogsButton.disabled = true; 
    try {
        const result = await api.clearLogs();
        if (result.success) {
            lastLogContent = "";
            dom.logDisplay.innerHTML = `<span class="text-gray-500 italic">Logs apagados com sucesso. Aguardando novos eventos...</span>`;
        }
        showToast(result.message, result.success ? 'success' : 'error');
    } catch (error) {
        showToast(`${i18n.errorGeneric}: ${error.message}`, 'error');
    } finally {
        dom.clearLogsButton.disabled = false;
        dom.toggleLogsButton.disabled = false; 
    }
}

export function toggleHmacSection() {
    if (dom.efiUseMtlsCheckbox && dom.efiHmacSection) {
        // Usa transição suave se a classe transition estiver no container
        if(dom.efiUseMtlsCheckbox.checked) {
            dom.efiHmacSection.classList.add('hidden');
        } else {
            dom.efiHmacSection.classList.remove('hidden');
        }
    }
}

/**
 * Configura a navegação entre abas.
 */
export function setupTabNavigation(navElement, contentContainer, contentSelector) {
    if (!navElement || !contentContainer) return;
    navElement.addEventListener('click', (e) => {
        const button = e.target.closest('button[data-tab], button[data-subtab]');
        if (button) {
            handleTabChange(button, navElement, contentContainer, contentSelector);
        }
    });
}

function handleTabChange(clickedButton, navElement, contentContainer, contentSelector) {
    const isSubtab = clickedButton.dataset.subtab;
    const tabId = isSubtab || clickedButton.dataset.tab;
    const prefix = isSubtab ? 'subtab-' : 'tab-';

    // Remove estado ativo de todos os botões no grupo de navegação
    navElement.querySelectorAll('.tab-button, button[data-subtab]').forEach(btn => {
        btn.classList.remove('active', 'bg-white', 'dark:bg-gray-700', 'text-gray-900', 'dark:text-white', 'shadow-sm', 'ring-1', 'ring-gray-200', 'dark:ring-gray-600');
        if (!btn.classList.contains('text-gray-500')) {
            btn.classList.add('text-gray-500', 'dark:text-gray-400'); // Devolve o aspeto apagado
        }
    });
    
    // Aplica aspeto "Premium" de ativo ao botão clicado
    clickedButton.classList.remove('text-gray-500', 'dark:text-gray-400');
    clickedButton.classList.add('active', 'bg-white', 'dark:bg-gray-700', 'text-gray-900', 'dark:text-white', 'shadow-sm', 'ring-1', 'ring-gray-200', 'dark:ring-gray-600');

    // Esconde todos os conteúdos
    contentContainer.querySelectorAll(contentSelector).forEach(content => {
        content.classList.add('hidden');
        content.classList.remove('active');
    });
    
    // Mostra o conteúdo específico
    const contentElement = document.getElementById(`${prefix}${tabId}`);
    if (contentElement) {
        contentElement.classList.remove('hidden');
        contentElement.classList.add('active'); // Opcional se usar Tailwind fade-in animations
    }

    // Gestão inteligente do Polling de Logs (Só faz polling se a aba de logs estiver aberta)
    if (tabId === 'logs' && !isSubtab) {
        if (!logIntervalId) toggleLogUpdates(); 
    } else if (!isSubtab) {
        if (logIntervalId) toggleLogUpdates(); 
    }

    if (!isSubtab) syncTabsSelect();
}

/**
 * Preenche as opções do select dropdown em ecrãs móveis baseando-se nas abas de Desktop.
 */
export function populateTabsSelect() {
    if (!dom.mainTabsSelect || !dom.mainTabs) return;

    dom.mainTabsSelect.innerHTML = ''; 
    dom.mainTabs.querySelectorAll('.tab-button').forEach(button => {
        const option = document.createElement('option');
        option.value = button.dataset.tab;
        // Pega o texto da span ignorando o ícone SVG
        const span = button.querySelector('span');
        option.textContent = span ? span.textContent : button.textContent;
        dom.mainTabsSelect.appendChild(option);
    });
}

export function syncTabsSelect() {
    if (!dom.mainTabsSelect || !dom.mainTabs) return;
    const activeButton = dom.mainTabs.querySelector('button[data-tab].active');
    if (activeButton) {
        dom.mainTabsSelect.value = activeButton.dataset.tab;
    }
}

export function handleTabsSelectChange(event) {
    const selectedTabId = event.target.value;
    const correspondingButton = dom.mainTabs.querySelector(`button[data-tab="${selectedTabId}"]`);
    if (correspondingButton) {
        handleTabChange(correspondingButton, dom.mainTabs, dom.mainTabContent, '.tab-content');
    }
}
