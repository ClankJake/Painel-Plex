import { fetchAPI, showToast, createModal } from './utils.js';

// ==========================================
// SEGURANÇA E UTILITÁRIOS
// ==========================================

/**
 * Sanitiza entradas do utilizador para prevenir XSS (Cross-Site Scripting).
 */
const sanitizeHTML = (str) => {
    if (!str) return '';
    const temp = document.createElement('div');
    temp.textContent = str;
    return temp.innerHTML;
};

// ==========================================
// CONFIGURAÇÃO, ESTADO E CACHE DOM
// ==========================================

const state = {
    currentUser: null,
    urls: {},
    i18n: {},
    pollingIntervalId: null,
    validatedCouponCode: null,
    historySearchTimeout: null,
    currentRequestFilter: 'all',
    currentPage: 1
};

const dom = {};

const initializeConfigAndDOM = () => {
    // Cache de Elementos Principais
    Object.assign(dom, {
        loadingIndicator: document.getElementById('loadingIndicator'),
        container: document.getElementById('accountDetailsContainer'),
        errorContainer: document.getElementById('errorContainer'),
        errorMessage: document.getElementById('errorMessage'),
        statusBanner: document.getElementById('status-banner'),
        paymentSection: document.getElementById('payment-section'),
        pixDisplay: document.getElementById('pix-display'),
        privacyToggle: document.getElementById('hide-leaderboard-toggle'),
        accountContent: document.getElementById('main-account-content'),
        tabContainer: document.getElementById('account-tabs'),
        contentContainer: document.getElementById('account-tab-content')
    });

    // Extração de URLs e Traduções injetados pelo backend no script tag
    const scriptTag = document.getElementById('account-script');
    if (scriptTag && scriptTag.dataset) {
        for (const [key, value] of Object.entries(scriptTag.dataset)) {
            if (key.startsWith('i18n')) {
                const i18nKey = key.charAt(4).toLowerCase() + key.slice(5);
                state.i18n[i18nKey] = value;
            } else {
                const urlKey = key.replace(/-(\w)/g, (_, letter) => letter.toUpperCase());
                state.urls[urlKey] = value;
            }
        }
    }
};

// ==========================================
// FORMATAÇÃO E HELPERS DE UI
// ==========================================

const formatTimeAgo = (date) => {
    const seconds = Math.floor((new Date() - date) / 1000);
    const intervals = [
        { label: state.i18n.yearsAgo, s: 31536000 },
        { label: state.i18n.monthsAgo, s: 2592000 },
        { label: state.i18n.daysAgo, s: 86400 },
        { label: state.i18n.hoursAgo, s: 3600 },
        { label: state.i18n.minutesAgo, s: 60 }
    ];
    for (const int of intervals) {
        const count = seconds / int.s;
        if (count >= 1) return int.label.replace('{count}', Math.floor(count));
    }
    return state.i18n.justNow || 'agora mesmo';
};

// ==========================================
// RENDERIZADORES DE COMPONENTES (UI)
// ==========================================

