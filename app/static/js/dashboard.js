import { fetchAPI, showToast, createModal } from './utils.js';

document.addEventListener('DOMContentLoaded', () => {
    // --- ELEMENTOS E DADOS GLOBAIS ---
    const loadingIndicator = document.getElementById('loadingIndicator');
    const dashboardContainer = document.getElementById('dashboardContainer');
    const errorContainer = document.getElementById('errorContainer');
    const errorMessage = document.getElementById('errorMessage');
    const realtimeStatus = document.getElementById('realtime-status');
    const sendBulkNotificationBtn = document.getElementById('send-bulk-notification-btn');
    const contactsOnlyCheckbox = document.getElementById('contacts_only_checkbox');
    const clearAllLogsBtn = document.getElementById('clear-all-logs-btn');

    const scriptTag = document.getElementById('dashboard-script');
    const urls = {};
    const i18n = {};

    // CORREÇÃO: Popula os objetos urls e i18n a partir dos data-attributes do script tag.
    if (scriptTag) {
        for (const key in scriptTag.dataset) {
            if (key.startsWith('i18n')) {
                const i18nKey = key.charAt(4).toLowerCase() + key.slice(5).replace(/-(\w)/g, (_, letter) => letter.toUpperCase());
                i18n[i18nKey] = scriptTag.dataset[key];
            } else {
                // Converte kebab-case para camelCase para as chaves do objeto urls.
                // Ex: data-summary-url se torna summaryUrl
                const urlKey = key.replace(/-(\w)/g, (_, letter) => letter.toUpperCase());
                urls[urlKey] = scriptTag.dataset[key];
            }
        }
    }

    let monthlyRevenueChart = null;
    let userStatusChart = null;
    let activeTimers = {}; // Para controlar os temporizadores de progresso
    let timeAgoInterval = null;

    // --- FUNÇÕES AUXILIARES ---
    function getChartColors() {
        const isDark = document.documentElement.classList.contains('dark');
        return {
            textColor: isDark ? '#E5E7EB' : '#1F2937',
            gridColor: isDark ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.1)',
            tooltipBg: isDark ? '#1F2937' : '#FFFFFF',
            barColor: 'rgba(34, 197, 94, 0.6)',
            barBorderColor: 'rgba(22, 163, 74, 1)',
            doughnutColors: ['#22C55E', '#EF4444'], // green, red
        };
    }

    function formatCurrency(value) {
        return (value || 0).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
    }

    function formatTime(ms) {
        if (typeof ms !== 'number' || ms < 0) {
            return '00:00';
        }
        const totalSeconds = Math.floor(ms / 1000);
        const hours = Math.floor(totalSeconds / 3600);
        const minutes = Math.floor((totalSeconds % 3600) / 60);
        const seconds = totalSeconds % 60;

        if (hours > 0) {
            return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
        }
        return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
    }
    
    function formatTimeAgo(date) {
        const now = new Date();
        const seconds = Math.floor((now - date) / 1000);

        let interval = seconds / 31536000;
        if (interval > 1) return `${Math.floor(interval)}a atrás`;
        interval = seconds / 2592000;
        if (interval > 1) return `${Math.floor(interval)}m atrás`;
        interval = seconds / 86400;
        if (interval > 1) return `${Math.floor(interval)}d atrás`;
        interval = seconds / 3600;
        if (interval > 1) return `${Math.floor(interval)}h atrás`;
        interval = seconds / 60;
        if (interval > 1) return `${Math.floor(interval)}min atrás`;
        if (seconds < 5) return `agora mesmo`;
        return `${Math.floor(seconds)}s atrás`;
    }
    
    // --- LÓGICA DE RENDERIZAÇÃO ---
    function createSummaryCard(id, icon, label, value, colorClass, isButton = false) {
        const tag = isButton ? 'button' : 'div';
        const buttonClasses = isButton ? 'hover:scale-105 hover:shadow-lg focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-white/50' : '';
        return `
            <${tag} id="${id}" class="p-4 rounded-xl flex items-center gap-4 transition-all duration-300 ${colorClass} ${buttonClasses} text-left w-full">
                <div class="p-3 bg-white/20 rounded-lg">${icon}</div>
                <div>
                    <p class="text-sm font-medium opacity-80">${label}</p>
                    <p class="text-2xl font-bold">${value}</p>
                </div>
            </${tag}>
        `;
    }

    function renderSummaryCards(summary) {
        const summaryCardsContainer = document.getElementById('summaryCards');
        if (!summaryCardsContainer) return;

        summaryCardsContainer.innerHTML = `
            ${createSummaryCard('active-streams-card', '<svg class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" /></svg>', i18n.activeStreams, summary.active_streams, 'bg-blue-500 text-white', false)}
            ${createSummaryCard('total-users-card', '<svg class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" /></svg>', i18n.totalUsers, summary.total_users, 'bg-purple-500 text-white')}
            ${createSummaryCard('monthly-revenue-card', '<svg class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v.01" /></svg>', i18n.monthlyRevenue, formatCurrency(summary.monthly_revenue), 'bg-green-500 text-white')}
            ${createSummaryCard('upcoming-renewals-card', '<svg class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" /></svg>', i18n.upcomingRenewals, summary.upcoming_renewals, 'bg-yellow-500 text-white')}
        `;
    }

    function renderCharts(summary) {
        const colors = getChartColors();
        const revenueCanvas = document.getElementById('monthlyRevenueChart');
        const userStatusCanvas = document.getElementById('userStatusChart');

        if (revenueCanvas) {
            const dailyData = summary.daily_revenue || {};
            const daysInMonth = new Date(new Date().getFullYear(), new Date().getMonth() + 1, 0).getDate();
            const labels = Array.from({ length: daysInMonth }, (_, i) => i + 1);
            const data = labels.map(day => dailyData[day] || 0);

            if (monthlyRevenueChart) {
                monthlyRevenueChart.data.labels = labels;
                monthlyRevenueChart.data.datasets[0].data = data;
                monthlyRevenueChart.update('none');
            } else {
                 monthlyRevenueChart = new Chart(revenueCanvas.getContext('2d'), {
                    type: 'bar',
                    data: {
                        labels: labels,
                        datasets: [{
                            label: i18n.revenueLabel,
                            data: data,
                            backgroundColor: colors.barColor,
                            borderColor: colors.barBorderColor,
                            borderWidth: 1,
                            borderRadius: 4,
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: { legend: { display: false } },
                        scales: {
                            y: { beginAtZero: true, ticks: { color: colors.textColor, callback: (value) => formatCurrency(value) }, grid: { color: colors.gridColor } },
                            x: { ticks: { color: colors.textColor }, grid: { display: false } }
                        }
                    }
                });
            }
        }
        
        if (userStatusCanvas) {
            const data = [summary.active_users, summary.blocked_users];
            if (userStatusChart) {
                userStatusChart.data.datasets[0].data = data;
                userStatusChart.update('none');
            } else {
                 userStatusChart = new Chart(userStatusCanvas.getContext('2d'), {
                    type: 'doughnut',
                    data: {
                        labels: [i18n.activeUsersLabel, i18n.blockedUsersLabel],
                        datasets: [{
                            data: data,
                            backgroundColor: colors.doughnutColors,
                            borderColor: colors.tooltipBg,
                            borderWidth: 4,
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            legend: { position: 'bottom', labels: { color: colors.textColor, font: { size: 14 } } }
                        }
                    }
                });
            }
        }
    }

    function renderSystemHealth(health) {
        const container = document.getElementById('systemHealthContainer');
        if (!container) return;

        const serviceMap = {
            plex: { label: i18n.plexServer },
            tautulli: { label: i18n.tautulli },
            efi: { label: i18n.paymentEfi },
            mercado_pago: { label: i18n.paymentMp },
            bpix: { label: i18n.paymentBpix },
            scheduler: { label: i18n.scheduler }
        };

        const statusMap = {
            ONLINE: { text: i18n.online, color: 'bg-green-500', icon: '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg>' },
            RUNNING: { text: i18n.running, color: 'bg-green-500', icon: '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg>' },
            OFFLINE: { text: i18n.offline, color: 'bg-red-500', icon: '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg>' },
            STOPPED: { text: i18n.stopped, color: 'bg-red-500', icon: '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg>' },
            DISABLED: { text: i18n.disabled, color: 'bg-gray-500', icon: '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636"></path></svg>' }
        };

        container.innerHTML = Object.entries(health).map(([key, value]) => {
            const service = serviceMap[key] || { label: key };
            const status = statusMap[value.status] || statusMap['OFFLINE'];
            return `
                <div class="flex items-center p-3 bg-gray-100 dark:bg-gray-900/50 rounded-lg" title="${value.message}">
                    <div class="flex-shrink-0 w-8 h-8 rounded-full ${status.color} flex items-center justify-center text-white">
                        ${status.icon}
                    </div>
                    <div class="ml-3">
                        <p class="text-sm font-semibold text-gray-800 dark:text-gray-200">${service.label}</p>
                        <p class="text-xs text-gray-500 dark:text-gray-400">${status.text}</p>
                    </div>
                </div>
            `;
        }).join('');
    }

    function getStreamCardInnerHtml(s) {
        let stateIcon = '';
        let iconColorClass = '';

        if (s.state === 'paused') {
            iconColorClass = 'text-yellow-500';
            stateIcon = `<svg class="w-5 h-5" viewBox="0 0 24 24" fill="currentColor"><path d="M9 8h2v8H9zm4 0h2v8h-2z"></path></svg>`;
        } else if (s.state === 'buffering') {
            iconColorClass = 'text-blue-500';
            stateIcon = `<svg class="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>`;
        } else { // playing
            iconColorClass = 'text-green-500';
            stateIcon = `<svg class="w-5 h-5" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"></path></svg>`;
        }

        const platform_icon_name = (s.platform || 'default').toLowerCase().replace(/\s+/g, '');

        const sd = s.stream_details;
        let streamText = sd.is_transcoding ? `Transcode` : 'Direct Play';
        if (sd.is_transcoding && typeof sd.transcode_progress === 'number') {
            streamText += ` (${sd.transcode_progress}%)`;
        }
        const videoText = `${sd.video_decision} (${sd.video_codec} ${sd.video_resolution})`;
        const audioText = `${sd.audio_decision} (${sd.audio_codec})`;
        const streamDetailsHtml = `
            <div class="space-y-1 text-xs text-gray-500 dark:text-gray-400">
                <p title="${s.player}"><strong>Dispositivo:</strong> <span class="truncate">${s.player}</span></p>
                <p title="${streamText} (${sd.container})"><strong>Stream:</strong> <span class="truncate">${streamText} (${sd.container})</span></p>
                <p title="${videoText}"><strong>Video:</strong> <span class="truncate">${videoText}</span></p>
                <p title="${audioText}"><strong>Audio:</strong> <span class="truncate">${audioText}</span></p>
            </div>
        `;

        return `
            <div class="w-24 sm:w-28 flex-shrink-0">
                <img src="${s.thumb_url || 'https://placehold.co/150x225/1F2937/E5E7EB?text=?'}" class="w-full h-auto aspect-[2/3] object-cover rounded-md shadow-sm" alt="Poster">
            </div>
            <div class="flex-1 min-w-0 w-full space-y-2">
                <div class="flex justify-between items-start gap-2">
                    <div class="flex-1 min-w-0">
                        <h4 class="font-bold text-base sm:text-lg text-gray-900 dark:text-white truncate" title="${s.title}">${s.title}</h4>
                        <p class="text-xs sm:text-sm text-gray-500 dark:text-gray-400 truncate" title="${s.subtitle}">${s.subtitle}</p>
                    </div>
                    <div class="platform-icon platform-${platform_icon_name} flex-shrink-0" title="${s.platform}"></div>
                </div>
                
                ${streamDetailsHtml}

                <div class="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-300 pt-1">
                    <img src="${s.user_thumb || 'https://placehold.co/24x24/1F2937/E5E7EB?text=?'}" class="w-6 h-6 rounded-full">
                    <span class="font-semibold truncate">${s.user}</span>
                    <div class="ml-auto flex items-center gap-1">
                        <span id="time-${s.session_key}" class="text-xs font-mono whitespace-nowrap">${formatTime(s.view_offset)}/${formatTime(s.duration)}</span>
                        <span id="state-icon-${s.session_key}" class="${iconColorClass}">${stateIcon}</span>
                    </div>
                </div>
                <div class="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-1.5">
                    <div id="progress-${s.session_key}" class="bg-yellow-400 h-1.5 rounded-full" style="width: ${s.progress}%"></div>
                </div>
            </div>
        `;
    }

    function renderActiveStreamsDashboard(sessions) {
        const section = document.getElementById('active-streams-section');
        const container = document.getElementById('activeStreamsContainer');
        if (!section || !container) return;
    
        const newSessionKeys = new Set(sessions.map(s => s.session_key));
    
        // 1. Remove cartões de sessões que não existem mais
        container.querySelectorAll('.stream-card').forEach(card => {
            const key = card.dataset.sessionKey;
            if (!newSessionKeys.has(key)) {
                card.remove();
                clearInterval(activeTimers[key]?.interval);
                delete activeTimers[key];
            }
        });
    
        if (sessions && sessions.length > 0) {
            section.classList.remove('hidden');
    
            // 2. Adiciona ou atualiza os cartões de sessão
            sessions.forEach(s => {
                let card = container.querySelector(`[data-session-key="${s.session_key}"]`);
                if (!card) {
                    card = document.createElement('div');
                    card.dataset.sessionKey = s.session_key;
                    card.className = 'stream-card bg-white dark:bg-gray-800/80 p-3 sm:p-4 rounded-xl shadow-lg border border-gray-200 dark:border-gray-700 flex items-start gap-3 sm:gap-4 overflow-hidden';
                    container.appendChild(card);
                }
                card.innerHTML = getStreamCardInnerHtml(s);
            });
        } else {
            section.classList.add('hidden');
            container.innerHTML = '';
        }
    
        // 3. Gerencia os timers para a animação suave
        sessions.forEach(s => {
            const timer = activeTimers[s.session_key];
    
            if (timer) {
                // Sincroniza com os dados do servidor
                timer.view_offset = s.view_offset;
                timer.duration = s.duration;
                timer.last_updated = Date.now();
    
                if (s.state !== 'playing' && timer.interval) {
                    clearInterval(timer.interval);
                    timer.interval = null;
                } else if (s.state === 'playing' && !timer.interval) {
                    timer.interval = createTimerInterval(s.session_key);
                }
                timer.state = s.state;
            } else {
                // Cria um novo timer
                activeTimers[s.session_key] = {
                    view_offset: s.view_offset,
                    duration: s.duration,
                    state: s.state,
                    last_updated: Date.now(),
                    interval: s.state === 'playing' ? createTimerInterval(s.session_key) : null
                };
            }
        });
    }

    function createTimerInterval(sessionKey) {
        return setInterval(() => {
            const timer = activeTimers[sessionKey];
            if (!timer) {
                clearInterval(this);
                return;
            };
            
            const elapsedSinceUpdate = Date.now() - timer.last_updated;
            const current_offset = timer.view_offset + elapsedSinceUpdate;
            
            const timeEl = document.getElementById(`time-${sessionKey}`);
            const progressEl = document.getElementById(`progress-${sessionKey}`);
            
            if (timeEl) timeEl.textContent = `${formatTime(current_offset)}/${formatTime(timer.duration)}`;
            if (progressEl && timer.duration > 0) {
                progressEl.style.width = `${Math.min(100, (current_offset / timer.duration) * 100)}%`;
            }
        }, 1000);
    }

    // --- FUNÇÕES DE LOG DE AUDITORIA COM CORREÇÕES ---
    function updateLogTimestamps() {
        document.querySelectorAll('.log-time-ago').forEach(el => {
            const timestamp = el.dataset.timestamp;
            if (timestamp) {
                const date = new Date(timestamp + 'Z');
                el.textContent = formatTimeAgo(date);
            }
        });
    }

    function getReasonText(reason) {
        switch (reason) {
            case 'limit_exceeded':
                return i18n.reasonLimitExceeded;
            case 'blocked_manual':
                return i18n.reasonBlockedManual;
            case 'blocked_expired':
                return i18n.reasonBlockedExpired;
            case 'blocked_trial_expired':
                return i18n.reasonBlockedTrialExpired;
            default:
                if (reason && reason.startsWith('blocked')) {
                    return i18n.reasonBlocked;
                }
                return reason || 'Desconhecido';
        }
    }

    function createTerminationLogRow(log) {
        const timeAgo = formatTimeAgo(new Date(log.timestamp + 'Z')); 
        const isBlocked = log.reason.startsWith('blocked');
        const icon = isBlocked
            ? `<svg class="w-5 h-5 text-red-500" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10 1a4.5 4.5 0 00-4.5 4.5V9H5a2 2 0 00-2 2v6a2 2 0 002 2h10a2 2 0 002-2v-6a2 2 0 00-2-2h-.5V5.5A4.5 4.5 0 0010 1zm-2.5 8V5.5a2.5 2.5 0 115 0V9h-5z" clip-rule="evenodd" /></svg>`
            : `<svg class="w-5 h-5 text-orange-500" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M4.25 2A2.25 2.25 0 002 4.25v11.5A2.25 2.25 0 004.25 18h11.5A2.25 2.25 0 0018 15.75V4.25A2.25 2.25 0 0015.75 2H4.25zM6.5 6a.75.75 0 000 1.5h7a.75.75 0 000-1.5h-7zM6 10.25a.75.75 0 01.75-.75h7a.75.75 0 010 1.5h-7A.75.75 0 016 10.25zM7.25 14a.75.75 0 000 1.5h3a.75.75 0 000-1.5h-3z" clip-rule="evenodd" /></svg>`;
        const iconBg = isBlocked ? 'bg-red-100 dark:bg-red-900/50' : 'bg-orange-100 dark:bg-orange-900/50';
        const reasonText = getReasonText(log.reason);

        return `
        <div class="flex items-center justify-between p-2 rounded-lg bg-gray-50 dark:bg-gray-700/50 animate-fade-in group" data-log-id="${log.id}">
            <div class="flex items-center gap-3 min-w-0">
                <span class="p-2 ${iconBg} rounded-full flex-shrink-0">${icon}</span>
                <div class="min-w-0">
                    <p class="text-sm font-semibold text-gray-800 dark:text-gray-200">
                        <strong>${log.username}</strong> - ${reasonText}
                    </p>
                    <p class="text-xs text-gray-500 dark:text-gray-400 truncate">
                       ${log.media_title} em ${log.platform} - <span class="log-time-ago" data-timestamp="${log.timestamp}">${timeAgo}</span>
                    </p>
                </div>
            </div>
            <button data-action="delete" title="${'Apagar Log'}" class="delete-log-btn p-1 text-gray-400 hover:text-red-500 transition-colors opacity-0 group-hover:opacity-100">
                <svg class="w-5 h-5" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z" clip-rule="evenodd" /></svg>
            </button>
        </div>`;
    }

    function renderTerminationLogs(logs) {
        const container = document.getElementById('auditLogContainer');
        if (!container) return;
        if (!logs || logs.length === 0) {
            container.innerHTML = `<p class="text-gray-500 dark:text-gray-400">${i18n.noTerminatedSessions}</p>`;
            if (clearAllLogsBtn) clearAllLogsBtn.disabled = true;
            return;
        }
        container.innerHTML = logs.map(log => createTerminationLogRow(log)).join('');
        if (clearAllLogsBtn) clearAllLogsBtn.disabled = false;
        
        if (timeAgoInterval) clearInterval(timeAgoInterval);
        timeAgoInterval = setInterval(updateLogTimestamps, 30000);
    }

    function prependTerminationLog(log) {
        const container = document.getElementById('auditLogContainer');
        if (!container) return;
        
        const placeholder = container.querySelector('p');
        if (placeholder) {
            placeholder.remove();
        }
        
        const newRowHtml = createTerminationLogRow(log);
        container.insertAdjacentHTML('afterbegin', newRowHtml);
        
        while (container.children.length > 20) {
            container.lastElementChild.remove();
        }
        if (clearAllLogsBtn) clearAllLogsBtn.disabled = false;
    }

    async function handleDeleteLog(logId) {
        try {
            const url = urls.deleteLogBaseUrl.replace('0', logId);
            const result = await fetchAPI(url, 'DELETE');
            showToast(result.message, result.success ? 'success' : 'error');
            if (result.success) {
                const logElement = document.querySelector(`[data-log-id="${logId}"]`);
                logElement.style.opacity = '0';
                setTimeout(() => {
                    logElement.remove();
                    const container = document.getElementById('auditLogContainer');
                    if (container.children.length === 0) {
                        renderTerminationLogs([]);
                    }
                }, 300);
            }
        } catch (error) {
            showToast(error.message, 'error');
        }
    }

    async function handleClearAllLogs() {
        try {
            const result = await fetchAPI(urls.clearAllLogsUrl, 'POST');
            showToast(result.message, result.success ? 'success' : 'error');
            if (result.success) {
                renderTerminationLogs([]);
            }
        } catch (error) {
            showToast(error.message, 'error');
        }
    }

    async function loadDashboardData() {
        loadingIndicator.style.display = 'block';
        dashboardContainer.classList.add('hidden');
        errorContainer.classList.add('hidden');

        try {
            const summaryPromise = fetchAPI(`${urls.summaryUrl}?force=true`);
            const healthPromise = fetchAPI(urls.healthUrl);
            const streamsPromise = fetchAPI(urls.activeStreamsUrl);
            const auditPromise = fetchAPI(urls.auditLogsUrl);

            const [summaryData, healthData, streamsData, auditData] = await Promise.all([summaryPromise, healthPromise, streamsPromise, auditPromise]);

            if (summaryData.success) {
                renderSummaryCards(summaryData.summary);
                renderCharts(summaryData.summary);
            } else {
                throw new Error(summaryData.message);
            }

            if (healthData.success) {
                renderSystemHealth(healthData.health);
            }

            if (streamsData.success) {
                renderActiveStreamsDashboard(streamsData.sessions);
            }
            
            if (auditData.success) {
                renderTerminationLogs(auditData.logs);
            }

            dashboardContainer.classList.remove('hidden');
        } catch (error) {
            errorMessage.textContent = error.message;
            errorContainer.classList.remove('hidden');
        } finally {
            loadingIndicator.style.display = 'none';
        }
    }

    // --- LÓGICA WEBSOCKET ---
    function setupWebSocket() {
        const socket = io('/dashboard', { reconnectionAttempts: 5, transports: ['websocket'] });

        const setStatus = (status, text) => {
            if (!realtimeStatus) return;
            const dot = realtimeStatus.querySelector('div');
            const span = realtimeStatus.querySelector('span');
            
            dot.className = 'w-2 h-2 rounded-full';
            realtimeStatus.classList.remove('bg-green-200', 'text-green-800', 'dark:bg-green-900', 'dark:text-green-200', 'bg-yellow-200', 'text-yellow-800', 'dark:bg-yellow-900', 'dark:text-yellow-200', 'bg-red-200', 'text-red-800', 'dark:bg-red-900', 'dark:text-red-200');

            switch (status) {
                case 'connected':
                    dot.classList.add('bg-green-500');
                    realtimeStatus.classList.add('bg-green-200', 'text-green-800', 'dark:bg-green-900', 'dark:text-green-200');
                    break;
                case 'reconnecting':
                    dot.classList.add('bg-yellow-500', 'animate-pulse');
                     realtimeStatus.classList.add('bg-yellow-200', 'text-yellow-800', 'dark:bg-yellow-900', 'dark:text-yellow-200');
                    break;
                case 'disconnected':
                    dot.classList.add('bg-red-500');
                    realtimeStatus.classList.add('bg-red-200', 'text-red-800', 'dark:bg-red-900', 'dark:text-red-200');
                    break;
            }
            span.textContent = text;
        };

        setStatus('reconnecting', i18n.connecting);

        socket.on('connect', () => {
            console.log('Conectado ao dashboard em tempo real!');
            setStatus('connected', i18n.connected);
        });

        socket.on('dashboard_update', (data) => {
            if (data.summary) {
                renderSummaryCards(data.summary);
                renderCharts(data.summary);
            }
        });
        
        socket.on('active_streams_update', (data) => {
            renderActiveStreamsDashboard(data.sessions);
        });

        socket.on('new_termination_log', (log) => {
            prependTerminationLog(log);
        });

        socket.on('disconnect', () => {
            console.warn('Desconectado do dashboard em tempo real.');
            setStatus('disconnected', i18n.disconnected);
        });
        
        socket.on('reconnect_attempt', () => {
            console.log('Tentando reconectar...');
            setStatus('reconnecting', i18n.reconnecting);
        });

        socket.on('connect_error', (error) => {
            console.error('Erro de conexão com o WebSocket:', error);
            setStatus('disconnected', i18n.disconnected);
        });

        const progressContainer = document.getElementById('bulk-progress-container');
        const progressBar = document.getElementById('bulk-progress-bar');
        const progressText = document.getElementById('bulk-progress-text');
        const progressPercent = document.getElementById('bulk-progress-percent');

        socket.on('bulk_notification_start', (data) => {
            progressContainer.classList.remove('hidden');
            sendBulkNotificationBtn.disabled = true;
            sendBulkNotificationBtn.textContent = i18n.sendingBulkNotification;
            progressBar.style.width = '0%';
            progressBar.classList.remove('bg-red-600');
            progressBar.classList.add('bg-blue-600');
            progressPercent.textContent = '0%';
            progressText.textContent = i18n.bulkSendProgress.replace('{current}', 0).replace('{total}', data.total);
        });

        socket.on('bulk_notification_progress', (data) => {
            const percent = data.total > 0 ? Math.round((data.current / data.total) * 100) : 0;
            progressBar.style.width = `${percent}%`;
            progressPercent.textContent = `${percent}%`;
            progressText.textContent = i18n.bulkSendProgress.replace('{current}', data.current).replace('{total}', data.total);
        });

        socket.on('bulk_notification_end', (data) => {
            progressBar.style.width = '100%';
            progressPercent.textContent = '100%';
            progressText.textContent = i18n.bulkSendComplete;
            showToast(i18n.bulkSendComplete, 'success');
            setTimeout(() => {
                sendBulkNotificationBtn.disabled = false;
                sendBulkNotificationBtn.innerHTML = `<svg class="w-5 h-5 mr-2" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor"><path d="M3.105 2.289a.75.75 0 00-.826.95l1.414 4.925A1.5 1.5 0 005.135 9.25h6.115a.75.75 0 010 1.5H5.135a1.5 1.5 0 00-1.442 1.086L2.279 16.76a.75.75 0 00.95.826l16-5.333a.75.75 0 000-1.418l-16-5.333z" /></svg> ${i18n.sendToAllActive}`;
                progressContainer.classList.add('hidden');
                document.getElementById('bulk_message').value = '';
                if (contactsOnlyCheckbox) contactsOnlyCheckbox.checked = false;
            }, 3000);
        });
        
        socket.on('bulk_notification_error', (data) => {
            showToast(`${i18n.bulkSendError}: ${data.message}`, 'error');
            progressText.textContent = i18n.bulkSendError;
            progressBar.classList.remove('bg-blue-600');
            progressBar.classList.add('bg-red-600');
            setTimeout(() => {
                 sendBulkNotificationBtn.disabled = false;
                sendBulkNotificationBtn.innerHTML = `<svg class="w-5 h-5 mr-2" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor"><path d="M3.105 2.289a.75.75 0 00-.826.95l1.414 4.925A1.5 1.5 0 005.135 9.25h6.115a.75.75 0 010 1.5H5.135a1.5 1.5 0 00-1.442 1.086L2.279 16.76a.75.75 0 00.95.826l16-5.333a.75.75 0 000-1.418l-16-5.333z" /></svg> ${i18n.sendToAllActive}`;
                progressContainer.classList.add('hidden');
            }, 5000);
        });
    }

    // --- EVENT LISTENERS ---
    window.addEventListener('themeChanged', () => {
       if(dashboardContainer.classList.contains('hidden')) return;
        const colors = getChartColors();
        if (monthlyRevenueChart) {
            monthlyRevenueChart.options.scales.y.ticks.color = colors.textColor;
            monthlyRevenueChart.options.scales.y.grid.color = colors.gridColor;
            monthlyRevenueChart.options.scales.x.ticks.color = colors.textColor;
            monthlyRevenueChart.update();
        }
        if (userStatusChart) {
            userStatusChart.data.datasets[0].borderColor = colors.tooltipBg;
            userStatusChart.options.plugins.legend.labels.color = colors.textColor;
            userStatusChart.update();
        }
    });

    if (sendBulkNotificationBtn) {
        sendBulkNotificationBtn.addEventListener('click', () => {
            const message = document.getElementById('bulk_message').value;
            if (!message.trim()) {
                showToast('Por favor, escreva uma mensagem para enviar.', 'error');
                return;
            }
            
            createModal('confirmationModal', i18n.confirmBulkSendTitle, 
                `<p>${i18n.confirmBulkSendMessage}</p>`,
                `<button id="modalConfirm" class="btn bg-red-600 text-white">${i18n.confirmSendButton}</button>
                 <button id="modalCancel" class="btn bg-gray-200 dark:bg-gray-600">${i18n.cancel}</button>`
            );
            
            document.getElementById('modalConfirm').onclick = async () => {
               document.getElementById('confirmationModal').classList.add('hidden');
               try {
                   const payload = { 
                       message, 
                       contacts_only: contactsOnlyCheckbox.checked 
                   };
                   const result = await fetchAPI(urls.bulkNotifyUrl, 'POST', payload);
                   showToast(result.message, result.success ? 'success' : 'error');
               } catch (error) {
                   showToast(error.message, 'error');
               }
            };
            document.getElementById('modalCancel').onclick = () => document.getElementById('confirmationModal').classList.add('hidden');
        });
    }

    if (clearAllLogsBtn) {
        clearAllLogsBtn.addEventListener('click', () => {
            createModal('confirmationModal', i18n.confirmClearLogsTitle,
                `<p>${i18n.confirmClearLogsMessage}</p>`,
                `<button id="modalConfirm" class="btn bg-red-600 text-white">${i18n.confirmClearLogsButton}</button>
                 <button id="modalCancel" class="btn bg-gray-200 dark:bg-gray-600">${i18n.cancel}</button>`
            );

            document.getElementById('modalConfirm').onclick = () => {
                document.getElementById('confirmationModal').classList.add('hidden');
                handleClearAllLogs();
            };
            document.getElementById('modalCancel').onclick = () => document.getElementById('confirmationModal').classList.add('hidden');
        });
    }

    document.getElementById('auditLogContainer')?.addEventListener('click', (e) => {
        const deleteButton = e.target.closest('.delete-log-btn');
        if (deleteButton) {
            const logId = deleteButton.parentElement.dataset.logId;
            createModal('confirmationModal', i18n.confirmDeleteLogTitle,
                `<p>${i18n.confirmDeleteLogMessage}</p>`,
                `<button id="modalConfirm" class="btn bg-red-600 text-white">${i18n.confirmDeleteLogButton}</button>
                 <button id="modalCancel" class="btn bg-gray-200 dark:bg-gray-600">${i18n.cancel}</button>`
            );

            document.getElementById('modalConfirm').onclick = () => {
                document.getElementById('confirmationModal').classList.add('hidden');
                handleDeleteLog(logId);
            };
            document.getElementById('modalCancel').onclick = () => document.getElementById('confirmationModal').classList.add('hidden');
        }
    });

    // --- INICIALIZAÇÃO ---
    loadDashboardData();
    setupWebSocket();
});

