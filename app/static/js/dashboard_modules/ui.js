// Funções para renderizar e atualizar elementos do DOM
import { dom, state } from './config.js';
import { getChartColors, formatCurrency, formatTime, formatTimeAgo, formatDateTime, escapeHtml, getReasonText } from './formatters.js';
import { getUsersForSelection } from './api.js';
import {
    syncStreamTimers,
    removeStreamTimer,
    clearStreamTimers,
    streamMediaSignature
} from './stream_timers.js';
import { updateSendButtonText } from './handlers.js';

// ==========================================
// CONSTANTES E ÍCONES SVG
// ==========================================

const ICONS = {
    activeStreams: '<svg class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" /></svg>',
    totalUsers: '<svg class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" /></svg>',
    revenue: '<svg class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v.01" /></svg>',
    renewals: '<svg class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" /></svg>',
    search: '<svg class="h-4 w-4 text-gray-400" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M8 4a4 4 0 100 8 4 4 0 000-8zM2 8a6 6 0 1110.89 3.476l4.817 4.817a1 1 0 01-1.414 1.414l-4.816-4.816A6 6 0 012 8z" clip-rule="evenodd" /></svg>',
    delete: '<svg class="w-5 h-5" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z" clip-rule="evenodd" /></svg>'
};

const SYSTEM_STATUS_MAP = {
    ONLINE: { text: 'Online', color: 'bg-green-500', icon: '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg>' },
    RUNNING: { text: 'A correr', color: 'bg-green-500', icon: '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg>' },
    OFFLINE: { text: 'Offline', color: 'bg-red-500', icon: '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg>' },
    STOPPED: { text: 'Parado', color: 'bg-red-500', icon: '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg>' },
    DISABLED: { text: 'Desativado', color: 'bg-gray-500', icon: '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636"></path></svg>' }
};

// ==========================================
// CARDS DE RESUMO (DASHBOARD)
// ==========================================

function _createSummaryCard(id, icon, label, value, colorClass) {
    return `
        <div id="${id}" class="p-4 rounded-xl flex items-center gap-4 ${colorClass} text-white shadow-lg">
            <div class="p-3 bg-white/20 rounded-lg">${icon}</div>
            <div class="min-w-0">
                <p class="text-sm font-medium opacity-80">${label}</p>
                <p data-card-value class="text-2xl font-bold truncate">${value}</p>
            </div>
        </div>
    `;
}

/**
 * Devolve os quatro cartões na ordem em que são desenhados.
 */
function _summaryCardValues(summary) {
    const { i18n } = state;
    return [
        { id: 'active-streams-card', icon: ICONS.activeStreams, label: i18n.activeStreams, value: summary.active_streams, color: 'bg-blue-500' },
        { id: 'total-users-card', icon: ICONS.totalUsers, label: i18n.totalUsers, value: summary.total_users, color: 'bg-purple-500' },
        { id: 'monthly-revenue-card', icon: ICONS.revenue, label: i18n.monthlyRevenue, value: formatCurrency(summary.monthly_revenue), color: 'bg-green-500' },
        { id: 'upcoming-renewals-card', icon: ICONS.renewals, label: i18n.upcomingRenewals, value: summary.upcoming_renewals, color: 'bg-yellow-500' }
    ];
}

export function renderSummaryCards(summary) {
    if (!dom.summaryCardsContainer || !summary) return;

    const cards = _summaryCardValues(summary);

    // Esta função é chamada pelo tempo real sempre que o servidor manda um
    // resumo novo. Refazer o innerHTML de todos os cartões de cada vez destruía
    // e recriava os nós do DOM (e cortava qualquer seleção de texto ou
    // transição em curso) só para mudar quatro números: quando os cartões já
    // existem, escreve-se apenas o valor que mudou.
    const existing = cards.map(card => document.getElementById(card.id)?.querySelector('[data-card-value]'));

    if (existing.every(Boolean)) {
        cards.forEach((card, i) => {
            const text = String(card.value);
            if (existing[i].textContent !== text) existing[i].textContent = text;
        });
        return;
    }

    dom.summaryCardsContainer.innerHTML = cards
        .map(card => _createSummaryCard(card.id, card.icon, card.label, card.value, card.color))
        .join('');
}

