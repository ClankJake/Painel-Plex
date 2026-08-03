/**
 * Módulo de Handlers (Manipuladores de Eventos)
 * Contém a lógica que é executada em resposta às interações do utilizador na página de configurações.
 */

import * as dom from './dom.js';
import * as api from './api.js';
import * as ui from './ui.js';
import { i18n, fieldMap } from './config.js';
import { showToast } from '../utils.js';

let pinCheckInterval = null;
let authWindow = null;
export let settingsData = { plex_url: null, plex_token: null };

const getSpinner = (classes = "w-4 h-4 mr-2") => `<svg class="animate-spin inline ${classes}" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>`;

async function handleSaveSettings(e) {
    e.preventDefault();
    if (!dom.saveButton) return;

    const originalText = dom.saveButton.textContent;
    dom.saveButton.disabled = true;
    dom.saveButton.innerHTML = `${getSpinner()} ${i18n.saving || 'A guardar...'}`;

    const newConfig = {};
    const screenPrices = {};

    for (const [id, field] of Object.entries(fieldMap)) {
        const el = document.getElementById(id);
        if (el) {
            if (field.type === 'price') {
                const priceValue = parseFloat(el.value.replace(',', '.'));
                if (!isNaN(priceValue) && priceValue > 0) {
                    screenPrices[field.key] = priceValue.toFixed(2);
                }
            } else if (!field.readonly) {
                const key = field.key || id;
                if (key.includes('_BULK_MESSAGE_TEMPLATE')) continue;

                if (field.type === 'checkbox') {
                    newConfig[key] = el.checked;
                } else if (field.type === 'number') {
                    newConfig[key] = parseInt(el.value, 10) || 0;
                } else if (field.type === 'password') {
                    const originalLength = parseInt(el.dataset.originalLength || '0', 10);
                    const isPlaceholder = el.value === '*'.repeat(originalLength);
                    if (!isPlaceholder) {
                        newConfig[key] = el.value;
                    }
                } else {
                    newConfig[key] = el.value;
                }
            }
        }
    }
    newConfig.SCREEN_PRICES = screenPrices;
    if (dom.logLevelSelector) newConfig.LOG_LEVEL = dom.logLevelSelector.value;

    if (settingsData.plex_url && settingsData.plex_token) {
        newConfig.plex_url = settingsData.plex_url;
        newConfig.plex_token = settingsData.plex_token;
    }

    try {
        const result = await api.saveSettings(newConfig);
        showToast(result.message, result.success ? 'success' : 'error');
        if (result.success) ui.loadSettings();
    } catch (error) {
        showToast(error.message || i18n.unknownError || 'Erro desconhecido.', 'error');
    } finally {
        dom.saveButton.disabled = false;
        dom.saveButton.textContent = originalText;
    }
}

async function handleSaveBulkTemplates() {
    if (!dom.saveBulkTemplatesButton) return;
    
    const originalText = dom.saveBulkTemplatesButton.textContent;
    dom.saveBulkTemplatesButton.disabled = true;
    dom.saveBulkTemplatesButton.innerHTML = `${getSpinner()} ${i18n.savingTemplates || 'A gravar...'}`;

    const templateData = {
        'TELEGRAM_BULK_MESSAGE_TEMPLATE': document.getElementById('TELEGRAM_BULK_MESSAGE_TEMPLATE')?.value || '',
        'DISCORD_BULK_MESSAGE_TEMPLATE': document.getElementById('DISCORD_BULK_MESSAGE_TEMPLATE')?.value || '',
        'WEBHOOK_BULK_MESSAGE_TEMPLATE': document.getElementById('WEBHOOK_BULK_MESSAGE_TEMPLATE')?.value || '',
    };

    try {
        const result = await api.saveSettings(templateData);
        showToast(result.message, result.success ? 'success' : 'error');
    } catch (error) {
        showToast(error.message || i18n.unknownError, 'error');
    } finally {
        dom.saveBulkTemplatesButton.disabled = false;
        dom.saveBulkTemplatesButton.textContent = originalText;
    }
}

async function handleTestConnection(button, endpoint, payloadBuilder) {
    if (!button) return;

    const originalHTML = button.innerHTML;
    button.disabled = true;
    button.innerHTML = `${getSpinner("w-4 h-4 mr-1")} ${i18n.testing || 'Testando...'}`;

    try {
        const apiFunction = api[endpoint];
        if (typeof apiFunction !== 'function') {
            throw new Error(`Função da API desconhecida: ${endpoint}`);
        }
        const result = await apiFunction(payloadBuilder());
        showToast(result.message, result.success ? 'success' : 'error');
    } catch (error) {
        showToast(error.message || i18n.unknownError, 'error');
    } finally {
        button.disabled = false;
        button.innerHTML = originalHTML;
    }
}

