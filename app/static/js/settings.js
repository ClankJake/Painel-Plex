import { fetchAPI, showToast } from './utils.js';

document.addEventListener('DOMContentLoaded', () => {
    // --- ELEMENTOS E DADOS GLOBAIS ---
    const scriptTag = document.getElementById('settings-script');
    
    const urls = {};
    const i18n = {};
    if (scriptTag) {
        for (const key in scriptTag.dataset) {
            if (key.startsWith('urls')) {
                const subKey = key.substring(4);
                const urlKey = subKey.charAt(0).toLowerCase() + subKey.slice(1).replace(/-(\w)/g, (match, letter) => letter.toUpperCase());
                urls[urlKey] = scriptTag.dataset[key];
            } else if (key.startsWith('i18n')) {
                const i18nKey = key.charAt(4).toLowerCase() + key.slice(5);
                i18n[i18nKey] = scriptTag.dataset[key];
            }
        }
    }
    
    const settingsData = { plex_url: null, plex_token: null };
    let pinCheckInterval = null;
    let authWindow = null;
    let logIntervalId = null;

    // --- ELEMENTOS DO DOM ---
    const form = document.getElementById('settingsForm');
    const saveButton = document.getElementById('saveButton');
    const logLevelSelector = document.getElementById('log_level_selector');
    const testTautulliButton = document.getElementById('testTautulliButton');
    const testOverseerrButton = document.getElementById('testOverseerrButton');
    
    // --- LÓGICA DE AUTENTICAÇÃO PLEX ---
    
    async function loginWithPlex() {
        const reauthButton = document.getElementById('reauth-plex-button');
        const originalButtonHTML = reauthButton.innerHTML; // Armazena o conteúdo original do botão

        // Função para restaurar o botão ao seu estado original
        const restoreButton = () => {
            if (reauthButton) {
                reauthButton.disabled = false;
                reauthButton.innerHTML = originalButtonHTML;
            }
        };

        if(reauthButton) {
            reauthButton.disabled = true;
            reauthButton.innerHTML = `<svg class="animate-spin h-5 w-5 mr-3" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" class="opacity-25"></circle><path d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" fill="currentColor" class="opacity-75"></path></svg> ${i18n.verifying}`;
        }
        
        if (pinCheckInterval) clearInterval(pinCheckInterval);

        try {
            const contextResponse = await fetch(`${urls.getPlexAuthContext}?from_settings=true`);
            if (!contextResponse.ok) throw new Error(i18n.errorGeneric);
            const contextData = await contextResponse.json();
            if (!contextData.success) throw new Error(contextData.message);
            
            const { product_name, client_id } = contextData;

            const plexHeaders = {
                'X-Plex-Product': product_name,
                'X-Plex-Client-Identifier': client_id,
                'Accept': 'application/json'
            };
            const plexResponse = await fetch("https://plex.tv/api/v2/pins?strong=true", {
                method: 'POST',
                headers: plexHeaders
            });
            if (!plexResponse.ok) throw new Error('Falha ao criar PIN de autenticação com o Plex.');
            const pinData = await plexResponse.json();
            const { id: pin_id, code: pin_code } = pinData;

            const authUrlParams = new URLSearchParams({
                'clientID': client_id,
                'code': pin_code,
                'context[device][product]': product_name,
                'context[device][deviceName]': product_name,
                'context[device][platform]': 'Web',
            });
            const auth_url = `https://app.plex.tv/auth#?${authUrlParams.toString()}`;
            authWindow = window.open(auth_url, 'plexAuth', 'width=800,height=700');

            pinCheckInterval = setInterval(async () => {
                if (!authWindow || authWindow.closed) {
                    clearInterval(pinCheckInterval);
                    restoreButton(); // Restaura o botão se a janela for fechada
                    return;
                }
                
                try {
                    const checkUrl = urls.checkPlexPin.replace('__CLIENT_ID__', client_id).replace('999999', pin_id);
                    const checkResponse = await fetch(checkUrl);
                    const checkData = await checkResponse.json();

                    if (checkData.success) {
                        clearInterval(pinCheckInterval);
                        if(authWindow && !authWindow.closed) authWindow.close();
                        showToast(i18n.authenticated, 'success');
                        await fetchPlexServersForSelection();
                    } else if (checkData.message === 'auth_denied') {
                        clearInterval(pinCheckInterval);
                        if(authWindow && !authWindow.closed) authWindow.close();
                        showToast(checkData.error, 'error');
                        restoreButton(); // Restaura o botão em caso de negação
                    }
                } catch (e) {
                    clearInterval(pinCheckInterval);
                    showToast(`${i18n.verificationError} ${e.message}`, 'error');
                    restoreButton(); // Restaura o botão em caso de erro na verificação
                }
            }, 3000);

        } catch (error) {
            showToast(error.message, 'error');
            restoreButton(); // Restaura o botão em caso de erro inicial
        }
    }

    function fetchPlexServersForSelection() {
        const serverSelectionContainer = document.getElementById('server-selection-container');
        if (!serverSelectionContainer) return;

        serverSelectionContainer.classList.remove('hidden');
        serverSelectionContainer.innerHTML = `<p class="text-sm text-gray-400">${i18n.fetchingServers}</p>`;
        
        fetchAPI(`${urls.getPlexServers}?from_settings=true`)
            .then(data => {
                if (data.success && data.servers.length > 0) {
                    settingsData.plex_token = data.token;
                    let serverHtml = `<label class="block text-sm font-medium text-gray-500 dark:text-gray-400">${i18n.selectNewServer}</label>
                                      <select id="server-selector" class="mt-1 block w-full p-2.5 text-sm rounded-lg border bg-gray-50 border-gray-300 text-gray-900 focus:ring-yellow-500 focus:border-yellow-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white">`;
                    data.servers.forEach(server => {
                        serverHtml += `<option value="${server.uri}">${server.name}</option>`;
                    });
                    serverHtml += `</select>
                                 <button type="button" id="confirm-server-selection" class="btn bg-green-600 hover:bg-green-500 text-white mt-2">${i18n.confirmServer}</button>`;
                    serverSelectionContainer.innerHTML = serverHtml;
                    
                    const serverSelector = document.getElementById('server-selector');
                    const plexUrlDisplay = document.getElementById('plex_url_display');
                    const confirmButton = document.getElementById('confirm-server-selection');
                    
                    const updateDisplayAndData = (uri) => { 
                        if(plexUrlDisplay) plexUrlDisplay.value = uri;
                        settingsData.plex_url = uri;
                    };
                    
                    if (serverSelector && serverSelector.options.length > 0) {
                        updateDisplayAndData(serverSelector.value);
                    }
                    
                    if(serverSelector) {
                        serverSelector.addEventListener('change', (e) => {
                            updateDisplayAndData(e.target.value);
                        });
                    }

                    if(confirmButton) {
                        confirmButton.addEventListener('click', async () => {
                            confirmButton.disabled = true;
                            confirmButton.textContent = i18n.saving;

                            try {
                                const result = await fetchAPI(urls.apiSettings, 'POST', {
                                    plex_url: settingsData.plex_url,
                                    plex_token: settingsData.plex_token
                                });
                                showToast(result.message, result.success ? 'success' : 'error');
                                if (result.success) {
                                    serverSelectionContainer.innerHTML = `<p class="text-sm text-green-500">${i18n.serverUpdated}</p>`;
                                    setTimeout(() => {
                                        serverSelectionContainer.classList.add('hidden');
                                        // ### INÍCIO DA CORREÇÃO ###
                                        // Restaura o botão original após o sucesso
                                        const reauthButton = document.getElementById('reauth-plex-button');
                                        if (reauthButton) {
                                            reauthButton.disabled = false;
                                            reauthButton.innerHTML = `<svg class="w-5 h-5 mr-2" xmlns="http://www.w3.org/2000/svg" fill="currentColor" viewBox="0 0 24 24"><path d="M11.64,12.02C11.64,12.02,11.64,12.02,11.64,12.02L9.36,7.66L9.35,7.63C9.35,7.63,9.35,7.63,9.35,7.63L11.64,12L9.35,16.38C9.35,16.38,9.35,16.38,9.35,16.38L9.36,16.35L11.64,12.02M12,2C6.48,2,2,6.48,2,12C2,17.52,6.48,22,12,22C17.52,22,22,17.52,22,12C22,6.48,17.52,2,12,2M14.65,16.37H12.44L12.44,12.03L14.65,7.64H17L13.8,12.01L17,16.37H14.65Z" /></svg> ${i18n.reauthText || 'Autenticar para Buscar Servidores'}`;
                                        }
                                        // ### FIM DA CORREÇÃO ###
                                    }, 3000);
                                }
                            } catch (error) {
                                showToast(error.message, 'error');
                            } finally {
                                confirmButton.disabled = false;
                                confirmButton.textContent = i18n.confirmServer;
                            }
                        });
                    }

                } else {
                    serverSelectionContainer.innerHTML = `<p class="text-sm text-yellow-400">${data.message || i18n.noServersFound}</p>`;
                }
            })
            .catch(e => {
                if(serverSelectionContainer) {
                    serverSelectionContainer.innerHTML = `<p class="text-sm text-red-400">${i18n.errorGeneric} ${e.message}</p>`
                }
            });
    }
    
    // --- LÓGICA DE LOGS ---
    function fetchLogs() {
        const logDisplay = document.getElementById('log-display');
        if(!logDisplay) return;
        fetchAPI(urls.getLogs)
            .then(data => {
                if (data.success) {
                    logDisplay.textContent = data.logs;
                    logDisplay.scrollTop = logDisplay.scrollHeight;
                } else {
                    logDisplay.textContent = `${i18n.errorLoadingLogs}: ${data.message}`;
                }
            })
            .catch(e => logDisplay.textContent = `${i18n.connectionError}: ${e.message}`);
    }

    function startLogUpdates() {
        const button = document.getElementById('toggle-logs');
        if (!logIntervalId) {
            fetchLogs();
            logIntervalId = setInterval(fetchLogs, 5000);
            if(button) {
                button.textContent = i18n.stopUpdates;
                button.classList.replace('bg-green-600', 'bg-yellow-600');
            }
        }
    }
    
    function stopLogUpdates() {
        const button = document.getElementById('toggle-logs');
        if (logIntervalId) {
            clearInterval(logIntervalId);
            logIntervalId = null;
            if(button) {
                button.textContent = i18n.startUpdates;
                button.classList.replace('bg-yellow-600', 'bg-green-600');
            }
        }
    }

    // --- LÓGICA DE AUTO-CONFIGURAÇÃO DO TAUTULLI ---
    async function autoConfigureNotifier(button) {
        const notifierType = button.dataset.notifierType;
        const targetId = button.dataset.targetId;
        const notifierIdInput = document.getElementById(targetId);
        if(!notifierIdInput) return;

        const notifierId = notifierIdInput.value;

        if (!notifierId || notifierId === '0') {
            showToast(i18n.provideNotifierId, 'error');
            return;
        }
        
        button.disabled = true;
        button.textContent = i18n.configuring;

        try {
            const result = await fetchAPI(urls.autoConfigureTautulli, 'POST', {
                notifier_id: notifierId,
                notifier_type: notifierType,
                url: document.getElementById('TAUTULLI_URL').value,
                api_key: document.getElementById('TAUTULLI_API_KEY').value,
            });
            showToast(result.message, result.success ? 'success' : 'error');
        } catch(error) {
            showToast(error.message, 'error');
        } finally {
            button.disabled = false;
            button.textContent = i18n.autoConfigure;
        }
    }

    // --- INICIALIZAÇÃO DA PÁGINA ---
    
    const fieldMap = {
        'APP_TITLE': { type: 'text', default: 'Painel Plex' },
        'APP_HOST': { type: 'text', default: '0.0.0.0' },
        'APP_PORT': { type: 'number', default: 5000 },
        'APP_BASE_URL': { type: 'text', default: 'http://127.0.0.1:5000' },
        'DAYS_TO_REMOVE_BLOCKED_USER': { type: 'number', default: 0 },
        'EXPIRATION_NOTIFICATION_TIME': { type: 'text', default: '09:00' },
        'BLOCK_REMOVAL_TIME': { type: 'text', default: '02:00' },
        'TELEGRAM_ENABLED': { type: 'checkbox', default: false },
        'WEBHOOK_ENABLED': { type: 'checkbox', default: false },
        'WEBHOOK_URL': { type: 'text', default: '' },
        'WEBHOOK_AUTHORIZATION_HEADER': { type: 'text', default: '' },
        'WEBHOOK_EXPIRATION_MESSAGE_TEMPLATE': { type: 'textarea', default: '' },
        'WEBHOOK_RENEWAL_MESSAGE_TEMPLATE': { type: 'textarea', default: '' },
        'WEBHOOK_TRIAL_END_MESSAGE_TEMPLATE': { type: 'textarea', default: '' },
        'TELEGRAM_BOT_TOKEN': { type: 'password', default: '' },
        'TELEGRAM_CHAT_ID': { type: 'text', default: '' },
        'DAYS_TO_NOTIFY_EXPIRATION': { type: 'number', default: 2 },
        'TELEGRAM_EXPIRATION_MESSAGE_TEMPLATE': { type: 'textarea', default: '' },
        'TELEGRAM_RENEWAL_MESSAGE_TEMPLATE': { type: 'textarea', default: '' },
        'TELEGRAM_TRIAL_END_MESSAGE_TEMPLATE': { type: 'textarea', default: '' },
        'plex_url_display': { type: 'text', readonly: true, key: 'PLEX_URL' },
        'TAUTULLI_URL': { type: 'text', default: '' },
        'TAUTULLI_API_KEY': { type: 'password', default: '' },
        'BLOCKING_NOTIFIER_ID': { type: 'number', default: 0 },
        'SCREEN_LIMIT_NOTIFIER_ID': { type: 'number', default: 0 },
        'TRIAL_BLOCK_NOTIFIER_ID': { type: 'number', default: 0 },
        'EFI_ENABLED': { type: 'checkbox', default: false },
        'MERCADOPAGO_ENABLED': { type: 'checkbox', default: false },
        'EFI_CLIENT_ID': { type: 'text', default: '' },
        'EFI_CLIENT_SECRET': { type: 'password', default: '' },
        'EFI_CERTIFICATE': { type: 'text', default: '' },
        'EFI_SANDBOX': { type: 'checkbox', default: false },
        'EFI_PIX_KEY': { type: 'text', default: '' },
        'MERCADOPAGO_ACCESS_TOKEN': { type: 'password', default: '' },
        'RENEWAL_PRICE': { type: 'text', default: '10.00' },
        'PRICE_SCREEN_1': { type: 'price', key: '1' },
        'PRICE_SCREEN_2': { type: 'price', key: '2' },
        'PRICE_SCREEN_3': { type: 'price', key: '3' },
        'PRICE_SCREEN_4': { type: 'price', key: '4' },
        'OVERSEERR_ENABLED': { type: 'checkbox', default: false },
        'OVERSEERR_URL': { type: 'text', default: '' },
        'OVERSEERR_API_KEY': { type: 'password', default: '' },
        'VAPID_ADMIN_EMAIL': { type: 'text', default: '' },
    };

    function loadSettings() {
        return fetchAPI(urls.apiSettings)
            .then(config => {
                for (const [id, field] of Object.entries(fieldMap)) {
                    const el = document.getElementById(id);
                    if (el) {
                        if (field.type === 'price') {
                            el.value = (config.SCREEN_PRICES && config.SCREEN_PRICES[field.key]) || '';
                        } else {
                            const key = field.key || id;
                            if (field.type === 'checkbox') {
                                el.checked = config[key] || field.default;
                            } else {
                                el.value = config[key] !== undefined ? config[key] : field.default;
                            }
                        }
                    }
                }
                if(logLevelSelector) logLevelSelector.value = config.LOG_LEVEL || 'INFO';
            })
            .catch(error => showToast(error.message, 'error'));
    }

    function initializeEventListeners() {
        
        const handleTabClick = (navElement, contentContainer, contentSelector) => {
            navElement.addEventListener('click', (e) => {
                if (e.target.tagName === 'BUTTON' && e.target.dataset.tab) {
                    const tabId = e.target.dataset.tab;
                    navElement.querySelectorAll('.tab-button').forEach(btn => btn.classList.remove('active'));
                    e.target.classList.add('active');
                    contentContainer.querySelectorAll(contentSelector).forEach(content => content.classList.remove('active'));
                    document.getElementById(`tab-${tabId}`).classList.add('active');
                    if (tabId === 'logs') {
                        startLogUpdates();
                    } else {
                        stopLogUpdates();
                    }
                } else if (e.target.tagName === 'BUTTON' && e.target.dataset.subtab) {
                    const subtabId = e.target.dataset.subtab;
                    navElement.querySelectorAll('.tab-button').forEach(btn => btn.classList.remove('active'));
                    e.target.classList.add('active');
                    contentContainer.querySelectorAll(contentSelector).forEach(content => content.classList.remove('active'));
                    document.getElementById(`subtab-${subtabId}`).classList.add('active');
                }
            });
        };
        
        const mainTabs = document.getElementById('main-tabs');
        const mainTabContent = document.getElementById('main-tab-content');
        if (mainTabs && mainTabContent) {
            handleTabClick(mainTabs, mainTabContent, '.tab-content');
        }

        const paymentTabs = document.getElementById('payment-provider-tabs');
        const paymentTabContent = document.getElementById('payment-provider-tab-content');
        if(paymentTabs && paymentTabContent) {
            handleTabClick(paymentTabs, paymentTabContent, '.sub-tab-content');
        }

        const notificationTabs = document.getElementById('notification-tabs');
        const notificationTabContent = document.getElementById('notification-tab-content');
        if(notificationTabs && notificationTabContent) {
            handleTabClick(notificationTabs, notificationTabContent, '.sub-tab-content');
        }

        if(form) {
            form.addEventListener('submit', async (e) => {
                e.preventDefault();
                if(!saveButton) return;
                
                saveButton.disabled = true;
                saveButton.textContent = i18n.saving;
                
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
                            if (field.type === 'checkbox') {
                                newConfig[key] = el.checked;
                            } else if (field.type === 'number') {
                                newConfig[key] = parseInt(el.value) || 0;
                            } else if (field.type === 'password' && el.value.trim() !== '' && el.value !== '********') {
                                newConfig[key] = el.value;
                            } else if (field.type !== 'password') {
                                newConfig[key] = el.value;
                            }
                        }
                    }
                }
                newConfig.SCREEN_PRICES = screenPrices;
                if(logLevelSelector) newConfig.LOG_LEVEL = logLevelSelector.value;

                if (settingsData.plex_url && settingsData.plex_token) {
                    newConfig.plex_url = settingsData.plex_url;
                    newConfig.plex_token = settingsData.plex_token;
                }
                
                try {
                    const result = await fetchAPI(urls.apiSettings, 'POST', newConfig);
                    showToast(result.message, result.success ? 'success' : 'error');
                    if (result.success) loadSettings();
                } catch (error) {
                    showToast(error.message || i18n.unknownError, 'error')
                } finally {
                    saveButton.disabled = false;
                    saveButton.textContent = i18n.saveChanges;
                }
            });
        }
        
        async function testConnection(button, endpoint, payloadBuilder) {
            if(!button) return;
            button.disabled = true;
            button.textContent = i18n.testing;
            try {
                const result = await fetchAPI(endpoint, 'POST', payloadBuilder());
                showToast(result.message, result.success ? 'success' : 'error');
            } catch(error) {
                showToast(error.message || i18n.unknownError, 'error');
            } finally {
                button.disabled = false;
                button.textContent = i18n.testConnection;
            }
        }
        
        if (testTautulliButton) {
            testTautulliButton.addEventListener('click', () => testConnection(testTautulliButton, urls.testTautulli, () => ({ 
                url: document.getElementById('TAUTULLI_URL').value, 
                api_key: document.getElementById('TAUTULLI_API_KEY').value 
            })));
        }
        if (testOverseerrButton) {
            testOverseerrButton.addEventListener('click', () => testConnection(testOverseerrButton, urls.testOverseerr, () => ({ 
                url: document.getElementById('OVERSEERR_URL').value, 
                api_key: document.getElementById('OVERSEERR_API_KEY').value 
            })));
        }

        const plexAuthButton = document.getElementById('reauth-plex-button');
        if (plexAuthButton) {
            plexAuthButton.onclick = loginWithPlex;
        }

        document.querySelectorAll('.btn-auto-config').forEach(button => {
            button.addEventListener('click', () => autoConfigureNotifier(button));
        });

        document.getElementById('languageSelector')?.addEventListener('change', (e) => {
            window.location.href = `${urls.setLanguage}${e.target.value}`;
        });
        
        const helpModal = document.getElementById('placeholderHelpModal');
        document.querySelectorAll('.show-help-button').forEach(button => {
            button.addEventListener('click', () => helpModal?.classList.remove('hidden'));
        });
        document.getElementById('closeHelpModal')?.addEventListener('click', () => helpModal?.classList.add('hidden'));
        
        document.getElementById('toggle-logs')?.addEventListener('click', () => {
            if (logIntervalId) stopLogUpdates(); else startLogUpdates();
        });

        document.getElementById('clear-logs')?.addEventListener('click', async () => {
            const toggleButton = document.getElementById('toggle-logs');
            if (toggleButton) toggleButton.disabled = true;
            try {
                const result = await fetchAPI(urls.clearLogs, 'POST');
                if(result.success) document.getElementById('log-display').textContent = '';
                showToast(result.message, result.success ? 'success' : 'error');
            } catch (error) {
                showToast(`${i18n.errorGeneric}: ${error.message}`, 'error');
            } finally {
                if (toggleButton) toggleButton.disabled = false;
            }
        });
    }
    
    loadSettings().then(() => {
        initializeEventListeners();
    });
});