/**
 * Esqueleto dos cartões de resumo, mostrado enquanto o pedido não chega. Mantém
 * a altura final da grelha para a página não "saltar" quando os dados entram.
 */
export function renderSummaryCardsSkeleton() {
    if (!dom.summaryCardsContainer || dom.summaryCardsContainer.children.length) return;

    dom.summaryCardsContainer.innerHTML = Array.from({ length: 4 }, () => `
        <div class="p-4 rounded-xl flex items-center gap-4 bg-gray-200 dark:bg-gray-800 animate-pulse">
            <div class="p-3 rounded-lg bg-gray-300 dark:bg-gray-700 w-12 h-12"></div>
            <div class="space-y-2 flex-1">
                <div class="h-3 w-24 rounded bg-gray-300 dark:bg-gray-700"></div>
                <div class="h-6 w-16 rounded bg-gray-300 dark:bg-gray-700"></div>
            </div>
        </div>
    `).join('');
}

// ==========================================
// GRÁFICOS (CHART.JS)
// ==========================================

export function renderCharts(summary) {
    const colors = getChartColors();
    const { i18n } = state;

    if (dom.revenueCanvas) {
        const dailyData = summary.daily_revenue || {};
        const daysInMonth = new Date(new Date().getFullYear(), new Date().getMonth() + 1, 0).getDate();
        const labels = Array.from({ length: daysInMonth }, (_, i) => i + 1);
        const data = labels.map(day => dailyData[day] || 0);

        if (state.monthlyRevenueChart) {
            state.monthlyRevenueChart.data.labels = labels;
            state.monthlyRevenueChart.data.datasets[0].data = data;
            state.monthlyRevenueChart.update('none'); 
        } else {
             state.monthlyRevenueChart = new Chart(dom.revenueCanvas.getContext('2d'), {
                type: 'bar',
                data: {
                    labels: labels,
                    datasets: [{
                        label: i18n.revenueLabel, data: data,
                        backgroundColor: colors.barColor, borderColor: colors.barBorderColor,
                        borderWidth: 1, borderRadius: 4,
                    }]
                },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    plugins: { legend: { display: false } },
                    scales: {
                        y: { beginAtZero: true, ticks: { color: colors.textColor, callback: val => formatCurrency(val) }, grid: { color: colors.gridColor } },
                        x: { ticks: { color: colors.textColor }, grid: { display: false } }
                    }
                }
            });
        }
    }

    if (dom.userStatusCanvas) {
        const data = [summary.active_users, summary.blocked_users];
        if (state.userStatusChart) {
            state.userStatusChart.data.datasets[0].data = data;
            state.userStatusChart.update('none'); 
        } else {
             state.userStatusChart = new Chart(dom.userStatusCanvas.getContext('2d'), {
                type: 'doughnut',
                data: {
                    labels: [i18n.activeUsersLabel, i18n.blockedUsersLabel],
                    datasets: [{
                        data: data, backgroundColor: colors.doughnutColors,
                        borderColor: colors.tooltipBg, borderWidth: 4,
                    }]
                },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    plugins: { legend: { position: 'bottom', labels: { color: colors.textColor, font: { size: 14 } } } }
                }
            });
        }
    }
}

// ==========================================
// SAÚDE DO SISTEMA
// ==========================================

/**
 * Esqueleto da grelha de saúde. Cada verificação é uma chamada de rede a um
 * serviço externo, por isso esta secção costuma ser das últimas a responder.
 */
export function renderSystemHealthSkeleton() {
    if (!dom.systemHealthContainer || dom.systemHealthContainer.children.length) return;

    dom.systemHealthContainer.innerHTML = Array.from({ length: 6 }, () => `
        <div class="flex items-center p-3 bg-gray-100 dark:bg-gray-900/50 rounded-lg animate-pulse">
            <div class="flex-shrink-0 w-8 h-8 rounded-full bg-gray-300 dark:bg-gray-700"></div>
            <div class="ml-3 space-y-2 flex-1">
                <div class="h-3 w-20 rounded bg-gray-300 dark:bg-gray-700"></div>
                <div class="h-2 w-12 rounded bg-gray-300 dark:bg-gray-700"></div>
            </div>
        </div>
    `).join('');
}

