import { fetchAPI, showToast, createModal, copyToClipboard } from './utils.js';

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
    currentPage: 1,
    // Plano atual do utilizador e cotação de upgrade pro-rata em curso.
    currentScreens: 0,
    prorationQuote: null,
    requiresProrationForUpgrade: false,
    requestsSkip: 0,
    requestsLoaded: false
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


// --- INDIQUE E GANHE ---

async function loadReferralCard() {
    const card = document.getElementById('referral-card');
    if (!card || !state.urls.referralInfoUrl) return;

    try {
        const data = await fetchAPI(state.urls.referralInfoUrl);
        // O card só aparece se o administrador tiver o sistema ativo e houver link.
        if (!data.success || data.enabled === false || !data.link) return;

        document.getElementById('referral-link').value = data.link;

        const rewardEl = document.getElementById('referral-reward-text');
        if (data.reward_type === 'credit') {
            rewardEl.textContent = (state.i18n.referralRewardCredit || 'Ganhe R$ {credit} de crédito por cada amigo que assinar pelo seu link.')
                .replace('{credit}', Number(data.reward_credit || 0).toFixed(2));
            document.getElementById('referral-credit-box')?.classList.remove('hidden');
            document.getElementById('referral-credit').textContent = `R$ ${Number(data.current_credit || 0).toFixed(2)}`;
            // Com 4 caixas a grid de 3 colunas fica desequilibrada: escondemos a de
            // pendentes, cuja informação já está implícita (indicados - confirmados).
            document.getElementById('referral-pending-box')?.classList.add('hidden');
        } else {
            rewardEl.textContent = (state.i18n.referralRewardDays || 'Ganhe {days} dia(s) grátis por cada amigo que assinar pelo seu link.')
                .replace('{days}', data.reward_days || 0);
            document.getElementById('referral-pending').textContent = data.pending || 0;
        }

        document.getElementById('referral-total').textContent = data.total_referred || 0;
        document.getElementById('referral-confirmed').textContent = data.total_confirmed || 0;

        const list = document.getElementById('referral-list');
        if (list && Array.isArray(data.referred_users) && data.referred_users.length) {
            list.innerHTML = data.referred_users.map(u => `
                <div class="flex items-center justify-between text-xs bg-white/50 dark:bg-black/10 rounded-lg px-3 py-2">
                    <span class="font-medium text-gray-700 dark:text-gray-300">${escapeHTML(u.username || '')}</span>
                    <span class="${u.confirmed ? 'text-emerald-600 dark:text-emerald-400' : 'text-amber-500'} font-semibold">
                        ${u.confirmed ? '✓ ' + (state.i18n.referralConfirmedLabel || 'Confirmado') : '⏳ ' + (state.i18n.referralPendingLabel || 'Aguarda 1º pagamento')}
                    </span>
                </div>
            `).join('');
        }

        document.getElementById('copy-referral-link')?.addEventListener('click', async () => {
            const ok = await copyToClipboard(data.link);
            showToast(ok ? (state.i18n.referralLinkCopied || 'Link copiado!') : (state.i18n.error || 'Erro'), ok ? 'success' : 'error');
        });

        card.classList.remove('hidden');
    } catch (error) {
        // Silencioso: o programa de indicações é um extra e nunca deve estragar a
        // página de conta se a chamada falhar.
        console.warn('Não foi possível carregar o programa de indicações:', error);
    }
}

