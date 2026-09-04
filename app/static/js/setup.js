import { showToast, buildPinCheckUrl } from './utils.js';

document.addEventListener('DOMContentLoaded', () => {
    // --- ESTADO E DADOS GLOBAIS ---
    const setupData = { plex_url: null, plex_token: null, admin_user: null };
    let pinCheckInterval = null;
    let currentStep = 0;
    
    // --- ELEMENTOS DO DOM ---
    const progressBar = document.getElementById('progressBar');
    const stepIndicator = document.getElementById('step-indicator');
    const wizardSteps = document.querySelectorAll('.wizard-step');
    const wizardContainer = document.getElementById('wizardContainer');
    
    // --- DADOS DO BACKEND (URLs e Traduções) ---
    const scriptTag = document.getElementById('setup-script');
    const urls = {};
    const i18n = {};
    for (const key in scriptTag.dataset) {
        if (key.startsWith('urls')) {
            const urlKey = key.charAt(4).toLowerCase() + key.slice(5);
            urls[urlKey] = scriptTag.dataset[key];
        } else if (key.startsWith('i18n')) {
            const i18nKey = key.charAt(4).toLowerCase() + key.slice(5);
            i18n[i18nKey] = scriptTag.dataset[key];
        }
    }
    const stepTitles = [i18n.step0Title, i18n.step1Title, i18n.step2Title, i18n.step3Title];
    // Passo especial, acessível a partir do ecrã de boas-vindas.
    const RESTORE_STEP = 4;
    let stepBeforeRestore = 0;
    let selectedRestoreFile = null;

    // --- LÓGICA DO WIZARD ---

    function navigateToStep(targetStep) {
        const currentStepEl = document.querySelector(`[data-step="${currentStep}"]`);
        if (currentStepEl) {
            currentStepEl.classList.add('opacity-0');
            setTimeout(() => currentStepEl.classList.add('hidden'), 500);
        }

        const targetStepEl = document.querySelector(`[data-step="${targetStep}"]`);
        if (targetStepEl) {
            setTimeout(() => {
                targetStepEl.classList.remove('hidden');
                void targetStepEl.offsetWidth; 
                targetStepEl.classList.remove('opacity-0');
            }, 500);
        }
        
        const previousStep = currentStep;
        currentStep = targetStep;

        // O passo 4 (restauro de backup) é um desvio fora do fluxo normal do
        // assistente: não entra no cálculo de progresso, senão o wizard nunca
        // chegaria aos 100% ao concluir a configuração normal.
        if (currentStep === RESTORE_STEP) {
            progressBar.style.width = '0%';
            stepIndicator.textContent = i18n.step4Title || '';
            stepBeforeRestore = previousStep;
            return;
        }

        const progress = (currentStep / (stepTitles.length - 1)) * 100;
        progressBar.style.width = `${progress}%`;
        stepIndicator.textContent = stepTitles[currentStep];
    }
    
    async function loginWithPlex() {
        const loginButton = document.getElementById('login-with-plex');
        if (loginButton) {
            loginButton.disabled = true;
            loginButton.innerHTML = `<svg class="animate-spin h-5 w-5 mr-3" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" class="opacity-25"></circle><path d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" fill="currentColor" class="opacity-75"></path></svg> ${i18n.verifying}`;
        }
        
        if (pinCheckInterval) clearInterval(pinCheckInterval);

        try {
            const contextResponse = await fetch(urls.getPlexAuthContext);
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
            const authWindow = window.open(auth_url, 'plexAuth', 'width=800,height=700');

            pinCheckInterval = setInterval(async () => {
                if (!authWindow || authWindow.closed) {
                    clearInterval(pinCheckInterval);
                    if (loginButton) {
                        loginButton.disabled = false;
                        loginButton.textContent = 'Login com Plex';
                    }
                    return;
                }
                
                try {
                    const checkUrl = buildPinCheckUrl(urls.checkPlexPin, client_id, pin_id);
                    const checkResponse = await fetch(checkUrl);
                    const checkData = await checkResponse.json();

                    if (checkData.success) {
                        clearInterval(pinCheckInterval);
                        if (authWindow && !authWindow.closed) {
                            authWindow.close();
                        }
                        showToast(i18n.authenticated, "success");
                        await initializeSetup(checkData);
                    } else if (checkData.message === 'auth_denied') {
                        clearInterval(pinCheckInterval);
                        if (authWindow && !authWindow.closed) {
                            authWindow.close();
                        }
                        showToast(checkData.error, 'error');
                        if (loginButton) {
                            loginButton.disabled = false;
                            loginButton.textContent = 'Login com Plex';
                        }
                    }
                } catch (e) {
                    clearInterval(pinCheckInterval);
                    showToast(`${i18n.verificationError} ${e.message}`, 'error');
                    if (loginButton) {
                        loginButton.disabled = false;
                        loginButton.textContent = 'Login com Plex';
                    }
                }
            }, 3000);

        } catch (error) {
            showToast(error.message, 'error');
            if (loginButton) {
                loginButton.disabled = false;
                loginButton.textContent = 'Login com Plex';
            }
        }
    }
    
    async function initializeSetup(authData) {
        navigateToStep(2);
        const serverListDiv = document.getElementById('server-list');
        serverListDiv.innerHTML = `<p class="text-center p-8 text-gray-600 dark:text-gray-400">${i18n.fetchingServers}</p>`;
        
        try {
            const response = await fetch(urls.getPlexServers);
            const data = await response.json();

            if (response.ok && data.success && data.servers.length > 0) {
                setupData.plex_token = data.token;
                setupData.admin_user = data.username;
                
                let serverHtml = `<p class="mb-4 text-gray-600 dark:text-gray-400">${i18n.selectServer}</p>`;
                
                data.servers.forEach((server, index) => {
                    serverHtml += `
                    <div class="p-4 bg-gray-100 dark:bg-gray-700/50 rounded-lg border-2 border-gray-300 dark:border-gray-600">
                        <h3 class="font-bold text-lg text-gray-900 dark:text-white mb-2">${server.name}</h3>
                        <div class="space-y-2">
                            ${server.connections.map((conn, connIndex) => {
                                const isSsl = conn.uri.startsWith('https://');
                                return `
                                <label for="server-${index}-${connIndex}" class="flex items-center p-2 rounded-md hover:bg-gray-200 dark:hover:bg-gray-600 cursor-pointer transition-colors duration-200">
                                    <input type="radio" id="server-${index}-${connIndex}" name="selected_server_uri" value="${conn.uri}" class="form-radio h-4 w-4 text-yellow-500 bg-gray-200 dark:bg-gray-900 border-gray-400 dark:border-gray-500 focus:ring-yellow-500 focus:ring-offset-gray-800">
                                    <span class="ml-3 text-sm text-gray-700 dark:text-gray-300 break-all">${conn.uri}</span>
                                    <span class="ml-auto flex items-center gap-2 flex-shrink-0">
                                        <span class="text-xs font-medium px-2 py-1 rounded-full ${isSsl ? 'bg-teal-200 text-teal-800 dark:bg-teal-900 dark:text-teal-200' : 'bg-orange-200 text-orange-800 dark:bg-orange-900 dark:text-orange-300'}">
                                            ${isSsl ? 'SSL' : 'HTTP'}
                                        </span>
                                        <span class="text-xs font-medium px-2 py-1 rounded-full ${conn.local ? 'bg-blue-200 text-blue-800 dark:bg-blue-900 dark:text-blue-200' : 'bg-green-200 text-green-800 dark:bg-green-900 dark:text-green-200'}">
                                            ${conn.local ? i18n.local : i18n.remote}
                                        </span>
                                    </span>
                                </label>
                                `
                            }).join('')}
                        </div>
                    </div>`;
                });

                serverListDiv.innerHTML = serverHtml;
                
                document.querySelectorAll('input[name="selected_server_uri"]').forEach(radio => {
                    radio.addEventListener('change', (e) => {
                        if (e.target.checked) {
                            setupData.plex_url = e.target.value;
                            document.getElementById('next-2').disabled = false;
                        }
                    });
                });
            } else {
                serverListDiv.innerHTML = `<p class="text-center p-8 text-yellow-600 dark:text-yellow-400">${data.message || i18n.noServersFound}</p>`;
            }
        } catch (error) {
            navigateToStep(1);
            showToast(`${i18n.errorGeneric} ${error.message}`, 'error');
        }
    }
    
    // Event Listeners
    document.getElementById('start-setup').addEventListener('click', () => navigateToStep(1));
    document.getElementById('login-with-plex').addEventListener('click', loginWithPlex);
    document.querySelectorAll('.btn-prev').forEach(btn => btn.addEventListener('click', () => {
        // A partir do ecrã de restauro, "Anterior" volta ao ponto de onde se veio
        // (o ecrã de boas-vindas), e não para o passo 3 do assistente.
        navigateToStep(currentStep === RESTORE_STEP ? stepBeforeRestore : currentStep - 1);
    }));

    // --- RESTAURO DE BACKUP ---

    document.getElementById('go-to-restore')?.addEventListener('click', () => navigateToStep(RESTORE_STEP));

    const restoreInput = document.getElementById('restore-file-input');
    const restoreLabel = document.getElementById('restore-file-label');
    const restoreSubmit = document.getElementById('restore-submit');
    const restoreDropzone = document.getElementById('restore-dropzone');

    function setRestoreFile(file) {
        if (!file) return;
        if (!file.name.toLowerCase().endsWith('.zip')) {
            showToast(i18n.invalidFileType || 'Selecione um ficheiro .zip válido.', 'error');
            return;
        }
        selectedRestoreFile = file;
        if (restoreLabel) {
            const sizeMb = (file.size / (1024 * 1024)).toFixed(2);
            restoreLabel.textContent = `${file.name} (${sizeMb} MB)`;
        }
        if (restoreSubmit) restoreSubmit.disabled = false;
        restoreDropzone?.classList.add('border-yellow-500');
    }

    restoreInput?.addEventListener('change', (e) => setRestoreFile(e.target.files?.[0]));

    // Suporte a arrastar-e-largar, além do clique.
    if (restoreDropzone) {
        ['dragenter', 'dragover'].forEach(evt =>
            restoreDropzone.addEventListener(evt, (e) => {
                e.preventDefault();
                restoreDropzone.classList.add('border-yellow-500');
            })
        );
        restoreDropzone.addEventListener('dragleave', () => {
            if (!selectedRestoreFile) restoreDropzone.classList.remove('border-yellow-500');
        });
        restoreDropzone.addEventListener('drop', (e) => {
            e.preventDefault();
            setRestoreFile(e.dataTransfer?.files?.[0]);
        });
    }

    restoreSubmit?.addEventListener('click', async () => {
        if (!selectedRestoreFile) return;

        // ⚠️ Ação destrutiva e irreversível: pede confirmação explícita.
        if (!confirm(i18n.confirmRestore || 'Isto vai substituir todos os dados desta instalação. Continuar?')) return;

        const original = restoreSubmit.textContent;
        restoreSubmit.disabled = true;
        restoreSubmit.textContent = i18n.restoring || 'A restaurar...';

        try {
            const formData = new FormData();
            formData.append('file', selectedRestoreFile);

            // Upload multipart: não passa pelo fetchAPI (que envia JSON).
            const response = await fetch(urls.restoreBackup, { method: 'POST', body: formData });
            const data = await response.json();

            if (!response.ok || !data.success) throw new Error(data.message || `HTTP ${response.status}`);

            showToast(data.message || (i18n.restoreSuccess || 'Backup restaurado!'), 'success');
            // O servidor reinicia sozinho; damos tempo e recarregamos.
            setTimeout(() => window.location.reload(), 8000);
        } catch (error) {
            showToast(`${i18n.restoreFailed || 'Falha ao restaurar'}: ${error.message}`, 'error');
            restoreSubmit.disabled = false;
            restoreSubmit.textContent = original || (i18n.restoreNow || 'Restaurar Agora');
        }
    });
    document.getElementById('next-2').addEventListener('click', () => navigateToStep(3));

    document.getElementById('finish-setup').addEventListener('click', async () => {
        const finishButton = document.getElementById('finish-setup');
        finishButton.disabled = true;
        finishButton.textContent = i18n.saving;

        setupData.APP_TITLE = document.getElementById('APP_TITLE').value;
        setupData.TAUTULLI_URL = document.getElementById('tautulli_url').value;
        setupData.TAUTULLI_API_KEY = document.getElementById('tautulli_api_key').value;
        setupData.OVERSEERR_ENABLED = document.getElementById('overseerr_enabled').checked;
        setupData.OVERSEERR_URL = document.getElementById('overseerr_url').value;
        setupData.OVERSEERR_API_KEY = document.getElementById('overseerr_api_key').value;
        
        try {
            const response = await fetch(urls.saveSetup, { 
                method: 'POST', 
                headers: { 'Content-Type': 'application/json' }, 
                body: JSON.stringify(setupData) 
            });
            const result = await response.json();
            if (result.success) {
                window.location.href = result.redirect_url;
            } else { throw new Error(result.message || i18n.unknownError); }
        } catch (error) {
            showToast(`${i18n.errorGeneric} ${error.message}`, 'error');
            finishButton.disabled = false;
            finishButton.textContent = i18n.finishSetup;
        }
    });

    async function handleApiButtonAction(button, endpoint, payloadBuilder, originalTextKey, resultSpanId) {
        const originalText = i18n[originalTextKey];
        const loadingText = button.dataset.loadingText || i18n.testing;
        const resultSpan = document.getElementById(resultSpanId);
        
        if (resultSpan) resultSpan.innerHTML = '';
        button.disabled = true;
        button.textContent = loadingText;

        try {
            const response = await fetch(endpoint, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payloadBuilder())
            });
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({ message: response.statusText }));
                throw new Error(errorData.message || `HTTP error! status: ${response.status}`);
            }
            const result = await response.json();
            showToast(result.message, result.success ? 'success' : 'error');
            
            if (resultSpan) {
                if (result.success) {
                    resultSpan.innerHTML = `<svg class="w-6 h-6 text-green-500" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>`;
                } else {
                    resultSpan.innerHTML = `<svg class="w-6 h-6 text-red-500" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>`;
                }
            }

        } catch (error) {
            showToast(`${i18n.errorGeneric} ${error.message}`, 'error');
            if (resultSpan) {
                 resultSpan.innerHTML = `<svg class="w-6 h-6 text-red-500" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>`;
            }
        } finally {
            button.disabled = false;
            button.textContent = originalText;
        }
    }
    
    document.getElementById('testTautulli').addEventListener('click', (e) => {
        handleApiButtonAction(e.target, urls.testTautulli, () => ({
            url: document.getElementById('tautulli_url').value,
            api_key: document.getElementById('tautulli_api_key').value
        }), 'testConnection', 'tautulli-test-result');
    });

    document.getElementById('testOverseerr').addEventListener('click', (e) => {
        handleApiButtonAction(e.target, urls.testOverseerr, () => ({
            url: document.getElementById('overseerr_url').value,
            api_key: document.getElementById('overseerr_api_key').value
        }), 'testConnection', 'overseerr-test-result');
    });
    
    navigateToStep(0);
});