export function renderSystemHealth(health) {
    if (!dom.systemHealthContainer) return;
    const { i18n } = state;
    const serviceMap = {
        plex: i18n.plexServer, tautulli: i18n.tautulli, efi: i18n.paymentEfi,
        mercado_pago: i18n.paymentMp, gates2b: i18n.paymentGates2b, scheduler: i18n.scheduler
    };

    dom.systemHealthContainer.innerHTML = Object.entries(health).map(([key, value]) => {
        const label = serviceMap[key] || key;
        const status = SYSTEM_STATUS_MAP[value.status] || SYSTEM_STATUS_MAP['OFFLINE'];
        
        return `
            <div class="flex items-center p-3 bg-gray-100 dark:bg-gray-900/50 rounded-lg" title="${value.message}">
                <div class="flex-shrink-0 w-8 h-8 rounded-full ${status.color} flex items-center justify-center text-white">
                    ${status.icon}
                </div>
                <div class="ml-3">
                    <p class="text-sm font-semibold text-gray-800 dark:text-gray-200">${label}</p>
                    <p class="text-xs text-gray-500 dark:text-gray-400">${status.text}</p>
                </div>
            </div>
        `;
    }).join('');
}

// ==========================================
// STREAMS EM TEMPO REAL (SMART SYNC)
// ==========================================

function _getStreamStateConfig(streamState) {
    if (streamState === 'paused') {
        return { color: 'text-yellow-500', icon: '<svg class="w-5 h-5" viewBox="0 0 24 24" fill="currentColor"><path d="M9 8h2v8H9zm4 0h2v8h-2z"></path></svg>' };
    }
    if (streamState === 'buffering') {
        return { color: 'text-blue-500', icon: '<svg class="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>' };
    }
    return { color: 'text-green-500', icon: '<svg class="w-5 h-5" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"></path></svg>' };
}

/**
 * Constrói os textos técnicos do stream (transcode, vídeo, áudio) a partir dos
 * detalhes devolvidos pela API. É usado tanto ao criar o cartão como ao
 * atualizá-lo, para que os dois caminhos nunca fiquem com formatos diferentes.
 */
function _getStreamDetailTexts(streamDetails) {
    const sd = streamDetails || {};

    let streamText = sd.is_transcoding ? 'Transcode' : 'Direct Play';
    if (sd.is_transcoding && typeof sd.transcode_progress === 'number') {
        streamText += ` (${parseFloat(sd.transcode_progress).toFixed(1)}%)`;
    }

    return {
        stream: `${streamText} (${sd.container})`,
        video: `${sd.video_decision} (${sd.video_codec} ${sd.video_resolution})`,
        audio: `${sd.audio_decision} (${sd.audio_codec})`
    };
}

/**
 * Linha de detalhe do cartão. O `truncate` fica no `<p>` (elemento de bloco)
 * porque num `<span>` em linha o corte com reticências não tem qualquer efeito
 * — era assim que nomes de dispositivos e codecs longos transbordavam o cartão.
 */
function _streamDetailLine(sessionKey, field, label, text) {
    return `<p id="${field}-tooltip-${sessionKey}" class="truncate" title="${escapeHtml(text)}"><strong>${label}:</strong> <span id="${field}-text-${sessionKey}">${escapeHtml(text)}</span></p>`;
}

function _updateStreamDetailLine(sessionKey, field, text) {
    const textEl = document.getElementById(`${field}-text-${sessionKey}`);
    if (!textEl || textEl.textContent === text) return;

    textEl.textContent = text;
    const tooltipEl = document.getElementById(`${field}-tooltip-${sessionKey}`);
    if (tooltipEl) tooltipEl.title = text;
}