function escapeHTML(str) {
    return String(str).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

const renderProfileBaseInfo = (data, expiration) => {
    document.getElementById('user-thumb').src = data.thumb || 'https://placehold.co/96x96/1F2937/E5E7EB?text=?';
    document.getElementById('user-username').textContent = data.username;
    document.getElementById('user-email').textContent = data.email;
    document.getElementById('user-join-date').textContent = data.join_date;
    document.getElementById('user-screen-limit').textContent = data.screen_limit;
    // Guardado para detetar upgrades a meio do ciclo (pro-rata).
    state.currentScreens = parseInt(data.screen_limit, 10) || 0;
    
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

    // 🎮 Sistema de XP/Nível: reaproveita 'level_info' já calculado pelo backend
    // dentro de watch_stats (o mesmo objeto usado na página de Estatísticas).
    const levelInfo = data.watch_stats && data.watch_stats.level_info;
    const levelBadge = document.getElementById('user-level-badge');
    const levelCard = document.getElementById('user-level-card');

    if (levelInfo) {
        if (levelBadge) {
            levelBadge.textContent = levelInfo.level_icon;
            levelBadge.title = `${state.i18n.level || 'Nível'} ${levelInfo.level_number}: ${levelInfo.level_name}`;
            levelBadge.classList.remove('hidden');
        }
        if (levelCard) {
            document.getElementById('user-level-icon').textContent = levelInfo.level_icon;
            document.getElementById('user-level-number-label').textContent = `${state.i18n.level || 'Nível'} ${levelInfo.level_number}`;
            document.getElementById('user-level-name').textContent = levelInfo.level_name;
            document.getElementById('user-xp-total').textContent = levelInfo.xp.toLocaleString();
            document.getElementById('user-xp-progress-bar').style.width = `${levelInfo.progress_percent}%`;
            document.getElementById('user-xp-next-level').textContent = levelInfo.is_max_level
                ? (state.i18n.maxLevelReached || 'Nível máximo atingido! 🎉')
                : (state.i18n.xpToNextLevel || '{xp} XP para o próximo nível').replace('{xp}', levelInfo.xp_for_next_level.toLocaleString());
            levelCard.classList.remove('hidden');
        }
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


/**
 * Mostra (ou limpa) a caixa que explica o upgrade proporcional.
 * Passar 'null' remove a caixa — usado sempre que o utilizador troca de plano.
 */
function renderProrationBox(quote) {
    const existing = document.getElementById('proration-box');
    if (existing) existing.remove();
    if (!quote) return;

    const plansContainer = document.querySelector('input[name="payment-plan"]')?.closest('.space-y-3');
    if (!plansContainer) return;

    const box = document.createElement('div');
    box.id = 'proration-box';
    box.className = 'mt-4 p-4 rounded-xl bg-emerald-50 dark:bg-emerald-900/20 border border-emerald-200 dark:border-emerald-700/40';

    if (quote.is_free) {
        box.innerHTML = `
            <div class="flex items-start gap-3">
                <span class="text-2xl leading-none">🎉</span>
                <div>
                    <p class="font-bold text-emerald-800 dark:text-emerald-300 text-sm">${escapeHTML(state.i18n.upgradeFreeTitle || 'Upgrade sem custo!')}</p>
                    <p class="text-xs text-emerald-700/80 dark:text-emerald-400/80 mt-1">
                        ${escapeHTML((state.i18n.upgradeFreeDesc || 'Faltam {days} dia(s) para o vencimento — o valor proporcional é tão baixo que o upgrade é gratuito.').replace('{days}', quote.days_remaining))}
                    </p>
                </div>
            </div>`;
    } else {
        const valor = Number(quote.amount).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
        box.innerHTML = `
            <div class="flex items-start gap-3">
                <span class="text-2xl leading-none">🔼</span>
                <div class="flex-1">
                    <p class="font-bold text-emerald-800 dark:text-emerald-300 text-sm">${escapeHTML(state.i18n.upgradeNowTitle || 'Faça upgrade agora, pagando só a diferença')}</p>
                    <p class="text-xs text-emerald-700/80 dark:text-emerald-400/80 mt-1">
                        ${escapeHTML((state.i18n.upgradeNowDesc || 'Como faltam {days} dia(s) no seu plano atual, você paga apenas {price} e as telas ficam disponíveis imediatamente. O seu vencimento não muda.')
                            .replace('{days}', quote.days_remaining).replace('{price}', valor))}
                    </p>
                    ${state.requiresProrationForUpgrade ? `
                        <!-- Pro-rata obrigatório: o checkbox fica fixo (a renovação a preço
                             cheio com mais telas só é permitida perto do vencimento). -->
                        <input type="checkbox" id="proration-toggle" checked class="hidden">
                        <p class="text-xs text-emerald-700/70 dark:text-emerald-400/70 mt-3 italic">
                            ${escapeHTML(state.i18n.upgradeOnlyProration || 'A troca de plano numa renovação completa fica disponível perto do vencimento.')}
                        </p>
                    ` : `
                        <label class="flex items-center gap-2 mt-3 cursor-pointer">
                            <input type="checkbox" id="proration-toggle" checked class="h-4 w-4 rounded text-emerald-600 focus:ring-emerald-500 border-gray-300 dark:border-gray-600">
                            <span class="text-xs font-semibold text-emerald-800 dark:text-emerald-300">${escapeHTML(state.i18n.upgradeUseProration || 'Pagar apenas a diferença')}</span>
                        </label>
                    `}
                </div>
            </div>`;
    }

    plansContainer.insertAdjacentElement('afterend', box);

    // Alternar entre pagar só a diferença ou um ciclo completo.
    document.getElementById('proration-toggle')?.addEventListener('change', (e) => {
        const pixBtn = document.getElementById('initiatePixButton');
        const applyCouponBtn = document.getElementById('applyCouponBtn');
        if (e.target.checked) {
            updatePixButtonForProration(quote, pixBtn, applyCouponBtn);
        } else {
            const radio = document.querySelector('input[name="payment-plan"]:checked');
            const price = parseFloat(radio.dataset.price).toFixed(2).replace('.', ',');
            pixBtn.textContent = state.i18n.generatePixForPrice.replace('{price}', price);
            if (applyCouponBtn) applyCouponBtn.disabled = false;
        }
    });
}

/**
 * Ajusta o botão de pagamento para refletir o valor proporcional e desativa o
 * cupão — descontos não acumulam com o pro-rata (o valor já é reduzido).
 */
function updatePixButtonForProration(quote, pixBtn, applyCouponBtn) {
    if (!pixBtn) return;
    if (quote.is_free) {
        pixBtn.textContent = state.i18n.upgradeFreeButton || 'Ativar Upgrade Gratuito';
    } else {
        const valor = Number(quote.amount).toFixed(2).replace('.', ',');
        pixBtn.textContent = (state.i18n.upgradeButton || 'Pagar diferença de R$ {price}').replace('{price}', valor);
    }
    pixBtn.disabled = false;
    // O cupão CONTINUA disponível: aplica-se sobre o valor proporcional, não sobre
    // o preço cheio. Desativá-lo confundia quem tentava usar um cupão de 100% para
    // fazer upgrade — o pedido acabava a seguir o caminho da renovação normal.
    if (applyCouponBtn) applyCouponBtn.disabled = false;
}


// --- MEUS PEDIDOS (OVERSEERR) ---

const STATUS_STYLES = {
    green:  'bg-green-100 text-green-700 dark:bg-green-500/15 dark:text-green-400 ring-green-600/20',
    blue:   'bg-blue-100 text-blue-700 dark:bg-blue-500/15 dark:text-blue-400 ring-blue-600/20',
    yellow: 'bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-400 ring-amber-600/20',
    teal:   'bg-teal-100 text-teal-700 dark:bg-teal-500/15 dark:text-teal-400 ring-teal-600/20',
    red:    'bg-red-100 text-red-700 dark:bg-red-500/15 dark:text-red-400 ring-red-600/20',
    gray:   'bg-gray-100 text-gray-600 dark:bg-gray-700/40 dark:text-gray-400 ring-gray-500/20',
};

/**
 * Esqueleto de carregamento. Mostrar a "forma" do conteúdo enquanto ele chega
 * transmite progresso muito melhor do que um texto "a carregar...", sobretudo
 * porque estes pedidos dependem de uma API externa e podem demorar.
 */
function renderRequestsSkeleton(n = 3) {
    const container = document.getElementById('requests-container');
    if (!container) return;
    container.innerHTML = Array.from({ length: n }).map(() => `
        <div class="flex gap-4 p-3 rounded-xl bg-gray-50 dark:bg-gray-900/40 animate-pulse">
            <div class="w-14 h-20 rounded-lg bg-gray-200 dark:bg-gray-700 flex-shrink-0"></div>
            <div class="flex-1 py-1 space-y-2">
                <div class="h-4 w-2/3 rounded bg-gray-200 dark:bg-gray-700"></div>
                <div class="h-3 w-1/3 rounded bg-gray-200 dark:bg-gray-700"></div>
                <div class="h-5 w-24 rounded-full bg-gray-200 dark:bg-gray-700 mt-3"></div>
            </div>
        </div>
    `).join('');
}

function renderEmptyRequests(mensagem, icone = '🎬') {
    const container = document.getElementById('requests-container');
    if (!container) return;
    // Sem resultados não faz sentido mostrar "0 de 0 pedidos".
    document.getElementById('requests-count')?.classList.add('hidden');
    container.innerHTML = `
        <div class="text-center py-12 px-4">
            <div class="text-5xl mb-3 opacity-60">${icone}</div>
            <p class="text-sm text-gray-500 dark:text-gray-400">${escapeHTML(mensagem)}</p>
        </div>
    `;
}

function buildRequestCard(req) {
    const badge = STATUS_STYLES[req.status_color] || STATUS_STYLES.gray;
    const isSerie = req.type === 'tv';

    // A data vem em ISO (UTC); mostramos no formato e idioma do utilizador.
    let dataPedido = '';
    if (req.requested_at) {
        try {
            dataPedido = new Date(req.requested_at).toLocaleDateString(
                document.documentElement.lang || undefined,
                { day: '2-digit', month: 'short', year: 'numeric' }
            );
        } catch { dataPedido = ''; }
    }

    const capa = req.poster_url
        ? `<img src="${escapeHTML(req.poster_url)}" alt="" loading="lazy"
                class="w-14 h-20 object-cover rounded-lg shadow-sm bg-gray-200 dark:bg-gray-700"
                onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';">
           <div class="w-14 h-20 rounded-lg bg-gray-200 dark:bg-gray-700 items-center justify-center text-2xl" style="display:none">🎞️</div>`
        : `<div class="w-14 h-20 rounded-lg bg-gray-200 dark:bg-gray-700 flex items-center justify-center text-2xl">${isSerie ? '📺' : '🎬'}</div>`;

    return `
        <div class="group flex gap-4 p-3 rounded-xl border border-gray-200 dark:border-gray-700/60 bg-white dark:bg-gray-900/30 hover:border-purple-300 dark:hover:border-purple-700/60 hover:shadow-sm transition-all">
            <div class="flex-shrink-0 relative">${capa}</div>
            <div class="flex-1 min-w-0 flex flex-col justify-between py-0.5">
                <div>
                    <p class="font-semibold text-gray-900 dark:text-white leading-snug line-clamp-2" title="${escapeHTML(req.title)}">
                        ${escapeHTML(req.title)}
                    </p>
                    <p class="text-xs text-gray-500 dark:text-gray-400 mt-0.5 flex items-center gap-1.5">
                        <span>${isSerie ? '📺' : '🎬'}</span>
                        <span>${escapeHTML(req.year || '----')}</span>
                        ${dataPedido ? `<span class="text-gray-300 dark:text-gray-600">•</span><span>${escapeHTML(state.i18n.requestedOn || 'Pedido a')} ${escapeHTML(dataPedido)}</span>` : ''}
                    </p>
                </div>
                <div class="mt-2">
                    <span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold ring-1 ring-inset ${badge}">
                        <span class="w-1.5 h-1.5 rounded-full bg-current"></span>
                        ${escapeHTML(req.status_text)}
                    </span>
                </div>
            </div>
        </div>
    `;
}

async function fetchRequests(append = false) {
    const container = document.getElementById('requests-container');
    if (!container || !state.urls.getAccountRequestsUrl) return;

    if (!append) {
        state.requestsSkip = 0;
        renderRequestsSkeleton();
    }

    try {
        const url = `${state.urls.getAccountRequestsUrl}?filter=${encodeURIComponent(state.currentRequestFilter)}&skip=${state.requestsSkip}&limit=20`;
        const data = await fetchAPI(url);

        if (data.overseerr_disabled) {
            renderEmptyRequests(state.i18n.overseerrDisabled || 'Módulo de pedidos desativado no servidor.', '🔌');
            return;
        }
        if (!data.success) {
            renderEmptyRequests(data.message || (state.i18n.errorGeneric || 'Erro'), '⚠️');
            return;
        }

        const pedidos = data.requests || [];
        const cards = pedidos.map(buildRequestCard).join('');

        if (append) {
            document.getElementById('requests-load-more-wrapper')?.remove();
            container.insertAdjacentHTML('beforeend', cards);
        } else if (pedidos.length === 0) {
            renderEmptyRequests(state.i18n.noRequestsFound || 'Nenhum pedido condiz com este filtro.');
            return;
        } else {
            container.innerHTML = cards;
        }

        state.requestsSkip += pedidos.length;

        // Contador "X de Y pedidos": dá contexto de quantos ainda faltam ver.
        const contador = document.getElementById('requests-count');
        if (contador) {
            const total = data.pagination?.total ?? state.requestsSkip;
            if (total > 0) {
                contador.textContent = (state.i18n.requestsCount || '{shown} de {total} pedido(s)')
                    .replace('{shown}', state.requestsSkip).replace('{total}', total);
                contador.classList.remove('hidden');
            } else {
                contador.classList.add('hidden');
            }
        }

        // Botão "ver mais": só aparece quando há mesmo mais para mostrar.
        if (data.pagination?.has_more) {
            container.insertAdjacentHTML('beforeend', `
                <div id="requests-load-more-wrapper" class="pt-2">
                    <button id="requests-load-more" class="w-full py-2.5 rounded-xl text-sm font-semibold text-purple-600 dark:text-purple-400 border border-dashed border-purple-300 dark:border-purple-700/60 hover:bg-purple-50 dark:hover:bg-purple-900/20 transition-colors">
                        ${escapeHTML(state.i18n.loadMore || 'Ver mais pedidos')}
                    </button>
                </div>
            `);
            document.getElementById('requests-load-more')?.addEventListener('click', (e) => {
                e.target.disabled = true;
                e.target.textContent = state.i18n.loadingRequests || 'A carregar...';
                fetchRequests(true);
            });
        }
    } catch (error) {
        renderEmptyRequests(`${state.i18n.errorGeneric || 'Erro'}: ${error.message}`, '⚠️');
    }
}

function initRequestsTab() {
    const filtros = document.getElementById('request-filters');
    if (!filtros) return;

    filtros.addEventListener('click', (e) => {
        const btn = e.target.closest('.filter-btn');
        if (!btn || btn.classList.contains('active')) return;

        filtros.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        state.currentRequestFilter = btn.dataset.filter || 'all';
        fetchRequests(false);
    });
}

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
        radio.addEventListener('change', async () => {
            const price = parseFloat(radio.dataset.price).toFixed(2).replace('.', ',');
            pixBtn.textContent = state.i18n.generatePixForPrice.replace('{price}', price);
            pixBtn.disabled = false;
            applyCouponBtn.disabled = false;

            // Reset Cupão
            statusDiv.innerHTML = '';
            couponInput.value = '';
            state.validatedCouponCode = null;
            state.prorationQuote = null;
            renderProrationBox(null);

            // 🔼 UPGRADE PRO-RATA: se o utilizador escolheu MAIS telas do que tem
            // hoje e a subscrição ainda está a meio do ciclo, oferecemos pagar só a
            // diferença proporcional em vez de um ciclo completo.
            const selected = parseInt(radio.value, 10);
            if (!state.urls.upgradeQuoteUrl || !selected || selected <= state.currentScreens) return;

            try {
                const quote = await fetchAPI(state.urls.upgradeQuoteUrl, 'POST', { screens: selected });
                if (quote && quote.eligible) {
                    state.prorationQuote = quote;
                    renderProrationBox(quote);
                    updatePixButtonForProration(quote, pixBtn, applyCouponBtn);
                }
            } catch (error) {
                // O upgrade proporcional é um extra: se a consulta falhar, o
                // utilizador continua a poder renovar normalmente pelo preço cheio.
                console.warn('Não foi possível obter a cotação de upgrade:', error);
            }
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
        if (!plan) return;

        // Só envia 'proration' se houver cotação válida E o utilizador não tiver
        // desmarcado a opção de pagar apenas a diferença.
        const usarProrata = !!state.prorationQuote &&
            (document.getElementById('proration-toggle')?.checked !== false);

        handlePixGenerationRequest({
            screens: plan.value,
            coupon_code: state.validatedCouponCode,
            proration: usarProrata
        }, providers);
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
            // Mesma correção: os provedores chegam em MAIÚSCULAS (ver .toUpperCase acima).
            const colors = p === 'MERCADOPAGO' ? 'bg-blue-600 hover:bg-blue-500' : p === 'GATES2B' ? 'bg-purple-600 hover:bg-purple-500' : 'bg-green-600 hover:bg-green-500';
            const name = p === 'MERCADOPAGO' ? state.i18n.payWithMp : p === 'GATES2B' ? (state.i18n.payWithGates2b || 'Pagar com Gates2b') : state.i18n.payWithEfi;
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
            if (result.free_renewal || result.free_upgrade) {
                // Upgrade abaixo do mínimo cobrável: aplicado de imediato, sem PIX.
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
            const statusUrl = state.urls.paymentStatusUrl
                ? state.urls.paymentStatusUrl.replace('__TXID__', txid)
                : `/api/payments/status/${txid}`; // fallback de segurança, caso a URL não tenha sido injetada
            const result = await fetchAPI(statusUrl);
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

    const activateTab = (tabName) => {
        const btn = dom.tabContainer.querySelector(`[data-tab="${tabName}"]`);
        if (!btn) return;
        dom.tabContainer.querySelectorAll('.tab-button').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        dom.contentContainer.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        document.getElementById(`tab-${tabName}`)?.classList.add('active');
        if (tabName === 'history') fetchWatchHistory();
        // Carrega os pedidos só ao abrir a aba: são chamadas a uma API externa,
        // não faz sentido pagá-las se o utilizador nunca lá for.
        if (tabName === 'requests' && !state.requestsLoaded) {
            state.requestsLoaded = true;
            fetchRequests(false);
        }
    };

    // 🐛 CORREÇÃO: o listener de clique precisa de estar registado ANTES de
    // qualquer troca de aba automática ser disparada (ver mais abaixo), senão
    // a troca de aba para admins nunca tem efeito nenhum (o clique acontecia
    // antes do listener existir).
    dom.tabContainer.addEventListener('click', (e) => {
        const btn = e.target.closest('.tab-button');
        if (!btn || !btn.dataset.tab) return;
        activateTab(btn.dataset.tab);
    });

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
        // Se a aba default (Overview) ficou oculta, muda para Histórico
        activateTab('history');
    }
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
            <button id="hist-prev" class="btn bg-gray-200 dark:bg-gray-700 text-xs px-3 py-1 disabled:opacity-50" ${data.pagination.current_page === 1 ? 'disabled' : ''}>${state.i18n.previous || 'Anterior'}</button>
            <span class="text-sm text-gray-600 dark:text-gray-400">${(state.i18n.pageOf || 'Página {currentPage} / {totalPages}').replace('{currentPage}', data.pagination.current_page).replace('{totalPages}', data.pagination.total_pages)}</span>
            <button id="hist-next" class="btn bg-gray-200 dark:bg-gray-700 text-xs px-3 py-1 disabled:opacity-50" ${data.pagination.current_page === data.pagination.total_pages ? 'disabled' : ''}>${state.i18n.next || 'Próxima'}</button>
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
    document.getElementById('copy-pix-code')?.addEventListener('click', async () => {
        const input = document.getElementById('pix-copy-paste');
        const success = await copyToClipboard(input.value);
        if (success) {
            showToast(state.i18n.codeCopied, 'success');
        } else {
            showToast(state.i18n.error || 'Erro', 'error');
        }
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
        loadReferralCard();
        
        if(paymentOptions.success) {
            state.requiresProrationForUpgrade = !!paymentOptions.requires_proration_for_upgrade;
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
        initRequestsTab();
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
