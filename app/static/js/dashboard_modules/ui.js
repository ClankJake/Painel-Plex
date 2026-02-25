// Funções para renderizar e atualizar elementos do DOM
import { dom, state } from './config.js';
import { getChartColors, formatCurrency, formatTime, formatTimeAgo, getReasonText } from './formatters.js';
import { getUsersForSelection } from './api.js';
import { updateSendButtonText } from './handlers.js';

// ==========================================
// CONSTANTES E ÍCONES SVG (Para limpar o código)
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
            <div>
                <p class="text-sm font-medium opacity-80">${label}</p>
                <p class="text-2xl font-bold">${value}</p>
            </div>
        </div>
    `;
}

export function renderSummaryCards(summary) {
    if (!dom.summaryCardsContainer) return;
    const { i18n } = state;
    
    dom.summaryCardsContainer.innerHTML = `
        ${_createSummaryCard('active-streams-card', ICONS.activeStreams, i18n.activeStreams, summary.active_streams, 'bg-blue-500')}
        ${_createSummaryCard('total-users-card', ICONS.totalUsers, i18n.totalUsers, summary.total_users, 'bg-purple-500')}
        ${_createSummaryCard('monthly-revenue-card', ICONS.revenue, i18n.monthlyRevenue, formatCurrency(summary.monthly_revenue), 'bg-green-500')}
        ${_createSummaryCard('upcoming-renewals-card', ICONS.renewals, i18n.upcomingRenewals, summary.upcoming_renewals, 'bg-yellow-500')}
    `;
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

export function renderSystemHealth(health) {
    if (!dom.systemHealthContainer) return;
    const { i18n } = state;
    const serviceMap = {
        plex: i18n.plexServer, tautulli: i18n.tautulli, efi: i18n.paymentEfi,
        mercado_pago: i18n.paymentMp, bpix: i18n.paymentBpix, scheduler: i18n.scheduler
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
// STREAMS EM TEMPO REAL
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

function _getStreamCardInnerHtml(s) {
    const stConfig = _getStreamStateConfig(s.state);
    const platformClass = (s.platform || 'default').toLowerCase().replace(/\s+/g, '');
    const sd = s.stream_details;
    
    let streamText = sd.is_transcoding ? `Transcode` : 'Direct Play';
    if (sd.is_transcoding && typeof sd.transcode_progress === 'number') streamText += ` (${sd.transcode_progress}%)`;
    
    // Adicionado fallback de imagens com onerror para não mostrar imagens rasgadas
    const placeholderImg = "https://placehold.co/150x225/1F2937/E5E7EB?text=Sem+Capa";
    const avatarPlaceholder = "https://placehold.co/24x24/1F2937/E5E7EB?text=U";

    return `
        <div class="w-24 sm:w-28 flex-shrink-0">
            <img src="${s.thumb_url || placeholderImg}" onerror="this.src='${placeholderImg}'" class="w-full h-auto aspect-[2/3] object-cover rounded-md shadow-sm" alt="Poster">
        </div>
        <div class="flex-1 min-w-0 w-full space-y-2">
            <div class="flex justify-between items-start gap-2">
                <div class="flex-1 min-w-0">
                    <h4 class="font-bold text-base sm:text-lg text-gray-900 dark:text-white truncate" title="${s.title}">${s.title}</h4>
                    <p class="text-xs sm:text-sm text-gray-500 dark:text-gray-400 truncate" title="${s.subtitle}">${s.subtitle}</p>
                </div>
                <div class="platform-icon platform-${platformClass} flex-shrink-0" title="${s.platform}"></div>
            </div>
            
            <div class="space-y-1 text-xs text-gray-500 dark:text-gray-400">
                <p title="${s.player}"><strong>Dispositivo:</strong> <span class="truncate">${s.player}</span></p>
                <p title="${streamText} (${sd.container})"><strong>Stream:</strong> <span class="truncate">${streamText} (${sd.container})</span></p>
                <p title="${sd.video_decision} (${sd.video_codec} ${sd.video_resolution})"><strong>Video:</strong> <span class="truncate">${sd.video_decision} (${sd.video_codec} ${sd.video_resolution})</span></p>
                <p title="${sd.audio_decision} (${sd.audio_codec})"><strong>Audio:</strong> <span class="truncate">${sd.audio_decision} (${sd.audio_codec})</span></p>
            </div>
            
            <div class="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-300 pt-1">
                <img src="${s.user_thumb || avatarPlaceholder}" onerror="this.src='${avatarPlaceholder}'" class="w-6 h-6 rounded-full">
                <span class="font-semibold truncate">${s.user}</span>
                <div class="ml-auto flex items-center gap-1">
                    <span id="time-${s.session_key}" class="text-xs font-mono whitespace-nowrap">${formatTime(s.view_offset)}/${formatTime(s.duration)}</span>
                    <span id="state-icon-${s.session_key}" class="${stConfig.color}">${stConfig.icon}</span>
                </div>
            </div>
            <div class="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-1.5">
                <div id="progress-${s.session_key}" class="bg-yellow-400 h-1.5 rounded-full" style="width: ${s.progress}%"></div>
            </div>
        </div>
    `;
}

// CORREÇÃO CRÍTICA DO MEMORY LEAK: o ID do interval estava a ser perdido porque `this` não existe em arrow functions.
function _createTimerInterval(sessionKey) {
    const intervalId = setInterval(() => {
        const timer = state.activeTimers[sessionKey];
        if (!timer) {
            clearInterval(intervalId); // <-- Correção aqui
            return;
        }
        const elapsedSinceUpdate = Date.now() - timer.last_updated;
        const current_offset = timer.view_offset + elapsedSinceUpdate;
        
        const timeEl = document.getElementById(`time-${sessionKey}`);
        const progressEl = document.getElementById(`progress-${sessionKey}`);
        
        if (timeEl) timeEl.textContent = `${formatTime(current_offset)}/${formatTime(timer.duration)}`;
        if (progressEl && timer.duration > 0) {
            progressEl.style.width = `${Math.min(100, (current_offset / timer.duration) * 100)}%`;
        }
    }, 1000);
    
    return intervalId;
}

export function renderActiveStreamsDashboard(sessions) {
    if (!dom.activeStreamsSection || !dom.activeStreamsContainer) return;
    const newSessionKeys = new Set(sessions.map(s => s.session_key));

    // Limpa streams que já pararam
    dom.activeStreamsContainer.querySelectorAll('.stream-card').forEach(card => {
        const key = card.dataset.sessionKey;
        if (!newSessionKeys.has(key)) {
            card.remove();
            if (state.activeTimers[key]?.interval) {
                clearInterval(state.activeTimers[key].interval);
            }
            delete state.activeTimers[key];
        }
    });

    if (sessions && sessions.length > 0) {
        dom.activeStreamsSection.classList.remove('hidden');
        sessions.forEach(s => {
            let card = dom.activeStreamsContainer.querySelector(`[data-session-key="${s.session_key}"]`);
            if (!card) {
                card = document.createElement('div');
                card.dataset.sessionKey = s.session_key;
                card.className = 'stream-card bg-white dark:bg-gray-800 p-3 sm:p-4 rounded-xl shadow-md flex items-start gap-3 sm:gap-4 overflow-hidden';
                dom.activeStreamsContainer.appendChild(card);
            }
            card.innerHTML = _getStreamCardInnerHtml(s);
        });
    } else {
        dom.activeStreamsSection.classList.add('hidden');
        dom.activeStreamsContainer.innerHTML = '';
    }

    // Gere o estado dos timers
    sessions.forEach(s => {
        const timer = state.activeTimers[s.session_key];
        if (timer) {
            timer.view_offset = s.view_offset;
            timer.duration = s.duration;
            timer.last_updated = Date.now();
            
            if (s.state !== 'playing' && timer.interval) {
                clearInterval(timer.interval);
                timer.interval = null;
            } else if (s.state === 'playing' && !timer.interval) {
                timer.interval = _createTimerInterval(s.session_key);
            }
            timer.state = s.state;
        } else {
            state.activeTimers[s.session_key] = {
                view_offset: s.view_offset,
                duration: s.duration,
                state: s.state,
                last_updated: Date.now(),
                interval: s.state === 'playing' ? _createTimerInterval(s.session_key) : null
            };
        }
    });
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
    const timeAgo = formatTimeAgo(new Date(log.timestamp + 'Z'));
    const isBlocked = log.reason.startsWith('blocked');
    const icon = isBlocked 
        ? `<svg class="w-5 h-5 text-red-500" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10 1a4.5 4.5 0 00-4.5 4.5V9H5a2 2 0 00-2 2v6a2 2 0 002 2h10a2 2 0 002-2v-6a2 2 0 00-2-2h-.5V5.5A4.5 4.5 0 0010 1zm-2.5 8V5.5a2.5 2.5 0 115 0V9h-5z" clip-rule="evenodd" /></svg>`
        : `<svg class="w-5 h-5 text-orange-500" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M4.25 2A2.25 2.25 0 002 4.25v11.5A2.25 2.25 0 004.25 18h11.5A2.25 2.25 0 0018 15.75V4.25A2.25 2.25 0 0015.75 2H4.25zM6.5 6a.75.75 0 000 1.5h7a.75.75 0 000-1.5h-7zM6 10.25a.75.75 0 01.75-.75h7a.75.75 0 010 1.5h-7A.75.75 0 016 10.25zM7.25 14a.75.75 0 000 1.5h3a.75.75 0 000-1.5h-3z" clip-rule="evenodd" /></svg>`;
    
    return `
    <div class="flex items-center justify-between p-2 rounded-lg bg-gray-50 dark:bg-gray-700/50 animate-fade-in group" data-log-id="${log.id}">
        <div class="flex items-center gap-3 min-w-0">
            <span class="p-2 ${isBlocked ? 'bg-red-100 dark:bg-red-900/50' : 'bg-orange-100 dark:bg-orange-900/50'} rounded-full flex-shrink-0">${icon}</span>
            <div class="min-w-0">
                <p class="text-sm font-semibold text-gray-800 dark:text-gray-200">
                    <strong>${log.username}</strong> - ${getReasonText(log.reason)}
                </p>
                <p class="text-xs text-gray-500 dark:text-gray-400 truncate">
                   ${log.media_title} em ${log.platform} - <span class="log-time-ago" data-timestamp="${log.timestamp}">${timeAgo}</span>
                </p>
            </div>
        </div>
        <button data-action="delete" title="Apagar Log" class="delete-log-btn p-1 text-gray-400 hover:text-red-500 transition-colors opacity-0 group-hover:opacity-100">
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

    // Injeta HTML Estrutural
    const parts = _getModalHtmlParts(i18n);
    modal.querySelector('.modal-title').textContent = i18n.selectUsers;
    modalBody.innerHTML = parts.body;
    modalFooter.innerHTML = parts.footer;

    const tempSelectedIds = new Set(state.selectedUserIds);
    
    // Bindings de UI rápidos
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

    // Listeners
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
