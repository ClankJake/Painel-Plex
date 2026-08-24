// app/static/js/payment_public.js
import { fetchAPI, showToast, createModal, setButtonLoading, restoreButton } from './utils.js';

document.addEventListener('DOMContentLoaded', async () => {
    // --- ELEMENTOS E DADOS GLOBAIS ---
    const loadingIndicator = document.getElementById('loadingIndicator');
    const container = document.getElementById('paymentContainer');
    const errorContainer = document.getElementById('errorContainer');
    const errorMessage = document.getElementById('errorMessage');
    const paymentSection = document.getElementById('payment-section');
    const pixDisplay = document.getElementById('pix-display');
    const successDisplay = document.getElementById('success-display');
    const userInfoHeader = document.getElementById('user-info-header');
    const scriptTag = document.getElementById('payment-public-script');

    const urls = {};
    const i18n = {};
    if (scriptTag) {
        for (const key in scriptTag.dataset) {
            if (key.startsWith('i18n')) {
                const i18nKey = key.charAt(4).toLowerCase() + key.slice(5);
                i18n[i18nKey] = scriptTag.dataset[key];
            } else if (key.startsWith('urls')) { 
                const subKey = key.substring(4);
                const urlKey = subKey.charAt(0).toLowerCase() + subKey.slice(1).replace(/-(\w)/g, (match, letter) => letter.toUpperCase());
                urls[urlKey] = scriptTag.dataset[key];
            } else {
                const urlKey = key.replace(/-(\w)/g, (match, letter) => letter.toUpperCase());
                urls[urlKey] = scriptTag.dataset[key];
            }
        }
    }

    let pollingIntervalId = null;
    const token = urls.token;
    const baseUsername = urls.username; // Username vindo do template HTML
    let validatedCouponCode = null;
    let originalPrice = 0;
    let screens = 0;
    
    // Variáveis para Auth Plex
    let authWindow = null;
    let pinCheckIntervalAuth = null;

    // --- FUNÇÕES DE SEGURANÇA E UTILIDADE ---
    const sanitizeHTML = (str) => {
        if (str == null) return '';
        const temp = document.createElement('div');
        temp.textContent = str;
        return temp.innerHTML;
    };

    // --- LÓGICA DE PAGAMENTO ---
    function renderPaymentInfo(prices, providers) {
        if (!paymentSection) return;

        const anyProviderEnabled = providers && Object.values(providers).some(enabled => enabled);
        if (!anyProviderEnabled || !prices || Object.keys(prices).length === 0) {
            paymentSection.innerHTML = `
                <div class="bg-red-50 dark:bg-red-900/20 p-4 rounded-xl text-center border border-red-100 dark:border-red-800/50">
                    <p class="text-red-600 dark:text-red-400 font-medium">${i18n.noProvider}</p>
                </div>`;
            return;
        }

        // Seleciona o primeiro plano por defeito
        screens = Object.keys(prices)[0];
        const price = parseFloat(prices[screens]);
        originalPrice = price;

        const isReactivation = container.dataset.isReactivation === 'true';
        let optionsHtml = '<div class="space-y-3">';

        Object.keys(prices).sort((a,b) => parseInt(a) - parseInt(b)).forEach((scr, index) => {
            const pVal = parseFloat(prices[scr]);
            const priceStr = pVal.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
            let planText = scr === "0" ? "Plano Padrão" : `${scr} ${parseInt(scr) > 1 ? i18n.screenPlural : i18n.screenSingular}`;
            
            // Design Premium com pseudo-class has-[:checked]
            optionsHtml += `
                <label class="flex items-center justify-between p-4 rounded-2xl border-2 border-gray-100 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/30 cursor-pointer transition-all duration-200 hover:border-yellow-300 dark:hover:border-yellow-600 has-[:checked]:border-yellow-500 has-[:checked]:bg-yellow-50/50 dark:has-[:checked]:bg-yellow-900/20 has-[:checked]:shadow-md group">
                    <div class="flex items-center gap-3">
                        <div class="relative flex items-center justify-center">
                            <input type="radio" name="payment-plan" value="${scr}" data-price="${pVal}" class="peer appearance-none w-5 h-5 border-2 border-gray-300 dark:border-gray-500 rounded-full checked:border-yellow-500 transition-colors cursor-pointer" ${index === 0 ? 'checked' : ''}>
                            <div class="absolute w-2.5 h-2.5 bg-yellow-500 rounded-full scale-0 peer-checked:scale-100 transition-transform duration-200"></div>
                        </div>
                        <span class="font-bold text-gray-800 dark:text-gray-200 group-hover:text-gray-900 dark:group-hover:text-white transition-colors">${planText}</span>
                    </div>
                    <div class="text-right">
                        <span class="font-black text-lg text-gray-900 dark:text-white">${priceStr}</span>
                        ${index === 0 ? `<span class="block text-[10px] uppercase font-bold text-gray-400 dark:text-gray-500 tracking-wider">${i18n.currentPlan}</span>` : ''}
                    </div>
                </label>
            `;
        });
        optionsHtml += '</div>';

        const priceText = price.toFixed(2).replace('.', ',');
        const buttonText = isReactivation
            ? (i18n.reactivateForPrice || 'Reativar por R$ {price}').replace('{price}', priceText)
            : i18n.generatePixForPrice.replace('{price}', priceText);

        paymentSection.innerHTML = `
            ${optionsHtml}
            
            <!-- Secção de Cupão Premium -->
            <div class="mt-6 pt-6 border-t border-gray-100 dark:border-gray-700/50">
                <label for="couponCodeInput" class="block text-sm font-bold text-gray-700 dark:text-gray-300 mb-2 uppercase tracking-wider">Código Promocional</label>
                <div class="flex gap-2">
                    <div class="relative flex-grow">
                        <div class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                            <svg class="h-5 w-5 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A1.994 1.994 0 013 12V7a4 4 0 014-4z"></path></svg>
                        </div>
                        <input type="text" id="couponCodeInput" class="w-full py-3 pl-10 pr-3 text-sm font-mono uppercase tracking-wider rounded-xl border border-gray-300 bg-white text-gray-900 focus:ring-2 focus:ring-yellow-500 focus:border-yellow-500 dark:bg-gray-800 dark:border-gray-600 dark:text-white shadow-sm transition-shadow" placeholder="INSERIR CUPÃO">
                    </div>
                    <button id="applyCouponBtn" class="btn bg-gray-800 hover:bg-gray-700 dark:bg-gray-700 dark:hover:bg-gray-600 text-white px-6 font-bold shadow-md rounded-xl transition-colors">Aplicar</button>
                </div>
                <div id="coupon-status" class="text-xs font-medium mt-2 min-h-[20px] transition-all"></div>
            </div>

            <!-- Botão de Ação Principal -->
            <button id="initiatePixButton" class="w-full mt-6 btn bg-green-600 hover:bg-green-500 text-white text-lg py-3.5 rounded-xl shadow-xl shadow-green-500/30 font-bold transition-all transform hover:-translate-y-0.5 disabled:opacity-50 disabled:transform-none disabled:shadow-none flex justify-center items-center gap-2 group">
                <svg class="w-5 h-5 transition-transform group-hover:scale-110" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v1m6 11h2m-6 0h-2v4m0-11v3m0 0h.01M12 12h4.01M16 20h4M4 12h4m12 0h.01M5 8h2a1 1 0 001-1V5a1 1 0 00-1-1H5a1 1 0 00-1 1v2a1 1 0 001 1zm14 0h2a1 1 0 001-1V5a1 1 0 00-1-1h-2a1 1 0 00-1 1v2a1 1 0 001 1zM5 20h2a1 1 0 001-1v-2a1 1 0 00-1-1H5a1 1 0 00-1 1v2a1 1 0 001 1z"></path></svg>
                <span id="btn-text-content">${buttonText}</span>
            </button>
        `;

        bindPaymentEvents(providers);
    }

    const bindPaymentEvents = (providers) => {
        const pixBtn = document.getElementById('initiatePixButton');
        const btnTextContent = document.getElementById('btn-text-content');
        const applyCouponBtn = document.getElementById('applyCouponBtn');
        const couponInput = document.getElementById('couponCodeInput');
        const statusDiv = document.getElementById('coupon-status');
        const isReactivation = container.dataset.isReactivation === 'true';

        // Mudança de plano selecionado
        document.querySelectorAll('input[name="payment-plan"]').forEach(radio => {
            radio.addEventListener('change', () => {
                screens = radio.value;
                originalPrice = parseFloat(radio.dataset.price);
                
                const priceText = originalPrice.toFixed(2).replace('.', ',');
                btnTextContent.textContent = isReactivation 
                    ? (i18n.reactivateForPrice || 'Reativar por R$ {price}').replace('{price}', priceText)
                    : i18n.generatePixForPrice.replace('{price}', priceText);
                
                pixBtn.disabled = false;
                applyCouponBtn.disabled = false;
                
                // Reset Cupão ao mudar de plano
                statusDiv.innerHTML = '';
                couponInput.value = '';
                validatedCouponCode = null;
                
                // Restaura a cor verde original do botão
                pixBtn.className = "w-full mt-6 btn bg-green-600 hover:bg-green-500 text-white text-lg py-3.5 rounded-xl shadow-xl shadow-green-500/30 font-bold transition-all transform hover:-translate-y-0.5 disabled:opacity-50 disabled:transform-none disabled:shadow-none flex justify-center items-center gap-2 group";
            });
        });

        // Validar Cupão
        applyCouponBtn?.addEventListener('click', async () => {
            const code = sanitizeHTML(couponInput.value.trim().toUpperCase());
            const selectedPlan = document.querySelector('input[name="payment-plan"]:checked');
            if (!code || !selectedPlan) return;

            try {
                applyCouponBtn.disabled = true;
                applyCouponBtn.innerHTML = `<svg class="animate-spin h-5 w-5 text-white" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" fill="none"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>`;
                
                const result = await fetchAPI(urls.validateCouponUrl, 'POST', { code, screens: selectedPlan.value, username: baseUsername });
                
                if (result.success) {
                    statusDiv.innerHTML = `<span class="text-green-600 dark:text-green-400 flex items-center gap-1 font-bold"><svg class="w-4 h-4" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd"></path></svg> ${result.message}</span>`;
                    validatedCouponCode = code;
                    
                    // Atualiza o preço na label do rádio ativo visualmente
                    const activeRadioLabel = selectedPlan.closest('label');
                    const priceSpan = activeRadioLabel.querySelector('span.font-black');
                    priceSpan.innerHTML = `<s class="text-gray-400 dark:text-gray-500 text-sm font-medium mr-1">${result.original_price.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })}</s> ${result.discounted_price.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })}`;

                    const newPriceText = result.discounted_price.toFixed(2).replace('.', ',');

                    if (result.discounted_price <= 0) {
                        btnTextContent.textContent = i18n.activateFreeSubscription;
                        // Estilo "Mágico" para o botão 100% grátis
                        pixBtn.className = "w-full mt-6 btn bg-gradient-to-r from-yellow-500 to-yellow-600 hover:from-yellow-400 hover:to-yellow-500 text-white text-lg py-3.5 rounded-xl shadow-xl shadow-yellow-500/40 font-bold transition-all transform hover:-translate-y-0.5 disabled:opacity-50 disabled:transform-none disabled:shadow-none flex justify-center items-center gap-2 group";
                    } else {
                        btnTextContent.textContent = isReactivation
                            ? (i18n.reactivateForPrice || 'Reativar por R$ {price}').replace('{price}', newPriceText)
                            : i18n.generatePixForPrice.replace('{price}', newPriceText);
                    }
                } else {
                    throw new Error(result.message);
                }
            } catch (error) {
                statusDiv.innerHTML = `<span class="text-red-500 flex items-center gap-1 font-semibold"><svg class="w-4 h-4" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clip-rule="evenodd"></path></svg> ${error.message}</span>`;
                validatedCouponCode = null;
                
                // Reverte preço no cartão
                const activeRadioLabel = selectedPlan.closest('label');
                const priceSpan = activeRadioLabel.querySelector('span.font-black');
                priceSpan.textContent = originalPrice.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });

                const originalPriceText = originalPrice.toFixed(2).replace('.', ',');
                btnTextContent.textContent = isReactivation
                    ? (i18n.reactivateForPrice || 'Reativar por R$ {price}').replace('{price}', originalPriceText)
                    : i18n.generatePixForPrice.replace('{price}', originalPriceText);
                    
                // Reverte classe do botão
                pixBtn.className = "w-full mt-6 btn bg-green-600 hover:bg-green-500 text-white text-lg py-3.5 rounded-xl shadow-xl shadow-green-500/30 font-bold transition-all transform hover:-translate-y-0.5 disabled:opacity-50 disabled:transform-none disabled:shadow-none flex justify-center items-center gap-2 group";
            } finally {
                applyCouponBtn.disabled = false;
                applyCouponBtn.textContent = 'Aplicar';
            }
        });

        // Iniciar Pagamento
        pixBtn?.addEventListener('click', () => {
            const payload = {
                token: token,
                username: baseUsername,
                screens: screens,
                coupon_code: validatedCouponCode
            };
            initiatePixPayment(payload, providers);
        });
    };

    async function initiatePixPayment(payload, providers) {
        const activeProviders = Object.keys(providers).filter(p => providers[p]).map(p => p.toUpperCase());
        const btnTextContent = document.getElementById('btn-text-content').textContent;
        const isFree = btnTextContent === i18n.activateFreeSubscription;

        if (isFree) {
            await generatePix(payload); // Ignora provedores se for gratuito
            return;
        }

        if (activeProviders.length === 1) {
            payload.provider = activeProviders[0];
            await generatePix(payload);
        } else if (activeProviders.length > 1) {
            showProviderChoiceModal(payload, activeProviders);
        } else {
             showToast(i18n.noProvider, "error");
        }
    }

    function showProviderChoiceModal(payload, providers) {
        let buttonsHtml = '';
        
        // Estilização Premium para os botões do Modal de Provedores
        if (providers.includes('EFI')) {
            buttonsHtml += `<button data-provider="EFI" class="btn bg-green-600 hover:bg-green-500 text-white w-full py-3 rounded-xl shadow-md transition-colors font-bold">${i18n.payWithEfi}</button>`;
        }
        if (providers.includes('MERCADOPAGO')) {
            buttonsHtml += `<button data-provider="MERCADOPAGO" class="btn bg-blue-600 hover:bg-blue-500 text-white w-full py-3 rounded-xl shadow-md transition-colors font-bold mt-3">${i18n.payWithMp}</button>`;
        }
        // 🐛 A lista de provedores é normalizada com .toUpperCase() acima, por isso a
        // comparação tem de ser em MAIÚSCULAS. Com 'Gates2b' a condição nunca era
        // verdadeira e o botão simplesmente não aparecia quando havia mais de um
        // gateway ativo.
        if (providers.includes('GATES2B')) {
            buttonsHtml += `<button data-provider="GATES2B" class="btn bg-purple-600 hover:bg-purple-500 text-white w-full py-3 rounded-xl shadow-md transition-colors font-bold mt-3">${i18n.payWithGates2b || 'Pagar com Gates2b'}</button>`;
        }

        const body = `<div class="space-y-3 mt-2">${buttonsHtml}</div>`;
        const modal = createModal('providerChoiceModal', i18n.chooseProvider, body, `<button id="cancel-provider" class="btn bg-gray-200 text-gray-800 hover:bg-gray-300 dark:bg-gray-700 dark:text-white dark:hover:bg-gray-600 w-full py-3 rounded-xl transition-colors font-bold">${i18n.cancel}</button>`);

        if(modal){
            modal.querySelectorAll('button[data-provider]').forEach(button => {
                button.onclick = async () => {
                    modal.classList.add('hidden');
                    payload.provider = button.dataset.provider;
                    await generatePix(payload);
                };
            });
            modal.querySelector('#cancel-provider').onclick = () => modal.classList.add('hidden');
        }
    }

    async function generatePix(payload) {
        const btn = document.getElementById('initiatePixButton');
        const iconSvg = btn.querySelector('svg');
        const textSpan = document.getElementById('btn-text-content');
        
        const originalText = textSpan.textContent;
        const originalIcon = iconSvg ? iconSvg.outerHTML : '';
        
        btn.disabled = true;
        textSpan.textContent = i18n.wait;
        if(iconSvg) {
             iconSvg.outerHTML = `<svg class="animate-spin h-5 w-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>`;
        }
        
        let result = null;

        try {
            result = await fetchAPI(urls.createChargeUrl, 'POST', payload);

            if (result && result.success && result.free_renewal) {
                const isReactivation = container.dataset.isReactivation === 'true';
                showSuccessState(isReactivation, result.user_status, result.invite_link);
                return;
            }

            if(result && result.success) {
                paymentSection.style.display = 'none';
                pixDisplay.style.display = 'block';
                document.getElementById('pix-qr-code').src = result.qr_code_image;
                document.getElementById('pix-copy-paste').value = result.pix_copy_paste;
                startPaymentStatusPolling(result.payment_id || result.txid);
            } else {
                showToast(result.message, 'error');
            }
        } catch (error) {
            showToast(error.message, 'error');
        } finally {
            if (!(result && result.success && (result.free_renewal || result.qr_code_image))) {
                btn.disabled = false;
                textSpan.textContent = originalText;
                if(btn.querySelector('svg')) {
                    btn.querySelector('svg').outerHTML = originalIcon;
                }
            }
        }
    }

    // --- LÓGICA DE SUCESSO E REATIVAÇÃO ---
    function showSuccessState(isReactivation, userStatus, inviteLink) {
        if (paymentSection) paymentSection.style.display = 'none';
        if (pixDisplay) pixDisplay.style.display = 'none';
        if (userInfoHeader) userInfoHeader.style.display = 'none';
        
        const successTitle = document.getElementById('success-title');
        const successMessage = document.getElementById('success-message');
        const loginButton = document.getElementById('login-button');
        const reactivationArea = document.getElementById('reactivation-action-area');
        const defaultBtnContainer = document.getElementById('default-login-btn-container');
        const inviteLinkArea = document.getElementById('reactivation-invite-link-area');
        const inviteLinkBtn = document.getElementById('reactivation-invite-link-btn');

        if (successTitle && successMessage && loginButton && successDisplay) {
            if (isReactivation) {
                // Se o status ainda for 'inactive', convite está pendente (ativação automática não confirmou).
                if (userStatus === 'inactive') {
                    successTitle.textContent = i18n.reactivationCheckEmailTitle;
                    successMessage.textContent = i18n.reactivationCheckEmailMessage;
                    
                    if (reactivationArea) {
                        reactivationArea.classList.remove('hidden');
                        if(defaultBtnContainer) defaultBtnContainer.classList.add('hidden');
                        
                        const btn = document.getElementById('reactivate-plex-login-btn');
                        if(btn) btn.onclick = startPlexAuthFlow;

                        // Se temos o link direto do convite, mostra o botão alternativo
                        const isRealInviteLink = inviteLink && !inviteLink.includes('app.plex.tv/desktop');
                        if (inviteLinkArea && inviteLinkBtn) {
                            if (isRealInviteLink) {
                                inviteLinkBtn.href = inviteLink;
                                inviteLinkArea.classList.remove('hidden');
                            } else {
                                inviteLinkArea.classList.add('hidden');
                            }
                        }
                    }
                } else {
                    // Reativação já efetivada pelo backend
                    successTitle.textContent = i18n.reactivationSuccessTitle;
                    successMessage.textContent = i18n.reactivationSuccessMessage;
                    if (reactivationArea) reactivationArea.classList.add('hidden');
                    if(defaultBtnContainer) defaultBtnContainer.classList.remove('hidden');
                }
            } else {
                // Renovação normal
                successTitle.textContent = i18n.renewalSuccessTitle;
                successMessage.textContent = i18n.renewalSuccessMessage;
                if (reactivationArea) reactivationArea.classList.add('hidden');
                if(defaultBtnContainer) defaultBtnContainer.classList.remove('hidden');
            }
            
            loginButton.textContent = i18n.loginButtonText;
            loginButton.href = urls.loginPageUrl;
            
            successDisplay.classList.remove('hidden');
        } else {
            // Fallback seguro
            window.location.href = urls.loginPageUrl || '/';
        }
    }

    // --- NOVA LÓGICA DE AUTH PLEX (Para Reativação) ---

    function startPlexAuthFlow() {
        const btn = document.getElementById('reactivate-plex-login-btn');
        setButtonLoading(btn, i18n.wait);

        window.addEventListener('message', handleAuthMessage, false);
        
        // Centra a janela de popup
        const width = 800;
        const height = 700;
        const left = (window.innerWidth / 2) - (width / 2);
        const top = (window.innerHeight / 2) - (height / 2);
        authWindow = window.open(urls.redirectToAuth, 'plexAuth', `width=${width},height=${height},top=${top},left=${left},status=no,scrollbars=yes,resizable=yes`);
    }

    function handleAuthMessage(event) {
        if (event.origin !== window.location.origin) return;
        const { type, pin_id, client_id } = event.data;

        if (type === 'plexAuthPin' && pin_id && client_id) {
            pollPlexPin(pin_id, client_id);
            window.removeEventListener('message', handleAuthMessage);
        }
    }

    function pollPlexPin(pin_id, client_id) {
        if (pinCheckIntervalAuth) clearInterval(pinCheckIntervalAuth);
        const btn = document.getElementById('reactivate-plex-login-btn');
        let attempts = 0;

        pinCheckIntervalAuth = setInterval(async () => {
            attempts++;
            if (!authWindow || authWindow.closed || attempts > 100) {
                clearInterval(pinCheckIntervalAuth);
                btn.disabled = false;
                btn.innerHTML = `<svg class="w-5 h-5 mr-2" viewBox="0 0 24 24" fill="currentColor"><path d="M12.0001 1.5C11.3001 1.5 10.7301 2.01 10.6501 2.71L9.50006 12.35L4.08006 15.2C3.36006 15.65 3.11006 16.59 3.56006 17.31C3.88006 17.84 4.48006 18.15 5.12006 18.15H6.28006L8.47006 22.29C8.91006 23.12 9.87006 23.57 10.7601 23.36C11.6501 23.15 12.3001 22.35 12.3001 21.42V14.88L17.5301 17.9C18.1501 18.25 18.8901 18.06 19.3501 17.48L21.8201 13.94C22.2801 13.36 22.1801 12.55 21.6501 12.01L15.2701 5.68C14.7301 5.14 13.8801 5.21 13.4301 5.76L12.3001 7.15V2.85C12.3001 2.1 11.7001 1.5 12.0001 1.5Z"/></svg> Tentar Novamente`; 
                return;
            }
            
            try {
                const checkUrl = urls.checkPlexPin.replace('__CLIENT_ID__', client_id).replace('999999', pin_id);
                const checkResponse = await fetch(checkUrl);
                const checkData = await checkResponse.json();

                if (checkData.success && checkData.token) {
                    clearInterval(pinCheckIntervalAuth);
                    authWindow.close();
                    btn.innerHTML = `<svg class="animate-spin h-5 w-5 mr-2" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg> ${i18n.activating || 'Ativando...'}`;
                    finalizeReactivation(checkData.token);
                } else if (checkData.message === 'auth_denied') {
                    clearInterval(pinCheckIntervalAuth);
                    authWindow.close();
                    showToast(i18n.authDenied, 'error');
                    btn.disabled = false;
                    btn.innerHTML = `<svg class="w-5 h-5 mr-2" viewBox="0 0 24 24" fill="currentColor"><path d="M12.0001 1.5C11.3001 1.5 10.7301 2.01 10.6501 2.71L9.50006 12.35L4.08006 15.2C3.36006 15.65 3.11006 16.59 3.56006 17.31C3.88006 17.84 4.48006 18.15 5.12006 18.15H6.28006L8.47006 22.29C8.91006 23.12 9.87006 23.57 10.7601 23.36C11.6501 23.15 12.3001 22.35 12.3001 21.42V14.88L17.5301 17.9C18.1501 18.25 18.8901 18.06 19.3501 17.48L21.8201 13.94C22.2801 13.36 22.1801 12.55 21.6501 12.01L15.2701 5.68C14.7301 5.14 13.8801 5.21 13.4301 5.76L12.3001 7.15V2.85C12.3001 2.1 11.7001 1.5 12.0001 1.5Z"/></svg> Tentar Novamente`;
                }
            } catch (e) {
                console.warn("Polling Auth:", e);
            }
        }, 2000);
    }

    async function finalizeReactivation(plexToken) {
        try {
            const response = await fetchAPI(urls.finalizeReactivation, 'POST', {
                plex_token: plexToken,
                payment_token: token
            });

            if (response.success) {
                showToast(response.message, 'success');
                showSuccessState(true, 'active');
            } else {
                showToast(response.message, 'error');
                const btn = document.getElementById('reactivate-plex-login-btn');
                btn.disabled = false;
                btn.innerHTML = `<svg class="w-5 h-5 mr-2" viewBox="0 0 24 24" fill="currentColor"><path d="M12.0001 1.5C11.3001 1.5 10.7301 2.01 10.6501 2.71L9.50006 12.35L4.08006 15.2C3.36006 15.65 3.11006 16.59 3.56006 17.31C3.88006 17.84 4.48006 18.15 5.12006 18.15H6.28006L8.47006 22.29C8.91006 23.12 9.87006 23.57 10.7601 23.36C11.6501 23.15 12.3001 22.35 12.3001 21.42V14.88L17.5301 17.9C18.1501 18.25 18.8901 18.06 19.3501 17.48L21.8201 13.94C22.2801 13.36 22.1801 12.55 21.6501 12.01L15.2701 5.68C14.7301 5.14 13.8801 5.21 13.4301 5.76L12.3001 7.15V2.85C12.3001 2.1 11.7001 1.5 12.0001 1.5Z"/></svg> Tentar Novamente`;
            }
        } catch (error) {
            showToast(error.message, 'error');
            const btn = document.getElementById('reactivate-plex-login-btn');
            btn.disabled = false;
            btn.innerHTML = `<svg class="w-5 h-5 mr-2" viewBox="0 0 24 24" fill="currentColor"><path d="M12.0001 1.5C11.3001 1.5 10.7301 2.01 10.6501 2.71L9.50006 12.35L4.08006 15.2C3.36006 15.65 3.11006 16.59 3.56006 17.31C3.88006 17.84 4.48006 18.15 5.12006 18.15H6.28006L8.47006 22.29C8.91006 23.12 9.87006 23.57 10.7601 23.36C11.6501 23.15 12.3001 22.35 12.3001 21.42V14.88L17.5301 17.9C18.1501 18.25 18.8901 18.06 19.3501 17.48L21.8201 13.94C22.2801 13.36 22.1801 12.55 21.6501 12.01L15.2701 5.68C14.7301 5.14 13.8801 5.21 13.4301 5.76L12.3001 7.15V2.85C12.3001 2.1 11.7001 1.5 12.0001 1.5Z"/></svg> Tentar Novamente`;
        }
    }

    function startPaymentStatusPolling(txid) {
        if (pollingIntervalId) clearInterval(pollingIntervalId);
        const pollingStatus = document.getElementById('polling-status');

        pollingIntervalId = setInterval(async () => {
            try {
                const statusResult = await fetchAPI(urls.getPaymentStatusBaseUrl.replace('__TXID__', txid));
                if (statusResult.success && statusResult.status === 'CONCLUIDA') {
                    clearInterval(pollingIntervalId);
                    const isReactivation = container.dataset.isReactivation === 'true';
                    
                    showToast(i18n.paymentConfirmed || 'Pagamento confirmado!', "success");
                    
                    if(pollingStatus) {
                        pollingStatus.innerHTML = `
                            <div class="flex items-center justify-center gap-2 text-sm font-bold text-green-600 dark:text-green-500 bg-green-50 dark:bg-green-900/20 px-4 py-2 rounded-full border border-green-200 dark:border-green-800">
                                <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg>
                                ${i18n.pollingConfirmed || 'Pagamento confirmado! A atualizar...'}
                            </div>`;
                    }
                    
                    setTimeout(() => showSuccessState(isReactivation, statusResult.user_status, statusResult.invite_link), 2000);
                }
            } catch (error) {
                console.warn(`${i18n.pollingError}:`, error.message);
            }
        }, 5000);
    }

    const copyButton = document.getElementById('copy-pix-code');
    if (copyButton) {
        copyButton.onclick = () => {
            const input = document.getElementById('pix-copy-paste');
            input.select();
            document.execCommand('copy');
            showToast(i18n.codeCopied, 'success');
        };
    }

    async function main() {
        try {
            const profileData = await fetchAPI(urls.getPublicProfileUrl);
            if (!profileData.success) throw new Error(profileData.message);

            const paymentOptions = await fetchAPI(`${urls.getPaymentOptionsUrl}?token=${token}`);
            if(paymentOptions.success) {
                renderPaymentInfo(paymentOptions.prices, paymentOptions.providers);
            } else {
                paymentSection.innerHTML = `<div class="bg-yellow-50 dark:bg-yellow-900/20 p-4 rounded-xl text-center border border-yellow-200 dark:border-yellow-700/50"><p class="text-yellow-700 dark:text-yellow-500 font-medium">${paymentOptions.message || i18n.noPlanPrice}</p></div>`;
            }

            const thumbUrl = profileData.profile.thumb;
            const isReactivation = container.dataset.isReactivation === 'true';
            
            const thumbElement = document.getElementById('user-thumb');
            
            // Lógica aperfeiçoada para encontrar a inicial
            const displayUsername = profileData.profile.username || baseUsername || '?';
            let initial = '?';
            if (displayUsername && displayUsername !== '?') {
                initial = displayUsername.charAt(0).toUpperCase();
            }

            // Configurar fallback fiável em caso de erro no link da imagem
            const fallbackUrl = `https://placehold.co/128x128/1F2937/E5E7EB?text=${initial}`;
            thumbElement.onerror = () => {
                if (thumbElement.src !== fallbackUrl) {
                     thumbElement.src = fallbackUrl;
                }
            };

            // Aplicação da imagem original ou do fallback imediato
            if (thumbUrl && !thumbUrl.includes("placehold.co")) {
                if (thumbUrl.startsWith('http')) {
                    thumbElement.src = thumbUrl;
                } else if (thumbUrl.startsWith('/')) {
                    thumbElement.src = `${window.location.origin}${thumbUrl}`;
                } else {
                    thumbElement.src = thumbUrl;
                }
            } else {
                thumbElement.src = fallbackUrl;
            }
            
            if (!isReactivation) {
                document.getElementById('user-username').textContent = displayUsername;
            }

            const expirationElem = document.getElementById('user-expiration');
            if (profileData.profile.expiration_date_iso && expirationElem) {
                const expDate = new Date(profileData.profile.expiration_date_iso);
                const today = new Date();
                today.setHours(0, 0, 0, 0);

                if (expDate < today) {
                    expirationElem.textContent = `${i18n.expiredOn} ${profileData.profile.expiration_date_formatted}`;
                    expirationElem.classList.add('text-red-500', 'dark:text-red-400');
                } else {
                    expirationElem.textContent = `${i18n.expiresOn} ${profileData.profile.expiration_date_formatted}`;
                }
            }

            if (loadingIndicator) loadingIndicator.style.display = 'none';
            if (container) container.classList.remove('hidden');

        } catch (error) {
            if (loadingIndicator) loadingIndicator.style.display = 'none';
            if (errorMessage) errorMessage.textContent = error.message;
            if (errorContainer) errorContainer.classList.remove('hidden');
        }
    }

    main();
});