const renderStatusBanner = (data, expiration) => {
    if (!dom.statusBanner) return;
    const { i18n } = state;
    let bannerHtml = '';

    if (data.is_blocked) {
        switch (data.block_reason) {
            case 'trial_expired':
                bannerHtml = `<div class="bg-orange-100 dark:bg-orange-900/30 border-l-4 border-orange-500 text-orange-700 dark:text-orange-300 p-4 rounded-lg shadow-md"><h3 class="font-bold">${i18n.testEnded}</h3><p>${i18n.testEndedMessage}</p></div>`;
                break;
            case 'expired':
                bannerHtml = `<div class="bg-red-100 dark:bg-red-900/30 border-l-4 border-red-500 text-red-700 dark:text-red-300 p-4 rounded-lg shadow-md"><h3 class="font-bold">${i18n.expiredSignature}</h3><p>${i18n.expiredSignatureMessage}</p></div>`;
                break;
            default: // Bloqueio manual
                bannerHtml = `<div class="bg-red-800/80 border-l-4 border-red-400 text-white p-6 rounded-lg shadow-lg"><h3 class="font-bold text-xl mb-2">${i18n.accessBlocked}</h3><p>${i18n.accessBlockedMessage}</p><p class="mt-2">${i18n.accessBlockedContact}</p></div>`;
                if (dom.accountContent) dom.accountContent.style.display = 'none';
                break;
        }
    } else if (expiration.status === 'expired') {
        bannerHtml = `<div class="bg-red-100 dark:bg-red-900/30 border-l-4 border-red-500 text-red-700 dark:text-red-300 p-4 rounded-lg shadow-md"><h3 class="font-bold">${i18n.expiredSignature}</h3><p>${i18n.expiredSignatureMessage}</p></div>`;
    } else if (expiration.status === 'expiring') {
        const expiringMessage = expiration.days_left === 0 
            ? i18n.expiresTodayMessage.replace('{date}', `<strong>${expiration.date}</strong>`)
            : i18n.expiringAccessMessage.replace('{days}', `<strong>${expiration.days_left}</strong>`).replace('{date}', `<strong>${expiration.date}</strong>`);
        bannerHtml = `<div class="bg-yellow-100 dark:bg-yellow-500/20 border-l-4 border-yellow-500 text-yellow-700 dark:text-yellow-300 p-4 rounded-lg shadow-md"><h3 class="font-bold">${i18n.expiringAccess}</h3><p>${expiringMessage}</p></div>`;
    }

    if (bannerHtml) {
        dom.statusBanner.innerHTML = bannerHtml;
        dom.statusBanner.classList.remove('hidden');
    }
};

const renderProfileBaseInfo = (data, expiration) => {
    document.getElementById('user-thumb').src = data.thumb || 'https://placehold.co/96x96/1F2937/E5E7EB?text=?';
    document.getElementById('user-username').textContent = data.username;
    document.getElementById('user-email').textContent = data.email;
    document.getElementById('user-join-date').textContent = data.join_date;
    document.getElementById('user-screen-limit').textContent = data.screen_limit;
    
    if (dom.privacyToggle) dom.privacyToggle.checked = data.hide_from_leaderboard;
    
    const expContainer = document.getElementById('user-expiration-container');
    if (expiration.date && expContainer) {
        expContainer.innerHTML = `${state.i18n.expiresOn} <span class="font-semibold">${expiration.date}</span>`;
        expContainer.classList.remove('hidden');
    }

    const libraryList = document.getElementById('library-list');
    if (libraryList) {
        libraryList.innerHTML = (data.libraries && data.libraries.length > 0)
            ? data.libraries.map(lib => `<span class="bg-blue-100 text-blue-800 text-xs font-medium px-2.5 py-0.5 rounded dark:bg-blue-900/40 dark:text-blue-300 border border-blue-200 dark:border-blue-800">${lib}</span>`).join(' ')
            : `<p class="text-gray-500 dark:text-gray-400 text-sm">${state.i18n.noSharedLibrary}</p>`;
    }
};

const renderDeviceList = (devices) => {
    const container = document.getElementById('device-list-container');
    if (!container) return;

    if (!devices || devices.length === 0) {
        container.innerHTML = `<p class="text-gray-500 dark:text-gray-400 text-center py-4">${state.i18n.noDevicesFound}</p>`;
        return;
    }

    const platformMap = ['alexa', 'android', 'atv', 'chrome', 'chromecast', 'dlna', 'firefox', 'gtv', 'ie', 'ios', 'kodi', 'lg', 'linux', 'macos', 'msedge', 'opera', 'playstation', 'plex', 'plexamp', 'roku', 'safari', 'samsung', 'tivo', 'windows', 'xbox'];

    container.innerHTML = devices.map(device => {
        const lastSeen = new Date(device.last_seen * 1000);
        const platform = (device.platform || '').toLowerCase().split(' ')[0];
        const platformClass = platformMap.includes(platform) ? `platform-${platform}` : 'platform-default';

        return `
            <div class="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-700/50 rounded-lg border border-gray-100 dark:border-gray-600/50">
                <div class="flex items-center gap-4">
                    <div class="platform-icon ${platformClass} flex-shrink-0"></div>
                    <div>
                        <p class="font-semibold text-gray-800 dark:text-gray-200 text-sm">${device.player}</p>
                        <p class="text-xs text-gray-500 dark:text-gray-400">${device.platform}</p>
                    </div>
                </div>
                <div class="text-right flex-shrink-0">
                    <p class="text-[10px] uppercase tracking-wider text-gray-500 dark:text-gray-400">${state.i18n.lastSeen}</p>
                    <p class="text-sm font-semibold text-gray-800 dark:text-gray-200">${formatTimeAgo(lastSeen)}</p>
                </div>
            </div>
        `;
    }).join('');
};