function _getStreamCardInnerHtml(s) {
    const stConfig = _getStreamStateConfig(s.state);

    // Força o ícone do chromecast se a palavra existir na plataforma ou no nome do player
    let platformClass = (s.platform || 'default').toLowerCase().replace(/[^a-z0-9-]/g, '');
    const rawPlatform = (s.platform || '').toLowerCase();
    const rawPlayer = (s.player || '').toLowerCase();
    if (rawPlatform.includes('chromecast') || rawPlayer.includes('chromecast')) {
        platformClass = 'chromecast';
    }

    const texts = _getStreamDetailTexts(s.stream_details);

    const placeholderImg = "https://placehold.co/150x225/1F2937/E5E7EB?text=Sem+Capa";
    const avatarPlaceholder = "https://placehold.co/24x24/1F2937/E5E7EB?text=U";

    return `
        <div class="w-24 sm:w-28 flex-shrink-0">
            <img src="${escapeHtml(s.thumb_url || placeholderImg)}" onerror="this.src='${placeholderImg}'" class="w-full h-auto aspect-[2/3] object-cover rounded-md shadow-sm" alt="Poster">
        </div>
        <div class="flex-1 min-w-0 space-y-2">
            <div class="flex justify-between items-start gap-2">
                <div class="flex-1 min-w-0">
                    <h4 class="font-bold text-base sm:text-lg text-gray-900 dark:text-white truncate" title="${escapeHtml(s.title)}">${escapeHtml(s.title)}</h4>
                    <p class="text-xs sm:text-sm text-gray-500 dark:text-gray-400 truncate" title="${escapeHtml(s.subtitle)}">${escapeHtml(s.subtitle)}</p>
                </div>
                <div class="platform-icon platform-${platformClass} flex-shrink-0" title="${escapeHtml(s.platform)}"></div>
            </div>

            <div class="space-y-1 text-xs text-gray-500 dark:text-gray-400">
                ${_streamDetailLine(s.session_key, 'player', 'Dispositivo', s.player)}
                ${_streamDetailLine(s.session_key, 'stream', 'Stream', texts.stream)}
                ${_streamDetailLine(s.session_key, 'video', 'Video', texts.video)}
                ${_streamDetailLine(s.session_key, 'audio', 'Audio', texts.audio)}
            </div>

            <div class="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-300 pt-1">
                <img src="${escapeHtml(s.user_thumb || avatarPlaceholder)}" onerror="this.src='${avatarPlaceholder}'" class="w-6 h-6 rounded-full shadow-sm flex-shrink-0">
                <span class="font-semibold truncate min-w-0" title="${escapeHtml(s.user)}">${escapeHtml(s.user)}</span>
                <div class="ml-auto flex items-center gap-1 flex-shrink-0">
                    <span id="time-${s.session_key}" class="text-xs font-mono whitespace-nowrap">${formatTime(s.view_offset)}/${formatTime(s.duration)}</span>
                    <span id="state-icon-${s.session_key}" class="${stConfig.color}">${stConfig.icon}</span>
                </div>
            </div>
            <div class="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-1.5 overflow-hidden">
                <div id="progress-${s.session_key}" class="bg-yellow-400 h-1.5 rounded-full transition-[width] duration-200 ease-linear" style="width: ${s.progress || 0}%"></div>
            </div>
        </div>
    `;
}

/**
 * Atualiza um cartão já existente sem refazer o HTML todo: só o estado visual
 * (play/pausa), a percentagem do transcoder e os detalhes de vídeo/áudio, que
 * são os únicos campos que mudam a meio de uma reprodução.
 */
function _updateStreamCardDetails(s) {
    const stConfig = _getStreamStateConfig(s.state);
    const iconSpan = document.getElementById(`state-icon-${s.session_key}`);
    if (iconSpan) {
        iconSpan.className = stConfig.color;
        iconSpan.innerHTML = stConfig.icon;
    }

    const texts = _getStreamDetailTexts(s.stream_details);
    _updateStreamDetailLine(s.session_key, 'player', s.player);
    _updateStreamDetailLine(s.session_key, 'stream', texts.stream);
    _updateStreamDetailLine(s.session_key, 'video', texts.video);
    _updateStreamDetailLine(s.session_key, 'audio', texts.audio);
}

/**
 * Estado de espera da secção de sessões: a página já está montada e o pedido às
 * sessões ainda vai a caminho do Plex. Sem isto, a secção ficava simplesmente
 * escondida e não havia forma de distinguir "ainda a carregar" de "ninguém está
 * a ver nada".
 */
