import { fetchAPI, showToast, createModal } from './utils.js';

document.addEventListener('DOMContentLoaded', async () => {
    // --- ELEMENTOS E DADOS GLOBAIS ---
    const loadingIndicator = document.getElementById('loadingIndicator');
    const container = document.getElementById('paymentContainer');
    const errorContainer = document.getElementById('errorContainer');
    const errorMessage = document.getElementById('errorMessage');
    const paymentSection = document.getElementById('payment-section');
    const pixDisplay = document.getElementById('pix-display');
    const scriptTag = document.getElementById('payment-public-script');
    
    const urls = {};
    const i18n = {};
    let currentUser = null;
    let paymentToken = null;

    if (scriptTag) {
        for (const key in scriptTag.dataset) {
            if (key.startsWith('i18n')) {
                const i18nKey = key.charAt(4).toLowerCase() + key.slice(5);
                i18n[i18nKey] = scriptTag.dataset[key];
            } else {
                const urlKey = key.replace(/-(\w)/g, (match, letter) => letter.toUpperCase());
                urls[urlKey] = scriptTag.dataset[key];
            }
        }
        paymentToken = scriptTag.dataset.token;
    }
    
    let pollingIntervalId = null;
    let validatedCouponCode = null;

    // --- LÓGICA DE PAGAMENTO ---

    function renderPaymentOptions(prices, providers) {
        if (!paymentSection) return;

        const anyProviderEnabled = providers && Object.values(providers).some(enabled => enabled);
        if (!anyProviderEnabled) {
            paymentSection.innerHTML = `<p class="text-gray-500 dark:text-gray-400 text-center">${i18n.noPaymentMethod}</p>`;
            return;
        }

        if (!prices || Object.keys(prices).length === 0) {
            paymentSection.innerHTML = `<p class="text-gray-500 dark:text-gray-400 text-center">${i18n.noPriceDefined}</p>`;
            return;
        }

        let optionsHtml = '<div class="space-y-3">';
        Object.keys(prices).sort((a,b) => parseInt(a) - parseInt(b)).forEach(screens => {
            const price = parseFloat(prices[screens]).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
            
            let planText = i18n.defaultPlan;
            if (screens !== "0") {
                const screenText = parseInt(screens) > 1 ? i18n.screenPlural : i18n.screenSingular;
                planText = `${screens} ${screenText}`;
            }

            optionsHtml += `
                <label class="flex items-center justify-between p-4 rounded-lg border-2 border-gray-300 dark:border-gray-600 has-[:checked]:border-yellow-500 has-[:checked]:ring-2 has-[:checked]:ring-yellow-500 cursor-pointer transition-all">
                    <div class="flex items-center">
                        <input type="radio" name="payment-plan" value="${screens}" data-price="${prices[screens]}" class="h-4 w-4 text-yellow-600 focus:ring-yellow-500 border-gray-300" checked>
                        <span class="ml-3 font-semibold text-gray-800 dark:text-gray-200">${planText}</span>
                    </div>
                    <span class="font-bold text-lg text-gray-900 dark:text-white">${price}</span>
                </label>
            `;
        });
        optionsHtml += '</div>';
        
        optionsHtml += `
            <div class="mt-4">
                <label for="couponCodeInput" class="text-sm font-medium text-gray-500 dark:text-gray-400">${i18n.discountCode}</label>
                <div class="flex gap-2 mt-1">
                    <input type="text" id="couponCodeInput" class="w-full p-2.5 text-sm rounded-lg border bg-gray-50 border-gray-300 text-gray-900 dark:bg-gray-700 dark:border-gray-600 dark:text-white" placeholder="${i18n.insertCoupon}">
                    <button id="applyCouponBtn" class="btn bg-gray-600 hover:bg-gray-500 text-white px-4">${i18n.apply}</button>
                </div>
                <div id="coupon-status" class="text-xs mt-1 h-4"></div>
            </div>
        `;

        paymentSection.innerHTML = `
            ${optionsHtml}
            <button id="initiatePixButton" class="w-full mt-4 btn bg-green-600 hover:bg-green-500 text-white disabled:opacity-50">${i18n.generatePix}</button>
        `;
        
        const selectedPlan = document.querySelector('input[name="payment-plan"]:checked');
        const pixButton = document.getElementById('initiatePixButton');
        if (selectedPlan) {
            const selectedPrice = parseFloat(selectedPlan.dataset.price).toFixed(2);
            pixButton.textContent = i18n.generatePixForPrice.replace('{price}', selectedPrice.replace('.', ','));
        }

        const applyCouponBtn = document.getElementById('applyCouponBtn');
        const couponCodeInput = document.getElementById('couponCodeInput');
        
        if (pixButton) {
            pixButton.addEventListener('click', () => {
                const selectedPlan = document.querySelector('input[name="payment-plan"]:checked');
                if (selectedPlan) {
                    const screens = selectedPlan.value;
                    initiatePixPayment({ screens, coupon_code: validatedCouponCode, username: currentUser.username }, providers);
                }
            });
        }

        if (applyCouponBtn) {
            applyCouponBtn.addEventListener('click', async () => {
                const code = couponCodeInput.value.trim();
                const statusDiv = document.getElementById('coupon-status');
                const selectedPlan = document.querySelector('input[name="payment-plan"]:checked');
                if (!code || !selectedPlan || !currentUser) return;

                const screens = selectedPlan.value;

                try {
                    const result = await fetchAPI(urls.validateCouponUrl, 'POST', { code, screens, username: currentUser.username });
                    if (result.success) {
                        statusDiv.innerHTML = `<span class="text-green-500">${result.message}</span>`;
                        validatedCouponCode = code;
                        const newPriceText = result.discounted_price.toFixed(2).replace('.', ',');
                        
                        if (result.discounted_price <= 0) {
                            pixButton.textContent = i18n.activateFreeSubscription;
                        } else {
                            pixButton.textContent = i18n.generatePixForPrice.replace('{price}', newPriceText);
                        }
                    } else {
                        statusDiv.innerHTML = `<span class="text-red-500">${result.message}</span>`;
                        validatedCouponCode = null;
                        const originalPriceText = parseFloat(selectedPlan.dataset.price).toFixed(2).replace('.', ',');
                        pixButton.textContent = i18n.generatePixForPrice.replace('{price}', originalPriceText);
                    }
                } catch (error) {
                    statusDiv.innerHTML = `<span class="text-red-500">${error.message}</span>`;
                    validatedCouponCode = null;
                    const originalPriceText = parseFloat(selectedPlan.dataset.price).toFixed(2).replace('.', ',');
                    pixButton.textContent = i18n.generatePixForPrice.replace('{price}', originalPriceText);
                }
            });
        }
    }

    async function initiatePixPayment(payload, providers) {
        const activeProviders = Object.keys(providers).filter(p => providers[p]).map(p => p.toUpperCase());
        
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
        // CORREÇÃO: Adiciona o botão para o BPIX
        if (providers.includes('BPIX')) {
            buttonsHtml += `<button data-provider="BPIX" class="btn bg-purple-600 hover:bg-purple-500 text-white w-full mt-2">${i18n.payWithBpix}</button>`;
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
                showToast(result.message, 'success');
                setTimeout(() => window.location.reload(), 3000);
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
            if (!(result && result.success && result.free_renewal)) {
                button.disabled = false;
                button.textContent = originalText;
            }
        }
    }

    function startPaymentStatusPolling(txid) {
        if (pollingIntervalId) clearInterval(pollingIntervalId);
        const pollingStatus = document.getElementById('polling-status');

        pollingIntervalId = setInterval(async () => {
            try {
                const statusResult = await fetchAPI(`/api/payments/status/${txid}`);
                if (statusResult.success && statusResult.status === 'CONCLUIDA') {
                    clearInterval(pollingIntervalId);
                    showToast(i18n.paymentConfirmed, "success");
                    if(pollingStatus) pollingStatus.innerHTML = `<div class="text-green-500 font-bold p-4">${i18n.pollingConfirmed}</div>`;
                    setTimeout(() => window.location.reload(), 3000);
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
        if (!paymentToken) {
            errorMessage.textContent = i18n.invalidLink;
            errorContainer.classList.remove('hidden');
            loadingIndicator.style.display = 'none';
            return;
        }

        try {
            const data = await fetchAPI(`${urls.getPublicProfileUrl}/${paymentToken}`);
            if (!data.success) throw new Error(data.message);
            
            currentUser = data.profile;
            const paymentOptions = await fetchAPI(`${urls.getPaymentOptionsUrl}?token=${paymentToken}`);
            
            if(paymentOptions.success) {
                renderPaymentOptions(paymentOptions.prices, paymentOptions.providers);
            } else {
                throw new Error(paymentOptions.message || i18n.errorLoadingOptions);
            }
            
            document.getElementById('user-thumb').src = data.profile.thumb || 'https://placehold.co/96x96/1F2937/E5E7EB?text=?';
            document.getElementById('user-username').textContent = data.profile.username;
            
            const expirationContainer = document.getElementById('user-expiration-container');
            if (data.profile.expiration_date_formatted) {
                expirationContainer.innerHTML = `${i18n.expiresOn} <span class="font-semibold">${data.profile.expiration_date_formatted}</span>`;
                expirationContainer.classList.remove('hidden');
            }

            loadingIndicator.style.display = 'none';
            container.classList.remove('hidden');

        } catch (error) {
            loadingIndicator.style.display = 'none';
            errorMessage.textContent = error.message;
            errorContainer.classList.remove('hidden');
            showToast(error.message, 'error');
        }
    }
    
    main();
});