const renderPaymentHistory = (payments) => {
    const container = document.getElementById('payment-history-container');
    if (!container) return;

    if (!payments || payments.length === 0) {
        container.innerHTML = `<p class="text-gray-500 dark:text-gray-400 text-center py-4">${state.i18n.noPaymentsFound}</p>`;
        return;
    }

    container.innerHTML = `
        <div class="overflow-x-auto rounded-xl border border-gray-200 dark:border-gray-700">
            <table class="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
                <thead class="bg-gray-50 dark:bg-gray-800/80">
                    <tr>
                        <th class="px-4 py-3 text-left text-xs font-bold text-gray-500 dark:text-gray-400 uppercase tracking-wider">${state.i18n.date}</th>
                        <th class="px-4 py-3 text-left text-xs font-bold text-gray-500 dark:text-gray-400 uppercase tracking-wider">${state.i18n.description}</th>
                        <th class="px-4 py-3 text-left text-xs font-bold text-gray-500 dark:text-gray-400 uppercase tracking-wider">${state.i18n.value}</th>
                        <th class="px-4 py-3 text-left text-xs font-bold text-gray-500 dark:text-gray-400 uppercase tracking-wider">${state.i18n.status}</th>
                    </tr>
                </thead>
                <tbody class="bg-white dark:bg-gray-800 divide-y divide-gray-200 dark:divide-gray-700">
                    ${payments.map(p => {
                        const isOk = p.status === 'CONCLUIDA';
                        const badgeClass = isOk ? 'bg-green-100 text-green-800 dark:bg-green-900/50 dark:text-green-300' : 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/50 dark:text-yellow-300';
                        const couponHtml = p.coupon_code ? `<span class="ml-2 px-2 py-0.5 text-[10px] uppercase font-bold rounded-full bg-indigo-100 text-indigo-800 dark:bg-indigo-900/50 dark:text-indigo-300 border border-indigo-200 dark:border-indigo-800" title="Cupão: ${p.coupon_code}">🏷️ ${p.coupon_code}</span>` : '';
                        
                        return `
                            <tr class="hover:bg-gray-50 dark:hover:bg-gray-700/30 transition-colors">
                                <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-600 dark:text-gray-300">${new Date(p.created_at).toLocaleString('pt-BR')}</td>
                                <td class="px-4 py-3 text-sm text-gray-800 dark:text-gray-200">
                                    <span class="font-medium">${p.description || `${p.provider} - ${p.screens > 0 ? `${p.screens} Telas` : 'Padrão'}`}</span>
                                    ${couponHtml}
                                </td>
                                <td class="px-4 py-3 text-sm font-mono font-bold text-gray-900 dark:text-white">${p.value.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })}</td>
                                <td class="px-4 py-3 text-sm"><span class="px-2.5 py-1 inline-flex text-xs leading-5 font-semibold rounded-full ${badgeClass}">${p.status}</span></td>
                            </tr>
                        `;
                    }).join('')}
                </tbody>
            </table>
        </div>
    `;
};

// ==========================================
// LÓGICA DE PAGAMENTOS E PIX
// ==========================================