export function renderActiveStreamsLoading() {
    if (!dom.activeStreamsSection || !dom.activeStreamsContainer) return;
    // Se já houver cartões (por exemplo, o tempo real chegou primeiro), não os
    // substitui por um esqueleto.
    if (dom.activeStreamsContainer.querySelector('.stream-card')) return;

    dom.activeStreamsSection.classList.remove('hidden');
    dom.activeStreamsContainer.innerHTML = Array.from({ length: 2 }, () => `
        <div class="stream-skeleton bg-white dark:bg-gray-800 p-3 sm:p-4 rounded-xl shadow-md flex items-start gap-3 sm:gap-4 animate-pulse">
            <div class="w-24 sm:w-28 flex-shrink-0 aspect-[2/3] rounded-md bg-gray-200 dark:bg-gray-700"></div>
            <div class="flex-1 min-w-0 space-y-3 py-1">
                <div class="h-4 w-3/4 rounded bg-gray-200 dark:bg-gray-700"></div>
                <div class="h-3 w-1/2 rounded bg-gray-200 dark:bg-gray-700"></div>
                <div class="h-3 w-2/3 rounded bg-gray-200 dark:bg-gray-700"></div>
                <div class="h-3 w-1/3 rounded bg-gray-200 dark:bg-gray-700"></div>
                <div class="h-1.5 w-full rounded-full bg-gray-200 dark:bg-gray-700"></div>
            </div>
        </div>
    `).join('');
}

/**
 * O pedido das sessões falhou (Plex fora do ar, timeout). Distinguir isto de
 * "nada a tocar" evita que o administrador leia um servidor inacessível como um
 * servidor vazio.
 */
export function renderActiveStreamsUnavailable() {
    if (!dom.activeStreamsSection || !dom.activeStreamsContainer) return;
    if (dom.activeStreamsContainer.querySelector('.stream-card')) return;

    dom.activeStreamsSection.classList.remove('hidden');
    delete dom.activeStreamsContainer.dataset.placeholder;
    dom.activeStreamsContainer.innerHTML = `
        <p class="stream-placeholder col-span-full text-center text-sm text-gray-500 dark:text-gray-400 py-6">
            ${escapeHtml(state.i18n.streamsUnavailable || 'Não foi possível obter as sessões ativas.')}
        </p>`;
}

function _renderNoActiveStreams() {
    const { i18n } = state;
    dom.activeStreamsSection.classList.remove('hidden');

    // Já está no ecrã: reescrever o HTML a cada atualização só reiniciava a
    // animação de entrada sem mudar nada.
    if (dom.activeStreamsContainer.dataset.placeholder === 'empty') return;
    dom.activeStreamsContainer.dataset.placeholder = 'empty';

    dom.activeStreamsContainer.innerHTML = `
        <p class="stream-placeholder col-span-full text-center text-sm text-gray-500 dark:text-gray-400 py-6">
            ${escapeHtml(i18n.noActiveStreams || 'Nenhuma reprodução ativa no momento.')}
        </p>`;
}

export function renderActiveStreamsDashboard(sessions) {
    if (!dom.activeStreamsSection || !dom.activeStreamsContainer) return;

    const activeSessions = Array.isArray(sessions) ? sessions : [];
    const newSessionKeys = new Set(activeSessions.map(s => s.session_key));

    // Limpa streams que já pararam
    dom.activeStreamsContainer.querySelectorAll('.stream-card').forEach(card => {
        const key = card.dataset.sessionKey;
        if (!newSessionKeys.has(key)) {
            card.remove();
            removeStreamTimer(key);
        }
    });

    if (activeSessions.length === 0) {
        _renderNoActiveStreams();
        clearStreamTimers();
        return;
    }

    dom.activeStreamsSection.classList.remove('hidden');

    // Sai do estado de espera (esqueleto ou mensagem de "nada a tocar") antes de
    // acrescentar os cartões reais. O seletor tem de ser específico: os próprios
    // cartões usam <p> nas linhas de detalhe.
    delete dom.activeStreamsContainer.dataset.placeholder;
    dom.activeStreamsContainer.querySelectorAll('.stream-skeleton, .stream-placeholder').forEach(el => el.remove());

    activeSessions.forEach(s => {
        let card = dom.activeStreamsContainer.querySelector(`[data-session-key="${s.session_key}"]`);
        const signature = streamMediaSignature(s);

        if (!card) {
            card = document.createElement('div');
            card.dataset.sessionKey = s.session_key;
            // `min-w-0` é essencial: sem ele o cartão é uma célula de grelha com
            // largura mínima automática e um título sem espaços esticava a coluna
            // para fora do ecrã em vez de ser cortado.
            card.className = 'stream-card bg-white dark:bg-gray-800 p-3 sm:p-4 rounded-xl shadow-md flex items-start gap-3 sm:gap-4 min-w-0 overflow-hidden';
            dom.activeStreamsContainer.appendChild(card);
        }

        if (card.dataset.mediaSignature !== signature) {
            // Cartão novo, ou a mesma sessão passou a reproduzir outro conteúdo
            // (o episódio seguinte): reconstrói para não ficar com dados antigos.
            card.dataset.mediaSignature = signature;
            card.innerHTML = _getStreamCardInnerHtml(s);
        } else {
            _updateStreamCardDetails(s);
        }
    });

    // O relógio de reprodução corre no seu próprio módulo, com sincronização
    // suave em relação ao Plex (ver stream_timers.js).
    syncStreamTimers(activeSessions);
}

