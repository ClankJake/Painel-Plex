import { showToast } from './utils.js';

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
        
        currentStep = targetStep;
        
        const progress = (currentStep / (stepTitles.length - 1)) * 100;
        progressBar.style.width = `${progress}%`;
        stepIndicator.textContent = stepTitles[currentStep];
    }
    
    // ### INÍCIO DA CORREÇÃO ###
    // A função loginWithPlex foi reescrita para ser mais robusta e não depender da página de redirecionamento.
    async function loginWithPlex() {
        const loginButton = document.getElementById('login-with-plex');
        if (loginButton) {
            loginButton.disabled = true;
            loginButton.innerHTML = `<svg class="animate-spin h-5 w-5 mr-3" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" class="opacity-25"></circle><path d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" fill="currentColor" class="opacity-75"></path></svg> ${i18n.verifying}`;
        }
        
        if (pinCheckInterval) clearInterval(pinCheckInterval);

        try {
            // 1. Obter o contexto de autenticação do nosso servidor.
            const contextResponse = await fetch(urls.getPlexAuthContext);
            if (!contextResponse.ok) throw new Error(i18n.errorGeneric);
            const contextData = await contextResponse.json();
            if (!contextData.success) throw new Error(contextData.message);
            
            const { product_name, client_id } = contextData;

            // 2. Fazer a requisição para a API do Plex para criar o PIN.
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

            // 3. Construir a URL de autenticação do Plex e abrir o popup.
            const authUrlParams = new URLSearchParams({
                'clientID': client_id,
                'code': pin_code,
                'context[device][product]': product_name,
                'context[device][deviceName]': product_name,
                'context[device][platform]': 'Web',
            });
            const auth_url = `https://app.plex.tv/auth#?${authUrlParams.toString()}`;
            const authWindow = window.open(auth_url, 'plexAuth', 'width=800,height=700');

            // 4. Iniciar a verificação do estado do PIN com o nosso servidor.
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
                    const checkUrl = urls.checkPlexPin.replace('__CLIENT_ID__', client_id).replace('999999', pin_id);
                    const checkResponse = await fetch(checkUrl);
                    const checkData = await checkResponse.json();

                    if (checkData.success) {
                        clearInterval(pinCheckInterval);
                        if (authWindow && !authWindow.closed) {
                            authWindow.close();
                        }
                        showToast(i18n.authenticated, "success");
                        await initializeSetup(checkData); // Passa os dados para o próximo passo
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
    // ### FIM DA CORREÇÃO ###
    
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
                data.servers.forEach(server => {
                    serverHtml += `<div class="p-4 bg-gray-100 dark:bg-gray-700/50 rounded-lg border-2 border-transparent cursor-pointer transition-all duration-200 hover:border-yellow-400 dark:hover:border-yellow-500/50 hover:bg-gray-200 dark:hover:bg-gray-700" data-uri="${server.uri}"><h3 class="font-bold text-lg text-gray-900 dark:text-white">${server.name}</h3><p class="text-sm text-gray-600 dark:text-gray-400">${server.uri}</p></div>`;
                });
                serverListDiv.innerHTML = serverHtml;
                
                document.querySelectorAll('[data-uri]').forEach(card => {
                    card.addEventListener('click', () => {
                        document.querySelectorAll('[data-uri]').forEach(c => c.classList.remove('border-yellow-500', 'ring-2', 'ring-yellow-500', 'bg-yellow-100/50', 'dark:bg-yellow-500/20'));
                        card.classList.add('border-yellow-500', 'ring-2', 'ring-yellow-500', 'bg-yellow-100/50', 'dark:bg-yellow-500/20');
                        setupData.plex_url = card.dataset.uri;
                        document.getElementById('next-2').disabled = false;
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
    document.querySelectorAll('.btn-prev').forEach(btn => btn.addEventListener('click', () => navigateToStep(currentStep - 1)));
    document.getElementById('next-2').addEventListener('click', () => navigateToStep(3));

    document.getElementById('finish-setup').addEventListener('click', async () => {
        const finishButton = document.getElementById('finish-setup');
        finishButton.disabled = true;
        finishButton.textContent = i18n.saving;

        setupData.APP_TITLE = document.getElementById('APP_TITLE').value;
        setupData.TAUTULLI_URL = document.getElementById('tautulli_url').value;
        setupData.TAUTULLI_API_KEY = document.getElementById('tautulli_api_key').value;
        setupData.BLOCKING_NOTIFIER_ID = document.getElementById('blocking_notifier_id').value || 0;
        setupData.SCREEN_LIMIT_NOTIFIER_ID = document.getElementById('screen_limit_notifier_id').value || 0;
        setupData.TRIAL_BLOCK_NOTIFIER_ID = document.getElementById('trial_block_notifier_id').value || 0;
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
    
    const helpModal = document.getElementById('helpModal');
    document.getElementById('showTautulliHelp').addEventListener('click', () => {
        helpModal.classList.remove('hidden');
    });
    helpModal.addEventListener('click', e => {
        if (e.target.id === 'helpModal' || e.target.id === 'closeHelpModal') {
            helpModal.classList.add('hidden');
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

    document.querySelectorAll('.btn-auto-config').forEach(button => {
        button.addEventListener('click', (e) => {
            const target = e.currentTarget;
            const notifierId = document.getElementById(target.dataset.targetId).value;
            const tautulliUrl = document.getElementById('tautulli_url').value;
            const tautulliApiKey = document.getElementById('tautulli_api_key').value;

            if (!notifierId || notifierId === '0') {
                showToast(i18n.provideNotifierId, 'error');
                return;
            }
            if (!tautulliUrl || !tautulliApiKey) {
                showToast(i18n.provideTautulliCredentials, 'error');
                return;
            }
            
            target.dataset.loadingText = i18n.autoConfiguring;
            handleApiButtonAction(target, urls.autoConfigureTautulli, () => ({
                notifier_id: notifierId,
                notifier_type: target.dataset.notifierType,
                url: tautulliUrl,
                api_key: tautulliApiKey
            }), 'autoConfigure', null);
        });
    });
    
    navigateToStep(0);
});

