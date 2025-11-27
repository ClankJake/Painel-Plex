// app/static/js/payment_public.js
import { fetchAPI, showToast, createModal } from './utils.js';

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
            } else if (key.startsWith('urls')) { // ADICIONADO: Suporte para prefixo 'urls'
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
    const username = urls.username;
    let validatedCouponCode = null;
    let originalPrice = 0;
    let screens = 0;
    
    // Variáveis para Auth Plex
    let authWindow = null;
    let pinCheckIntervalAuth = null;


    // --- LÓGICA DE PAGAMENTO ---
    function renderPaymentInfo(prices, providers) {
        if (!paymentSection) return;

        const anyProviderEnabled = providers && Object.values(providers).some(enabled => enabled);
        if (!anyProviderEnabled || !prices || Object.keys(prices).length === 0) {
            paymentSection.innerHTML = `<p class="text-gray-500 dark:text-gray-400">${i18n.noProvider}</p>`;
            return;
        }

        screens = Object.keys(prices)[0];
        const price = parseFloat(prices[screens]);
        originalPrice = price;

        let planText = i18n.currentPlan;
        if (screens !== "0") {
            const screenText = parseInt(screens) > 1 ? i18n.screenPlural : i18n.screenSingular;
            planText = `${i18n.currentPlan}: ${screens} ${screenText}`;
        }

        const priceText = price.toFixed(2).replace('.', ',');
        const isReactivation = container.dataset.isReactivation === 'true';
        const buttonText = isReactivation
            ? (i18n.reactivateForPrice || 'Reativar por R$ {price}').replace('{price}', priceText)
            : i18n.generatePixForPrice.replace('{price}', priceText);

        paymentSection.innerHTML = `
            <div class="text-center p-4 rounded-lg border-2 border-gray-300 dark:border-gray-600">
                <p class="font-semibold text-gray-800 dark:text-gray-200">${planText}</p>
                <div id="price-display" class="font-bold text-2xl text-gray-900 dark:text-white mt-2">${price.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })}</div>
            </div>

            <div class="mt-4">
                <label for="couponCodeInput" class="text-sm font-medium text-gray-500 dark:text-gray-400">Código de Desconto</label>
                <div class="flex gap-2 mt-1">
                    <input type="text" id="couponCodeInput" class="w-full p-2.5 text-sm rounded-lg border bg-gray-50 border-gray-300 text-gray-900 dark:bg-gray-700 dark:border-gray-600 dark:text-white" placeholder="Insira o cupão">
                    <button id="applyCouponBtn" class="btn bg-gray-600 hover:bg-gray-500 text-white px-4">Aplicar</button>
                </div>
                <div id="coupon-status" class="text-xs mt-1 h-4"></div>
            </div>

            <button id="initiatePixButton" class="w-full mt-6 btn bg-green-600 hover:bg-green-500 text-white">${buttonText}</button>
        `;

        document.getElementById('initiatePixButton').addEventListener('click', () => {
            const payload = {
                token: token,
                username: username,
                screens: screens,
                coupon_code: validatedCouponCode
            };
            initiatePixPayment(payload, providers);
        });

        document.getElementById('applyCouponBtn').addEventListener('click', async () => {
            const code = document.getElementById('couponCodeInput').value.trim();
            const statusDiv = document.getElementById('coupon-status');
            const pixButton = document.getElementById('initiatePixButton');
            const isReactivation = container.dataset.isReactivation === 'true';
            if (!code) return;

            try {
                const result = await fetchAPI(urls.validateCouponUrl, 'POST', { code, screens, username: username });
                if (result.success) {
                    statusDiv.innerHTML = `<span class="text-green-500">${result.message}</span>`;
                    document.getElementById('price-display').innerHTML = `<s class="text-gray-400 text-lg">${result.original_price.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })}</s> ${result.discounted_price.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })}`;
                    validatedCouponCode = code;
                    const newPriceText = result.discounted_price.toFixed(2).replace('.', ',');

                    if (result.discounted_price <= 0) {
                        pixButton.textContent = i18n.activateFreeSubscription;
                    } else {
                        pixButton.textContent = isReactivation
                            ? (i18n.reactivateForPrice || 'Reativar por R$ {price}').replace('{price}', newPriceText)
                            : i18n.generatePixForPrice.replace('{price}', newPriceText);
                    }
                } else {
                    statusDiv.innerHTML = `<span class="text-red-500">${result.message}</span>`;
                    document.getElementById('price-display').innerHTML = originalPrice.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
                    validatedCouponCode = null;
                    const originalPriceText = originalPrice.toFixed(2).replace('.', ',');
                    pixButton.textContent = isReactivation
                        ? (i18n.reactivateForPrice || 'Reativar por R$ {price}').replace('{price}', originalPriceText)
                        : i18n.generatePixForPrice.replace('{price}', originalPriceText);
                }
            } catch (error) {
                statusDiv.innerHTML = `<span class="text-red-500">${error.message}</span>`;
                validatedCouponCode = null;
                const originalPriceText = originalPrice.toFixed(2).replace('.', ',');
                pixButton.textContent = isReactivation
                    ? (i18n.reactivateForPrice || 'Reativar por R$ {price}').replace('{price}', originalPriceText)
                    : i18n.generatePixForPrice.replace('{price}', originalPriceText);
            }
        });
    }

    async function initiatePixPayment(payload, providers) {
        const activeProviders = Object.keys(providers).filter(p => providers[p]).map(p => p.toUpperCase());

        const pixButtonText = document.getElementById('initiatePixButton').textContent;
        if (pixButtonText === i18n.activateFreeSubscription) {
            await generatePix(payload);
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
        if (providers.includes('EFI')) {
            buttonsHtml += `<button data-provider="EFI" class="btn bg-green-600 hover:bg-green-500 text-white w-full">${i18n.payWithEfi}</button>`;
        }
        if (providers.includes('MERCADOPAGO')) {
            buttonsHtml += `<button data-provider="MERCADOPAGO" class="btn bg-blue-600 hover:bg-blue-500 text-white w-full mt-2">${i18n.payWithMp}</button>`;
        }
        if (providers.includes('BPIX')) {
            buttonsHtml += `<button data-provider="BPIX" class="btn bg-purple-600 hover:bg-purple-500 text-white w-full mt-2">${i18n.payWithBpix || 'Pagar com BPIX'}</button>`;
        }

        const body = `<div class="space-y-3">${buttonsHtml}</div>`;
        const modal = createModal('providerChoiceModal', i18n.chooseProvider, body, `<button id="cancel-provider" class="btn bg-gray-200 dark:bg-gray-600">${i18n.cancel}</button>`);

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
        const button = document.getElementById('initiatePixButton');
        const originalText = button.textContent;
        button.disabled = true;
        button.textContent = i18n.wait;
        let result = null;

        try {
            result = await fetchAPI(urls.createChargeUrl, 'POST', payload);

            if (result && result.success && result.free_renewal) {
                const isReactivation = container.dataset.isReactivation === 'true';
                showSuccessState(isReactivation);
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
                button.disabled = false;
                button.textContent = originalText;
            }
        }
    }

    // --- LÓGICA ATUALIZADA PARA SUCESSO E REATIVAÇÃO ---
    function showSuccessState(isReactivation, userStatus) {
        if (paymentSection) paymentSection.style.display = 'none';
        if (pixDisplay) pixDisplay.style.display = 'none';
        if (userInfoHeader) userInfoHeader.style.display = 'none';
        
        const successDisplay = document.getElementById('success-display');
        const successTitle = document.getElementById('success-title');
        const successMessage = document.getElementById('success-message');
        const loginButton = document.getElementById('login-button');
        const reactivationArea = document.getElementById('reactivation-action-area');
        const defaultBtnContainer = document.getElementById('default-login-btn-container');

        if (successTitle && successMessage && loginButton && successDisplay) {
            if (isReactivation) {
                // Se o status ainda for 'inactive', significa que o convite está pendente.
                // Nesse caso, mostramos o botão de login com Plex para aceitar automaticamente.
                if (userStatus === 'inactive') {
                    successTitle.textContent = i18n.reactivationCheckEmailTitle;
                    successMessage.textContent = i18n.reactivationCheckEmailMessage;
                    
                    if (reactivationArea) {
                        reactivationArea.classList.remove('hidden');
                        if(defaultBtnContainer) defaultBtnContainer.classList.add('hidden');
                        
                        const btn = document.getElementById('reactivate-plex-login-btn');
                        if(btn) btn.onclick = startPlexAuthFlow;
                    }
                } else {
                    // Reativação concluída com sucesso (usuário já era amigo ou já aceitou)
                    successTitle.textContent = i18n.reactivationSuccessTitle;
                    successMessage.textContent = i18n.reactivationSuccessMessage;
                    if (reactivationArea) reactivationArea.classList.add('hidden');
                    if(defaultBtnContainer) defaultBtnContainer.classList.remove('hidden');
                }
            } else {
                successTitle.textContent = i18n.renewalSuccessTitle;
                successMessage.textContent = i18n.renewalSuccessMessage;
                if (reactivationArea) reactivationArea.classList.add('hidden');
                if(defaultBtnContainer) defaultBtnContainer.classList.remove('hidden');
            }
            
            loginButton.textContent = i18n.loginButtonText;
            loginButton.href = urls.loginPageUrl;
            
            successDisplay.classList.remove('hidden');
        } else {
            window.location.href = urls.loginPageUrl || '/';
        }
    }

    // --- NOVA LÓGICA DE AUTH PLEX (Para Reativação) ---

    function startPlexAuthFlow() {
        const btn = document.getElementById('reactivate-plex-login-btn');
        btn.disabled = true;
        btn.innerHTML = `<svg class="animate-spin h-5 w-5 mr-3" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg> ${i18n.wait}`;

        window.addEventListener('message', handleAuthMessage, false);
        authWindow = window.open(urls.redirectToAuth, 'plexAuth', 'width=800,height=700,status=no,scrollbars=yes,resizable=yes');
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

        pinCheckIntervalAuth = setInterval(async () => {
            if (!authWindow || authWindow.closed) {
                clearInterval(pinCheckIntervalAuth);
                btn.disabled = false;
                btn.textContent = 'Entrar com Plex para Ativar'; // Fallback de texto se i18n falhar
                return;
            }
            
            try {
                const checkUrl = urls.checkPlexPin.replace('__CLIENT_ID__', client_id).replace('999999', pin_id);
                const checkResponse = await fetch(checkUrl);
                const checkData = await checkResponse.json();

                if (checkData.success && checkData.token) {
                    clearInterval(pinCheckIntervalAuth);
                    authWindow.close();
                    btn.innerHTML = i18n.activating || 'Ativando...';
                    finalizeReactivation(checkData.token);
                } else if (checkData.message === 'auth_denied') {
                    clearInterval(pinCheckIntervalAuth);
                    authWindow.close();
                    showToast(i18n.authDenied, 'error');
                    btn.disabled = false;
                    btn.textContent = 'Tentar Novamente';
                }
            } catch (e) {
                console.error(e);
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
                // Atualiza a UI para sucesso total (esconde botão de reativar, mostra botão de login)
                showSuccessState(true, 'active');
            } else {
                showToast(response.message, 'error');
                const btn = document.getElementById('reactivate-plex-login-btn');
                btn.disabled = false;
                btn.textContent = 'Tentar Novamente';
            }
        } catch (error) {
            showToast(error.message, 'error');
            const btn = document.getElementById('reactivate-plex-login-btn');
            btn.disabled = false;
            btn.textContent = 'Tentar Novamente';
        }
    }

    // --- FIM NOVA LÓGICA ---

    function startPaymentStatusPolling(txid) {
        if (pollingIntervalId) clearInterval(pollingIntervalId);
        const pollingStatus = document.getElementById('polling-status');

        pollingIntervalId = setInterval(async () => {
            try {
                const statusResult = await fetchAPI(urls.getPaymentStatusBaseUrl.replace('__TXID__', txid));
                if (statusResult.success && statusResult.status === 'CONCLUIDA') {
                    clearInterval(pollingIntervalId);
                    const isReactivation = container.dataset.isReactivation === 'true';
                    
                    const confirmedMsg = i18n.paymentConfirmed || 'Pagamento confirmado!';
                    const pollingMsg = i18n.pollingConfirmed || 'Pagamento confirmado! Atualizando...';
                    
                    showToast(confirmedMsg, "success");
                    if(pollingStatus) pollingStatus.innerHTML = `<div class="text-green-500 font-bold p-4">${pollingMsg}</div>`;
                    
                    console.log('Payment confirmed. User Status:', statusResult.user_status, 'Is Reactivation:', isReactivation);

                    // Passa o status do utilizador para determinar a mensagem correta
                    setTimeout(() => showSuccessState(isReactivation, statusResult.user_status), 2000);
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
                paymentSection.innerHTML = `<p class="text-yellow-500">${paymentOptions.message || i18n.noPlanPrice}</p>`;
            }

            const thumbUrl = profileData.profile.thumb;
            const isReactivation = container.dataset.isReactivation === 'true';
            
            const thumbElement = document.getElementById('user-thumb');
            if (thumbUrl) {
                if (thumbUrl.startsWith('http')) {
                    thumbElement.src = thumbUrl;
                } else if (thumbUrl.startsWith('/')) {
                    thumbElement.src = `${window.location.origin}${thumbUrl}`;
                } else {
                    thumbElement.src = thumbUrl;
                }
            } else {
                thumbElement.src = 'https://placehold.co/96x96/1F2937/E5E7EB?text=?';
            }
            
            if (!isReactivation) {
                document.getElementById('user-username').textContent = profileData.profile.username;
            }

            const expirationElem = document.getElementById('user-expiration');
            if (profileData.profile.expiration_date_iso && expirationElem) {
                const expDate = new Date(profileData.profile.expiration_date_iso);
                const today = new Date();
                today.setHours(0, 0, 0, 0);

                if (expDate < today) {
                    expirationElem.textContent = `${i18n.expiredOn} ${profileData.profile.expiration_date_formatted}`;
                    expirationElem.classList.add('text-red-500', 'dark:text-red-400', 'font-semibold');
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