// ==========================================
// LOGS DE AUDITORIA (HISTÓRICO)
// ==========================================

function updateLogTimestamps() {
    document.querySelectorAll('.log-time-ago').forEach(el => {
        const timestamp = el.dataset.timestamp;
        if (timestamp) el.textContent = formatTimeAgo(new Date(timestamp + 'Z'));
    });
}

function _createTerminationLogRow(log) {
    const endedAt = new Date(log.timestamp + 'Z');
    const isBlocked = log.reason.startsWith('blocked');
    const icon = isBlocked 
        ? `<svg class="w-5 h-5 text-red-500" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10 1a4.5 4.5 0 00-4.5 4.5V9H5a2 2 0 00-2 2v6a2 2 0 002 2h10a2 2 0 002-2v-6a2 2 0 00-2-2h-.5V5.5A4.5 4.5 0 0010 1zm-2.5 8V5.5a2.5 2.5 0 115 0V9h-5z" clip-rule="evenodd" /></svg>`
        : `<svg class="w-5 h-5 text-orange-500" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M4.25 2A2.25 2.25 0 002 4.25v11.5A2.25 2.25 0 004.25 18h11.5A2.25 2.25 0 0018 15.75V4.25A2.25 2.25 0 0015.75 2H4.25zM6.5 6a.75.75 0 000 1.5h7a.75.75 0 000-1.5h-7zM6 10.25a.75.75 0 01.75-.75h7a.75.75 0 010 1.5h-7A.75.75 0 016 10.25zM7.25 14a.75.75 0 000 1.5h3a.75.75 0 000-1.5h-3z" clip-rule="evenodd" /></svg>`;

    const clockIcon = `<svg class="w-3.5 h-3.5 flex-shrink-0" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"></circle><path stroke-linecap="round" stroke-linejoin="round" d="M12 7v5l3 2"></path></svg>`;

    // O que estava a assistir é truncado (títulos de séries/filmes são longos e,
    // no telemóvel, empurravam a hora para fora do cartão); a data e a hora do
    // encerramento passam para uma linha própria, que nunca é cortada.
    const media = [log.media_title, log.platform].filter(Boolean).map(escapeHtml);
    const mediaLine = media.length === 2 ? `${media[0]} <span class="text-gray-400 dark:text-gray-500">·</span> ${media[1]}` : (media[0] || '');
    const mediaTooltip = escapeHtml([log.media_title, log.platform].filter(Boolean).join(' · '));

    return `
    <div class="flex items-start justify-between gap-2 p-3 rounded-lg bg-gray-50 dark:bg-gray-700/50 animate-fade-in group" data-log-id="${log.id}">
        <div class="flex items-start gap-3 min-w-0 flex-1">
            <span class="p-2 ${isBlocked ? 'bg-red-100 dark:bg-red-900/50' : 'bg-orange-100 dark:bg-orange-900/50'} rounded-full flex-shrink-0">${icon}</span>
            <div class="min-w-0 flex-1">
                <p class="text-sm font-semibold text-gray-800 dark:text-gray-200 break-words">
                    <strong>${escapeHtml(log.username)}</strong> <span class="text-gray-400 dark:text-gray-500">—</span> ${escapeHtml(getReasonText(log.reason))}
                </p>
                <p class="text-xs text-gray-500 dark:text-gray-400 truncate" title="${mediaTooltip}">
                   ${mediaLine}
                </p>
                <p class="mt-1.5 flex flex-wrap items-center gap-x-1.5 text-[11px] font-medium text-gray-500 dark:text-gray-400">
                   ${clockIcon}
                   <span class="whitespace-nowrap">${escapeHtml(formatDateTime(endedAt))}</span>
                   <span class="text-gray-400 dark:text-gray-500">·</span>
                   <span class="log-time-ago whitespace-nowrap" data-timestamp="${log.timestamp}">${escapeHtml(formatTimeAgo(endedAt))}</span>
                </p>
            </div>
        </div>
        <button data-action="delete" title="Apagar Log" class="delete-log-btn p-1 text-gray-400 hover:text-red-500 transition flex-shrink-0 opacity-100 sm:opacity-0 sm:group-hover:opacity-100 focus-visible:opacity-100">
            ${ICONS.delete}
        </button>
    </div>`;
}