const setupPaymentSection = (prices, providers, canDowngrade) => {
    const paymentCard = document.getElementById('payment-card');
    if (!dom.paymentSection || !paymentCard) return;

    const anyProviderEnabled = providers && Object.values(providers).some(enabled => enabled);
    if (!anyProviderEnabled) {
        paymentCard.style.display = 'none';
        return;
    }

    if (!prices || Object.keys(prices).length === 0) {
        dom.paymentSection.innerHTML = `<p class="text-gray-500 dark:text-gray-400">${state.i18n.noProvider}</p>`;
        return;
    }

    // Gerar Planos
    let optionsHtml = '<div class="space-y-3">';
    Object.keys(prices).sort((a,b) => parseInt(a) - parseInt(b)).forEach(screens => {
        const priceStr = parseFloat(prices[screens]).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
        const planText = screens === "0" ? "Plano Padrão" : `${screens} ${parseInt(screens) > 1 ? state.i18n.screenPlural : state.i18n.screenSingular}`;

        optionsHtml += `
            <label class="flex items-center justify-between p-4 rounded-xl border-2 border-gray-200 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/30 has-[:checked]:border-yellow-500 has-[:checked]:bg-yellow-50/30 dark:has-[:checked]:bg-yellow-900/10 has-[:checked]:ring-1 has-[:checked]:ring-yellow-500 cursor-pointer transition-all hover:border-yellow-400">
                <div class="flex items-center">
                    <input type="radio" name="payment-plan" value="${screens}" data-price="${prices[screens]}" class="h-5 w-5 text-yellow-500 focus:ring-yellow-500 border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700">
                    <span class="ml-3 font-bold text-gray-800 dark:text-gray-200">${planText}</span>
                </div>
                <span class="font-black text-lg text-gray-900 dark:text-white">${priceStr}</span>
            </label>
        `;
    });
    optionsHtml += '</div>';
    
    if (!canDowngrade && Object.keys(prices).length > 1) {
        optionsHtml += `<div class="mt-4 p-3 text-sm text-blue-800 bg-blue-50 rounded-lg dark:bg-blue-900/20 dark:text-blue-300 border border-blue-100 dark:border-blue-800/50 flex gap-2 items-start"><span class="text-lg leading-none">ℹ️</span> <span>A troca para um plano com menos telas só fica disponível perto da data de vencimento.</span></div>`;
    }

    // Injetar HTML Final
    dom.paymentSection.innerHTML = `
        ${optionsHtml}
        <div class="mt-6 pt-6 border-t border-gray-100 dark:border-gray-700">
            <label for="couponCodeInput" class="text-sm font-bold text-gray-700 dark:text-gray-300 uppercase tracking-wider">Código de Desconto</label>
            <div class="flex gap-2 mt-2">
                <input type="text" id="couponCodeInput" class="w-full p-3 text-sm font-mono uppercase tracking-wider rounded-lg border bg-white border-gray-300 text-gray-900 focus:ring-yellow-500 focus:border-yellow-500 dark:bg-gray-800 dark:border-gray-600 dark:text-white" placeholder="INSERIR CUPÃO">
                <button id="applyCouponBtn" class="btn bg-gray-800 hover:bg-gray-700 dark:bg-gray-600 dark:hover:bg-gray-500 text-white px-6 font-semibold" disabled>Aplicar</button>
            </div>
            <div id="coupon-status" class="text-xs font-medium mt-2 min-h-[20px]"></div>
        </div>
        <button id="initiatePixButton" class="w-full mt-6 btn bg-green-600 hover:bg-green-500 text-white text-lg py-3 shadow-lg shadow-green-500/30 disabled:opacity-50 disabled:shadow-none transition-all" disabled>${state.i18n.generatePix}</button>
    `;

    bindPaymentEvents(providers);
};