async function handlePlexAuth() {
    const originalButtonHTML = dom.reauthPlexButton.innerHTML;
    const restoreButton = () => {
        dom.reauthPlexButton.disabled = false;
        dom.reauthPlexButton.innerHTML = originalButtonHTML;
    };

    dom.reauthPlexButton.disabled = true;
    dom.reauthPlexButton.innerHTML = `${getSpinner("w-5 h-5 mr-3 text-yellow-500")} ${i18n.verifying || 'A aguardar autenticação...'}`;

    if (pinCheckInterval) clearInterval(pinCheckInterval);

    try {
        const contextData = await api.getPlexAuthContext();
        const { product_name, client_id } = contextData;
        const plexHeaders = { 'X-Plex-Product': product_name, 'X-Plex-Client-Identifier': client_id, 'Accept': 'application/json' };
        
        const plexResponse = await fetch("https://plex.tv/api/v2/pins?strong=true", { method: 'POST', headers: plexHeaders });
        if (!plexResponse.ok) throw new Error('Falha ao criar PIN de autenticação com a Plex.tv.');
        
        const pinData = await plexResponse.json();
        const { id: pin_id, code: pin_code } = pinData;

        const authUrlParams = new URLSearchParams({ 
            'clientID': client_id, 
            'code': pin_code, 
            'context[device][product]': product_name, 
            'context[device][deviceName]': product_name, 
            'context[device][platform]': 'Web' 
        });
        const auth_url = `https://app.plex.tv/auth#?${authUrlParams.toString()}`;
        
        const width = 800;
        const height = 700;
        const left = (window.innerWidth / 2) - (width / 2);
        const top = (window.innerHeight / 2) - (height / 2);
        authWindow = window.open(auth_url, 'plexAuth', `width=${width},height=${height},top=${top},left=${left}`);

        let attempts = 0;
        const maxAttempts = 100;

        pinCheckInterval = setInterval(async () => {
            attempts++;
            
            if (!authWindow || authWindow.closed || attempts > maxAttempts) {
                clearInterval(pinCheckInterval);
                if (attempts > maxAttempts) {
                    showToast('Tempo limite para autenticação excedido.', 'warning');
                    if (authWindow && !authWindow.closed) authWindow.close();
                }
                restoreButton();
                return;
            }
            
            try {
                const checkData = await api.checkPlexPin(client_id, pin_id);
                if (checkData.success) {
                    clearInterval(pinCheckInterval);
                    if (authWindow && !authWindow.closed) authWindow.close();
                    
                    showToast(i18n.authenticated || 'Autenticado com sucesso!', 'success');
                    await ui.fetchAndDisplayPlexServers();
                } else if (checkData.message === 'auth_denied') {
                    clearInterval(pinCheckInterval);
                    if (authWindow && !authWindow.closed) authWindow.close();
                    
                    showToast(checkData.error || 'Autenticação negada pelo utilizador.', 'error');
                    restoreButton();
                }
            } catch (e) {
                console.warn(`Verificação do Plex Pin falhou na tentativa ${attempts}: ${e.message}`);
            }
        }, 3000);
        
    } catch (error) {
        showToast(error.message, 'error');
        restoreButton();
    }
}

export function initializeEventListeners() {
    if (dom.form) dom.form.addEventListener('submit', handleSaveSettings);
    
    if (dom.saveBulkTemplatesButton) {
        dom.saveBulkTemplatesButton.addEventListener('click', handleSaveBulkTemplates);
    }
    
    // Removido listener do Tautulli
    
    if (dom.testOverseerrButton) {
        dom.testOverseerrButton.addEventListener('click', () => handleTestConnection(dom.testOverseerrButton, 'testOverseerr', () => ({ 
            url: document.getElementById('OVERSEERR_URL')?.value, 
            api_key: document.getElementById('OVERSEERR_API_KEY')?.value 
        })));
    }
    
    if (dom.reauthPlexButton) dom.reauthPlexButton.onclick = handlePlexAuth;
    if (dom.languageSelector) dom.languageSelector.addEventListener('change', (e) => window.location.href = `/language/${e.target.value}`);
    if (dom.toggleLogsButton) dom.toggleLogsButton.addEventListener('click', () => ui.toggleLogUpdates());
    if (dom.clearLogsButton) dom.clearLogsButton.addEventListener('click', ui.clearLogs);
    if (dom.efiUseMtlsCheckbox) dom.efiUseMtlsCheckbox.addEventListener('change', ui.toggleHmacSection);
    
    if (dom.generateHmacButton) {
        dom.generateHmacButton.addEventListener('click', () => {
            const randomString = Array.from(crypto.getRandomValues(new Uint8Array(20))).map(b => b.toString(16).padStart(2, '0')).join('');
            if(dom.hmacInput) {
                dom.hmacInput.value = randomString;
                dom.hmacInput.classList.add('ring-2', 'ring-green-500', 'bg-green-50', 'dark:bg-green-900/30');
                setTimeout(() => dom.hmacInput.classList.remove('ring-2', 'ring-green-500', 'bg-green-50', 'dark:bg-green-900/30'), 500);
            }
        });
    }

    if (dom.mainTabsSelect) {
        dom.mainTabsSelect.addEventListener('change', ui.handleTabsSelectChange);
    }

    document.querySelectorAll('.show-help-button').forEach(button => button.addEventListener('click', () => dom.helpModal?.classList.remove('hidden')));
    if (dom.closeHelpModalButton) dom.closeHelpModalButton.addEventListener('click', () => dom.helpModal?.classList.add('hidden'));

    ui.setupTabNavigation(dom.mainTabs, dom.mainTabContent, '.tab-content');
    ui.setupTabNavigation(dom.paymentTabs, dom.paymentTabContent, '.sub-tab-content');
    ui.setupTabNavigation(dom.notificationTabs, dom.notificationTabContent, '.sub-tab-content');
    ui.setupTabNavigation(dom.comunicacoesTabs, dom.comunicacoesTabContent, '.sub-tab-content');
}