export function renderTerminationLogs(logs) {
    if (!dom.auditLogContainer) return;
    const { i18n } = state;
    
    if (!logs || logs.length === 0) {
        dom.auditLogContainer.innerHTML = `<p class="text-gray-500 dark:text-gray-400 p-4 text-center">${i18n.noTerminatedSessions}</p>`;
        if (dom.clearAllLogsBtn) dom.clearAllLogsBtn.disabled = true;
        return;
    }
    
    dom.auditLogContainer.innerHTML = logs.map(log => _createTerminationLogRow(log)).join('');
    if (dom.clearAllLogsBtn) dom.clearAllLogsBtn.disabled = false;

    if (state.timeAgoInterval) clearInterval(state.timeAgoInterval);
    state.timeAgoInterval = setInterval(updateLogTimestamps, 30000);
}

export function prependTerminationLog(log) {
    if (!dom.auditLogContainer) return;
    
    const placeholder = dom.auditLogContainer.querySelector('p');
    if (placeholder) placeholder.remove();
    
    dom.auditLogContainer.insertAdjacentHTML('afterbegin', _createTerminationLogRow(log));
    
    while (dom.auditLogContainer.children.length > 20) {
        dom.auditLogContainer.lastElementChild.remove();
    }
    if (dom.clearAllLogsBtn) dom.clearAllLogsBtn.disabled = false;
}

// ==========================================
// MODAL DE SELEÇÃO DE UTILIZADORES (BULK)
// ==========================================

export function updateSpecificUserLabel(count) {
    const { i18n } = state;
    const countSpan = document.getElementById('target-specific-count');
    if (countSpan) countSpan.textContent = (count > 0) ? ` (${count} ${i18n.selected})` : '';
}

function _getModalHtmlParts(i18n) {
    const searchId = 'bulk-notify-search-input';
    const listId = 'bulk-notify-selection-list';
    
    const body = `
        <div class="relative mb-3">
            <input type="search" id="${searchId}" placeholder="${i18n.searchUsers}" class="w-full p-2 pl-8 text-sm rounded-lg border bg-gray-50 border-gray-300 text-gray-900 focus:ring-yellow-500 focus:border-yellow-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white">
            <div class="absolute inset-y-0 left-0 pl-2 flex items-center pointer-events-none">${ICONS.search}</div>
        </div>
        <div id="${listId}" class="overflow-y-auto bg-gray-100 dark:bg-gray-900/50 p-2 rounded-lg border border-gray-300 dark:border-gray-600" style="max-height: 40vh;">
            <p class="text-gray-400 text-sm p-2">${i18n.loadingUsers}</p>
        </div>
        <div class="flex justify-between items-center text-xs mt-2 px-1">
            <button type="button" id="bulk-notify-select-all" class="text-blue-500 hover:underline">${i18n.selectAll}</button>
            <span id="bulk-notify-selected-count">0 ${i18n.selected}</span>
            <button type="button" id="bulk-notify-deselect-all" class="text-blue-500 hover:underline">${i18n.deselectAll}</button>
        </div>
    `;
    const footer = `
        <button id="bulk-notify-confirm" class="btn bg-yellow-500 text-white">${i18n.confirmSelection}</button>
        <button id="bulk-notify-cancel" class="btn bg-gray-200 dark:bg-gray-600">${i18n.cancel}</button>
    `;
    return { body, footer };
}