const bindPaymentEvents = (providers) => {
    const pixBtn = document.getElementById('initiatePixButton');
    const applyCouponBtn = document.getElementById('applyCouponBtn');
    const couponInput = document.getElementById('couponCodeInput');
    const statusDiv = document.getElementById('coupon-status');

    // Mudança de plano selecionado
    document.querySelectorAll('input[name="payment-plan"]').forEach(radio => {
        radio.addEventListener('change', () => {
            const price = parseFloat(radio.dataset.price).toFixed(2).replace('.', ',');
            pixBtn.textContent = state.i18n.generatePixForPrice.replace('{price}', price);
            pixBtn.disabled = false;
            applyCouponBtn.disabled = false;
            
            // Reset Cupão
            statusDiv.innerHTML = '';
            couponInput.value = '';
            state.validatedCouponCode = null;
        });
    });

    // Validar Cupão
    applyCouponBtn?.addEventListener('click', async () => {
        const code = sanitizeHTML(couponInput.value.trim().toUpperCase());
        const selectedPlan = document.querySelector('input[name="payment-plan"]:checked');
        if (!code || !selectedPlan || !state.currentUser) return;

        try {
            applyCouponBtn.disabled = true;
            applyCouponBtn.textContent = '...';
            const result = await fetchAPI(state.urls.validateCouponUrl, 'POST', { code, screens: selectedPlan.value, username: state.currentUser.username });
            
            if (result.success) {
                statusDiv.innerHTML = `<span class="text-green-600 dark:text-green-400 flex items-center gap-1"><svg class="w-4 h-4" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd"></path></svg> ${result.message}</span>`;
                state.validatedCouponCode = code;
                
                if (result.discounted_price <= 0) {
                    pixBtn.textContent = state.i18n.activateFreeSubscription;
                    pixBtn.classList.replace('bg-green-600', 'bg-yellow-500');
                    pixBtn.classList.replace('hover:bg-green-500', 'hover:bg-yellow-400');
                    pixBtn.classList.replace('shadow-green-500/30', 'shadow-yellow-500/30');
                } else {
                    pixBtn.textContent = state.i18n.generatePixForPrice.replace('{price}', result.discounted_price.toFixed(2).replace('.', ','));
                }
            } else {
                throw new Error(result.message);
            }
        } catch (error) {
            statusDiv.innerHTML = `<span class="text-red-500 flex items-center gap-1">❌ ${error.message}</span>`;
            state.validatedCouponCode = null;
            pixBtn.textContent = state.i18n.generatePixForPrice.replace('{price}', parseFloat(selectedPlan.dataset.price).toFixed(2).replace('.', ','));
        } finally {
            applyCouponBtn.disabled = false;
            applyCouponBtn.textContent = 'Aplicar';
        }
    });

    // Iniciar Pagamento
    pixBtn?.addEventListener('click', () => {
        const plan = document.querySelector('input[name="payment-plan"]:checked');
        if (plan) handlePixGenerationRequest({ screens: plan.value, coupon_code: state.validatedCouponCode }, providers);
    });
};

const handlePixGenerationRequest = async (payload, providers) => {
    const activeProviders = Object.keys(providers).filter(p => providers[p]).map(p => p.toUpperCase());
    const isFree = document.getElementById('initiatePixButton').textContent === state.i18n.activateFreeSubscription;

    if (isFree) {
        await executePixGeneration(payload); // Ignora provedores se for gratuito
        return;
    }

    if (activeProviders.length === 1) {
        payload.provider = activeProviders[0];
        await executePixGeneration(payload);
    } else if (activeProviders.length > 1) {
        // Modal de escolha se houver +1 gateway configurado
        const buttonsHtml = activeProviders.map(p => {
            const colors = p === 'MERCADOPAGO' ? 'bg-blue-600 hover:bg-blue-500' : p === 'BPIX' ? 'bg-purple-600 hover:bg-purple-500' : 'bg-green-600 hover:bg-green-500';
            const name = p === 'MERCADOPAGO' ? state.i18n.payWithMp : p === 'BPIX' ? 'Pagar com BPIX' : state.i18n.payWithEfi;
            return `<button data-provider="${p}" class="btn ${colors} text-white w-full mb-2 shadow-md">${name}</button>`;
        }).join('');

        const modal = createModal('providerChoiceModal', state.i18n.chooseProvider, `<div class="pt-2">${buttonsHtml}</div>`, `<button id="cancel-provider" class="btn bg-gray-200 dark:bg-gray-700 w-full mt-2">${state.i18n.cancel}</button>`);
        
        if (modal) {
            modal.querySelectorAll('button[data-provider]').forEach(btn => {
                btn.onclick = async () => {
                    modal.classList.add('hidden');
                    payload.provider = btn.dataset.provider;
                    await executePixGeneration(payload);
                };
            });
            modal.querySelector('#cancel-provider').onclick = () => modal.classList.add('hidden');
        }
    } else {
        showToast(state.i18n.noProvider, "error");
    }
};

