import { fetchAPI, showToast, createModal } from './utils.js';

document.addEventListener('DOMContentLoaded', async () => {
    // --- ELEMENTOS E DADOS GLOBAIS ---
    const loadingIndicator = document.getElementById('loadingIndicator');
    const container = document.getElementById('accountDetailsContainer');
    const errorContainer = document.getElementById('errorContainer');
    const errorMessage = document.getElementById('errorMessage');
    const statusBanner = document.getElementById('status-banner');
    const paymentSection = document.getElementById('payment-section');
    const pixDisplay = document.getElementById('pix-display');
    const scriptTag = document.getElementById('account-script');
    const privacyToggle = document.getElementById('hide-leaderboard-toggle');

    const urls = {};
    const i18n = {};
    if (scriptTag) {
        for (const key in scriptTag.dataset) {
            if (key.startsWith('i18n')) {
                const i18nKey = key.charAt(4).toLowerCase() + key.slice(5);
                i18n[i18nKey] = scriptTag.dataset[key];
            } else {
                urls[key] = scriptTag.dataset[key];
            }
        }
    }
    
    let pollingIntervalId = null;

    // --- LÓGICA DE PAGAMENTO ---

    function renderPaymentOptions(prices, providers, canDowngrade) {
        const paymentCard = document.getElementById('payment-card');
        if (!paymentSection || !paymentCard) return;

        const anyProviderEnabled = providers && Object.values(providers).some(enabled => enabled);

        if (!anyProviderEnabled) {
            paymentCard.style.display = 'none';
            return;
        }

        if (!prices || Object.keys(prices).length === 0) {
            paymentSection.innerHTML = `<p class="text-gray-500 dark:text-gray-400">${i18n.noProvider}</p>`;
            return;
        }

        let optionsHtml = '<div class="space-y-3">';
        Object.keys(prices).sort((a,b) => parseInt(a) - parseInt(b)).forEach(screens => {
            const price = parseFloat(prices[screens]).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
            
            let planText = "Plano Padrão";
            if (screens !== "0") {
                const screenText = parseInt(screens) > 1 ? i18n.screenPlural : i18n.screenSingular;
                planText = `${screens} ${screenText}`;
            }

            optionsHtml += `
                <label class="flex items-center justify-between p-4 rounded-lg border-2 border-gray-300 dark:border-gray-600 has-[:checked]:border-yellow-500 has-[:checked]:ring-2 has-[:checked]:ring-yellow-500 cursor-pointer transition-all">
                    <div class="flex items-center">
                        <input type="radio" name="payment-plan" value="${screens}" data-price="${prices[screens]}" class="h-4 w-4 text-yellow-600 focus:ring-yellow-500 border-gray-300">
                        <span class="ml-3 font-semibold text-gray-800 dark:text-gray-200">${planText}</span>
                    </div>
                    <span class="font-bold text-lg text-gray-900 dark:text-white">${price}</span>
                </label>
            `;
        });
        optionsHtml += '</div>';
        
        if (!canDowngrade && Object.keys(prices).length > 1) {
            optionsHtml += `
                <div class="mt-4 p-3 text-sm text-blue-700 bg-blue-100 rounded-lg dark:bg-blue-900/30 dark:text-blue-300" role="alert">
                  <span class="font-medium">Info:</span> A troca para um plano com menos telas só fica disponível perto da data de vencimento da sua assinatura atual.
                </div>
            `;
        }

        paymentSection.innerHTML = `
            ${optionsHtml}
            <button id="initiatePixButton" class="w-full mt-4 btn bg-green-600 hover:bg-green-500 text-white disabled:opacity-50" disabled>${i18n.generatePix}</button>
        `;

        const pixButton = document.getElementById('initiatePixButton');
        document.querySelectorAll('input[name="payment-plan"]').forEach(radio => {
            radio.addEventListener('change', () => {
                const selectedPrice = parseFloat(radio.dataset.price).toFixed(2);
                pixButton.textContent = i18n.generatePixForPrice.replace('{price}', selectedPrice.replace('.', ','));
                pixButton.disabled = false;
            });
        });

        if (pixButton) {
            pixButton.addEventListener('click', () => {
                const selectedPlan = document.querySelector('input[name="payment-plan"]:checked');
                if (selectedPlan) {
                    const screens = selectedPlan.value;
                    initiatePixPayment({ screens }, providers);
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

        try {
            const result = await fetchAPI(urls.createChargeUrl, 'POST', payload);
            
            if(result && result.success) {
                paymentSection.style.display = 'none';
                pixDisplay.style.display = 'block';
                document.getElementById('pix-qr-code').src = result.qr_code_image;
                document.getElementById('pix-copy-paste').value = result.pix_copy_paste;
                startPaymentStatusPolling(result.payment_id || result.txid);
            }
        } catch (error) {
            showToast(error.message, 'error');
        } finally {
            button.disabled = false;
            button.textContent = originalText;
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

    async function handlePrivacyToggle() {
        if (!privacyToggle) return;
        
        privacyToggle.addEventListener('change', async (event) => {
            const isHidden = event.target.checked;
            try {
                await fetchAPI(urls.updatePrivacyUrl, 'POST', { hide: isHidden });
                showToast('Configuração de privacidade atualizada!', 'success');
            } catch (error) {
                showToast(error.message, 'error');
                // Reverte o toggle em caso de erro
                event.target.checked = !isHidden;
            }
        });
    }

    function renderPaymentHistory(payments) {
        const container = document.getElementById('payment-history-container');
        if (!container) return;

        if (payments && payments.length > 0) {
            container.innerHTML = `
                <div class="overflow-x-auto">
                    <table class="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
                        <thead class="bg-gray-50 dark:bg-gray-700/50">
                            <tr>
                                <th class="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase">${i18n.date}</th>
                                <th class="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase">${i18n.description}</th>
                                <th class="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase">${i18n.value}</th>
                                <th class="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase">${i18n.status}</th>
                            </tr>
                        </thead>
                        <tbody class="bg-white dark:bg-gray-800 divide-y divide-gray-200 dark:divide-gray-700">
                            ${payments.map(p => `
                                <tr>
                                    <td class="px-4 py-2 whitespace-nowrap text-sm">${new Date(p.created_at).toLocaleString('pt-BR')}</td>
                                    <td class="px-4 py-2 text-sm">${p.description || `${p.provider} - ${p.screens > 0 ? `${p.screens} Telas` : 'Padrão'}`}</td>
                                    <td class="px-4 py-2 text-sm font-mono">${p.value.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })}</td>
                                    <td class="px-4 py-2 text-sm"><span class="px-2 inline-flex text-xs leading-5 font-semibold rounded-full ${p.status === 'CONCLUIDA' ? 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-300' : 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-300'}">${p.status}</span></td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
            `;
        } else {
            container.innerHTML = `<p class="text-gray-500 dark:text-gray-400 text-center py-4">${i18n.noPaymentsFound}</p>`;
        }
    }

    async function main() {
        try {
            const data = await fetchAPI(urls.getAccountDetailsUrl);
            
            const paymentOptions = await fetchAPI(urls.getPaymentOptionsUrl);
            if(paymentOptions.success) {
                renderPaymentOptions(paymentOptions.prices, paymentOptions.providers, paymentOptions.can_downgrade);
            }
            
            const expiration = data.expiration_info;
            
            if (data.is_blocked) {
                switch (data.block_reason) {
                    case 'trial_expired':
                        statusBanner.innerHTML = `<div class="bg-orange-100 dark:bg-orange-900/30 border-l-4 border-orange-500 text-orange-700 dark:text-orange-300 p-4 rounded-lg shadow-md"><h3 class="font-bold">${i18n.testEnded}</h3><p>${i18n.testEndedMessage}</p></div>`;
                        break;
                    case 'expired':
                        statusBanner.innerHTML = `<div class="bg-red-100 dark:bg-red-900/30 border-l-4 border-red-500 text-red-700 dark:text-red-300 p-4 rounded-lg shadow-md"><h3 class="font-bold">${i18n.expiredSignature}</h3><p>${i18n.expiredSignatureMessage}</p></div>`;
                        break;
                    default: // 'manual' ou qualquer outro motivo não especificado
                        statusBanner.innerHTML = `<div class="bg-red-800/80 border-l-4 border-red-400 text-white p-6 rounded-lg shadow-lg"><h3 class="font-bold text-xl mb-2">${i18n.accessBlocked}</h3><p>${i18n.accessBlockedMessage}</p><p class="mt-2">${i18n.accessBlockedContact}</p></div>`;
                        if (container) container.style.display = 'none'; // Esconde opções de pagamento se bloqueado manualmente
                        break;
                }
            } else if (expiration.status === 'expired') {
                // Fallback para caso o agendador ainda não tenha bloqueado o utilizador
                statusBanner.innerHTML = `<div class="bg-red-100 dark:bg-red-900/30 border-l-4 border-red-500 text-red-700 dark:text-red-300 p-4 rounded-lg shadow-md"><h3 class="font-bold">${i18n.expiredSignature}</h3><p>${i18n.expiredSignatureMessage}</p></div>`;
            } else if (expiration.status === 'expiring') {
                let expiringMessage;
                if (expiration.days_left === 0) {
                    expiringMessage = i18n.expiresTodayMessage.replace('{date}', `<strong>${expiration.date}</strong>`);
                } else {
                    expiringMessage = i18n.expiringAccessMessage.replace('{days}', `<strong>${expiration.days_left}</strong>`).replace('{date}', `<strong>${expiration.date}</strong>`);
                }
                 statusBanner.innerHTML = `<div class="bg-yellow-100 dark:bg-yellow-500/20 border-l-4 border-yellow-500 text-yellow-700 dark:text-yellow-300 p-4 rounded-lg shadow-md"><h3 class="font-bold">${i18n.expiringAccess}</h3><p>${expiringMessage}</p></div>`;
            }
            
            if (statusBanner && statusBanner.innerHTML) {
                statusBanner.classList.remove('hidden');
            }

            document.getElementById('user-thumb').src = data.thumb || 'https://placehold.co/96x96/1F2937/E5E7EB?text=?';
            document.getElementById('user-username').textContent = data.username;
            document.getElementById('user-email').textContent = data.email;
            document.getElementById('user-join-date').textContent = data.join_date;
            document.getElementById('user-screen-limit').textContent = data.screen_limit;
            
            if (privacyToggle) {
                privacyToggle.checked = data.hide_from_leaderboard;
            }
            
            const expirationContainer = document.getElementById('user-expiration-container');
            if (expiration.date && expirationContainer) {
                expirationContainer.innerHTML = `${i18n.expiresOn} <span class="font-semibold">${expiration.date}</span>`;
                expirationContainer.classList.remove('hidden');
            }

            const libraryList = document.getElementById('library-list');
            if (data.libraries && data.libraries.length > 0) {
                libraryList.innerHTML = data.libraries.map(lib => `<span class="bg-blue-100 text-blue-800 text-sm font-medium me-2 px-2.5 py-0.5 rounded dark:bg-blue-900 dark:text-blue-300">${lib}</span>`).join('');
            } else {
                if (libraryList) libraryList.innerHTML = `<p class="text-gray-500 dark:text-gray-400">${i18n.noSharedLibrary}</p>`;
            }

            const paymentHistory = await fetchAPI(urls.getPaymentHistoryUrl.replace('__USERNAME__', data.username));
            if (paymentHistory.success) {
                renderPaymentHistory(paymentHistory.payments);
            }

            if (loadingIndicator) loadingIndicator.style.display = 'none';
            if (container) container.classList.remove('hidden');

        } catch (error) {
            if (loadingIndicator) loadingIndicator.style.display = 'none';
            if (errorMessage) errorMessage.textContent = error.message;
            if (errorContainer) errorContainer.classList.remove('hidden');
            showToast(error.message, 'error');
        }
    }
    
    main();
    handlePrivacyToggle();
});