export function openUserSelectionModal() {
    const { i18n } = state;
    const modal = dom.userSelectionModal;
    if (!modal) return;

    const modalBody = modal.querySelector('.modal-body');
    const modalFooter = modal.querySelector('.modal-footer');
    if (!modalBody || !modalFooter) return;

    const parts = _getModalHtmlParts(i18n);
    modal.querySelector('.modal-title').textContent = i18n.selectUsers;
    modalBody.innerHTML = parts.body;
    modalFooter.innerHTML = parts.footer;

    const tempSelectedIds = new Set(state.selectedUserIds);
    
    const $ = id => modal.querySelector(`#${id}`);
    const userList = $('bulk-notify-selection-list');
    const countSpan = $('bulk-notify-selected-count');

    const updateModalCount = () => countSpan && (countSpan.textContent = `${tempSelectedIds.size} ${i18n.selected}`);

    const renderUserList = (users) => {
        if (!userList) return;
        if (users.length === 0) {
            userList.innerHTML = `<p class="text-gray-400 text-sm p-2">${i18n.noUsersToSelect}</p>`;
            return;
        }
        userList.innerHTML = users.map(user => `
            <label class="flex items-center p-2 rounded cursor-pointer gap-2 hover:bg-gray-200 dark:hover:bg-gray-700">
                <input type="checkbox" value="${user.id}" class="h-4 w-4 rounded border-gray-300 text-yellow-600 focus:ring-yellow-500" ${tempSelectedIds.has(user.id) ? 'checked' : ''}>
                <span class="text-sm text-gray-700 dark:text-gray-300">${user.username}</span>
            </label>
        `).join('');
        updateModalCount();
    };

    const closeModal = (resetRadio = false) => {
        modal.classList.add('hidden');
        if (resetRadio && state.selectedUserIds.size === 0) {
            const targetActive = document.getElementById('target_active');
            if (targetActive) targetActive.checked = true;
            if (dom.openUserSelectionBtn) dom.openUserSelectionBtn.disabled = true;
            updateSendButtonText();
        }
    };

    $('bulk-notify-confirm').onclick = () => {
        state.selectedUserIds = tempSelectedIds;
        updateSpecificUserLabel(state.selectedUserIds.size);
        updateSendButtonText();
        closeModal();
    };
    
    $('bulk-notify-cancel').onclick = () => closeModal(true);
    modal.querySelector('.modal-close').onclick = () => closeModal(true);

    getUsersForSelection().then(allUsers => {
        renderUserList(allUsers);
        
        $('bulk-notify-search-input')?.addEventListener('input', (e) => {
            const term = e.target.value.toLowerCase();
            renderUserList(allUsers.filter(u => u.username.toLowerCase().includes(term) || (u.email && u.email.toLowerCase().includes(term))));
        });

        $('bulk-notify-select-all')?.addEventListener('click', () => {
            userList.querySelectorAll('input[type="checkbox"]').forEach(cb => { cb.checked = true; tempSelectedIds.add(parseInt(cb.value)); });
            updateModalCount();
        });

        $('bulk-notify-deselect-all')?.addEventListener('click', () => {
            userList.querySelectorAll('input[type="checkbox"]').forEach(cb => { cb.checked = false; tempSelectedIds.delete(parseInt(cb.value)); });
            updateModalCount();
        });
        
    }).catch(err => userList && (userList.innerHTML = `<p class="text-red-400 text-sm p-2">${err.message}</p>`));

    userList?.addEventListener('change', (e) => {
        if (e.target.type === 'checkbox') {
            const id = parseInt(e.target.value);
            e.target.checked ? tempSelectedIds.add(id) : tempSelectedIds.delete(id);
            updateModalCount();
        }
    });

    modal.classList.remove('hidden');
}