const executePixGeneration = async (payload) => {
    const btn = document.getElementById('initiatePixButton');
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.innerHTML = `<svg class="animate-spin h-5 w-5 mr-2 inline-block" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" fill="none"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg> A processar...`;

    try {
        const result = await fetchAPI(state.urls.createChargeUrl, 'POST', payload);
        
        if (result && result.success) {
            if (result.free_renewal) {
                showToast(result.message, 'success');
                setTimeout(() => window.location.reload(), 2500);
            } else {
                dom.paymentSection.style.display = 'none';
                dom.pixDisplay.style.display = 'block';
                document.getElementById('pix-qr-code').src = result.qr_code_image;
                document.getElementById('pix-copy-paste').value = result.pix_copy_paste;
                startPaymentPolling(result.payment_id || result.txid);
            }
        } else {
            throw new Error(result?.message || 'Erro desconhecido ao gerar pagamento.');
        }
    } catch (error) {
        showToast(error.message, 'error');
        btn.disabled = false;
        btn.textContent = originalText;
    }
};

const startPaymentPolling = (txid) => {
    if (state.pollingIntervalId) clearInterval(state.pollingIntervalId);
    const pollingStatus = document.getElementById('polling-status');

    state.pollingIntervalId = setInterval(async () => {
        try {
            const result = await fetchAPI(`/api/payments/status/${txid}`);
            if (result.success && result.status === 'CONCLUIDA') {
                clearInterval(state.pollingIntervalId);
                showToast(state.i18n.paymentConfirmed, "success");
                
                if (pollingStatus) {
                    pollingStatus.innerHTML = `
                        <div class="flex flex-col items-center justify-center p-4 bg-green-50 dark:bg-green-900/30 rounded-lg border border-green-200 dark:border-green-800">
                            <svg class="w-12 h-12 text-green-500 mb-2" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
                            <span class="text-green-700 dark:text-green-400 font-bold text-lg">${state.i18n.pollingConfirmed}</span>
                        </div>`;
                }
                setTimeout(() => window.location.reload(), 3000);
            }
        } catch (error) {
            console.warn(`Polling silencioso falhou:`, error.message);
        }
    }, 4000); // 4 segundos é ideal para APIs de pagamento
};

// ==========================================
// FORMULÁRIO DE CONTACTOS
// ==========================================

const initContactForm = (details) => {
    if (!document.getElementById('contact-details-form')) return;

    const countries = [
        { name: 'Brasil', code: '+55' }, { name: 'Portugal', code: '+351' },
        { name: 'Angola', code: '+244' }, { name: 'Moçambique', code: '+258' },
        { name: 'Cabo Verde', code: '+238' }, { name: 'EUA/Canadá', code: '+1' },
        { name: 'Reino Unido', code: '+44' }, { name: 'Espanha', code: '+34' },
        { name: 'França', code: '+33' }, { name: 'Alemanha', code: '+49' }
    ];

    const select = document.getElementById('countryCode');
    select.innerHTML = countries.map(c => `<option value="${c.code}">${c.name} (${c.code})</option>`).join('');

    if (details) {
        document.getElementById('profileName').value = details.name || '';
        document.getElementById('profileTelegram').value = details.telegram_user || '';
        document.getElementById('profileDiscord').value = details.discord_user_id || '';
        
        const fullPhone = details.phone_number || '';
        const phoneInput = document.getElementById('profilePhone');
        
        const match = countries.slice().sort((a, b) => b.code.length - a.code.length).find(c => fullPhone.startsWith(c.code));
        if (match) {
            select.value = match.code;
            phoneInput.value = fullPhone.substring(match.code.length);
        } else {
            phoneInput.value = fullPhone.replace(/\D/g, '');
            select.value = '+55'; // Default fallback
        }
    }

    document.getElementById('saveContactDetails').addEventListener('click', async (e) => {
        const btn = e.target;
        const origText = btn.textContent;
        btn.disabled = true;
        btn.textContent = state.i18n.saving;

        try {
            const phone = document.getElementById('profilePhone').value.replace(/\D/g, '');
            const payload = {
                name: sanitizeHTML(document.getElementById('profileName').value),
                telegram_user: sanitizeHTML(document.getElementById('profileTelegram').value),
                discord_user_id: sanitizeHTML(document.getElementById('profileDiscord').value),
                phone_number: phone ? `${document.getElementById('countryCode').value}${phone}` : '',
            };
            const result = await fetchAPI(state.urls.updateAccountProfileUrl, 'POST', payload);
            showToast(result.message, result.success ? 'success' : 'error');
        } catch (error) {
            showToast(error.message, 'error');
        } finally {
            btn.disabled = false;
            btn.textContent = origText;
        }
    });
};

// ==========================================
// HISTÓRICO E TABS DE NAVEGAÇÃO
// ==========================================

const initTabs = () => {
    if (!dom.tabContainer || !dom.contentContainer) return;

    // Mostrar aba Requests apenas se permitido e não for admin
    if (state.currentUser && !state.currentUser.is_admin && state.currentUser.profile_details?.overseerr_access) {
        dom.tabContainer.querySelector('[data-tab="requests"]')?.classList.remove('hidden');
    }

    // Admins não veem Overview e Pagamento na sua própria conta
    if (!state.currentUser || state.currentUser.is_admin) {
        ['overview', 'payment'].forEach(t => {
            const el = dom.tabContainer.querySelector(`[data-tab="${t}"]`);
            if(el) el.style.display = 'none';
        });
        // Se a default estava oculta, muda para history
        dom.tabContainer.querySelector('[data-tab="history"]')?.click();
    }

    dom.tabContainer.addEventListener('click', (e) => {
        const btn = e.target.closest('.tab-button');
        if (!btn || !btn.dataset.tab) return;

        dom.tabContainer.querySelectorAll('.tab-button').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');

        dom.contentContainer.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        document.getElementById(`tab-${btn.dataset.tab}`).classList.add('active');
        
        if (btn.dataset.tab === 'history') fetchWatchHistory();
    });
};

const fetchWatchHistory = async (page = 1, search = '') => {
    const hc = document.getElementById('history-container');
    const pc = document.getElementById('history-pagination');
    if (!hc) return;

    hc.innerHTML = `<div class="flex justify-center p-8"><div class="animate-spin rounded-full h-8 w-8 border-b-2 border-yellow-500"></div></div>`;
    
    try {
        const data = await fetchAPI(`${state.urls.getWatchHistoryUrl}?page=${page}&search=${encodeURIComponent(search)}`);
        if (!data.success) throw new Error(data.message);

        if (data.history.length === 0) {
            hc.innerHTML = `<p class="text-center text-gray-500 dark:text-gray-400 py-8">${state.i18n.noHistoryFound}</p>`;
            pc.innerHTML = '';
            return;
        }

        hc.innerHTML = `
            <div class="overflow-x-auto rounded-lg border border-gray-200 dark:border-gray-700">
                <table class="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
                    <thead class="bg-gray-50 dark:bg-gray-800/80">
                        <tr>
                            <th class="px-4 py-3 text-left text-xs font-bold text-gray-500 dark:text-gray-400 uppercase">${state.i18n.title}</th>
                            <th class="px-4 py-3 text-left text-xs font-bold text-gray-500 dark:text-gray-400 uppercase">${state.i18n.date}</th>
                            <th class="px-4 py-3 text-left text-xs font-bold text-gray-500 dark:text-gray-400 uppercase">${state.i18n.player}</th>
                            <th class="px-4 py-3 text-left text-xs font-bold text-gray-500 dark:text-gray-400 uppercase">${state.i18n.progress}</th>
                        </tr>
                    </thead>
                    <tbody class="bg-white dark:bg-gray-800 divide-y divide-gray-200 dark:divide-gray-700">
                        ${data.history.map(item => `
                            <tr class="hover:bg-gray-50 dark:hover:bg-gray-700/30">
                                <td class="px-4 py-3 whitespace-nowrap">
                                    <div class="flex items-center">
                                        <img src="${item.poster_url}" class="w-10 h-14 object-cover rounded shadow-sm mr-4" alt="Poster" onerror="this.src='https://placehold.co/80x120/1F2937/E5E7EB?text=NO+ART'">
                                        <div>
                                            <div class="text-sm font-bold text-gray-900 dark:text-white">${sanitizeHTML(item.title)}</div>
                                            <div class="text-xs text-gray-500 dark:text-gray-400">${sanitizeHTML(item.subtitle)}</div>
                                        </div>
                                    </div>
                                </td>
                                <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-600 dark:text-gray-300">${item.date}</td>
                                <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-600 dark:text-gray-300">${sanitizeHTML(item.player)}</td>
                                <td class="px-4 py-3 whitespace-nowrap text-sm font-mono font-medium ${item.percent_complete === 100 ? 'text-green-500' : 'text-yellow-600'}">${item.percent_complete}%</td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            </div>
        `;

        pc.innerHTML = `
            <button id="hist-prev" class="btn bg-gray-200 dark:bg-gray-700 text-xs px-3 py-1 disabled:opacity-50" ${data.pagination.current_page === 1 ? 'disabled' : ''}>Anterior</button>
            <span class="text-sm text-gray-600 dark:text-gray-400">Pág ${data.pagination.current_page} de ${data.pagination.total_pages}</span>
            <button id="hist-next" class="btn bg-gray-200 dark:bg-gray-700 text-xs px-3 py-1 disabled:opacity-50" ${data.pagination.current_page === data.pagination.total_pages ? 'disabled' : ''}>Próxima</button>
        `;

        const searchInput = document.getElementById('historySearchInput');
        document.getElementById('hist-prev').onclick = () => fetchWatchHistory(data.pagination.current_page - 1, searchInput.value);
        document.getElementById('hist-next').onclick = () => fetchWatchHistory(data.pagination.current_page + 1, searchInput.value);

    } catch (error) {
        hc.innerHTML = `<p class="text-center text-red-500 py-8">❌ ${error.message}</p>`;
    }
};

const initGlobalEventListeners = () => {
    // Busca do histórico (Debounce)
    const searchInput = document.getElementById('historySearchInput');
    if (searchInput) {
        searchInput.addEventListener('input', (e) => {
            clearTimeout(state.historySearchTimeout);
            state.historySearchTimeout = setTimeout(() => {
                fetchWatchHistory(1, sanitizeHTML(e.target.value));
            }, 500);
        });
    }

    // Cópia de chave PIX
    document.getElementById('copy-pix-code')?.addEventListener('click', () => {
        const input = document.getElementById('pix-copy-paste');
        input.select();
        document.execCommand('copy');
        showToast(state.i18n.codeCopied, 'success');
    });

    // Toggle de Privacidade (Leaderboard)
    dom.privacyToggle?.addEventListener('change', async (e) => {
        const isHidden = e.target.checked;
        try {
            await fetchAPI(state.urls.updatePrivacyUrl, 'POST', { hide: isHidden });
            showToast('Privacidade atualizada com sucesso!', 'success');
        } catch (error) {
            showToast(error.message, 'error');
            e.target.checked = !isHidden; // Reverte visualmente
        }
    });
};

// ==========================================
// INICIALIZAÇÃO PRINCIPAL (ORQUESTRADOR)
// ==========================================

document.addEventListener('DOMContentLoaded', async () => {
    initializeConfigAndDOM();

    try {
        // Obtenção Paralela dos Dados Fundamentais
        const [accountData, paymentOptions] = await Promise.all([
            fetchAPI(state.urls.getAccountDetailsUrl),
            fetchAPI(state.urls.getPaymentOptionsUrl)
        ]);

        state.currentUser = { 
            username: accountData.username, 
            is_admin: accountData.role === 'admin',
            profile_details: accountData.profile_details
        };

        // Renderizações Sequenciais
        renderStatusBanner(accountData, accountData.expiration_info);
        renderProfileBaseInfo(accountData, accountData.expiration_info);
        
        if(paymentOptions.success) {
            setupPaymentSection(paymentOptions.prices, paymentOptions.providers, paymentOptions.can_downgrade);
        }

        initContactForm(accountData.profile_details);

        // Fetch secundários e pesados não bloqueiam a UI primária
        fetchAPI(state.urls.getPaymentHistoryUrl).then(res => {
            if (res.success) renderPaymentHistory(res.payments);
        });

        fetchAPI(state.urls.getAccountDevicesUrl).then(res => {
            if (res.success) renderDeviceList(res.devices);
        });

        initTabs();
        initGlobalEventListeners();

        // Reveal Interface
        if (dom.loadingIndicator) dom.loadingIndicator.style.display = 'none';
        if (dom.container) dom.container.classList.remove('hidden');

    } catch (error) {
        if (dom.loadingIndicator) dom.loadingIndicator.style.display = 'none';
        if (dom.errorMessage) dom.errorMessage.textContent = error.message;
        if (dom.errorContainer) dom.errorContainer.classList.remove('hidden');
        showToast(error.message, 'error');
    }
});
