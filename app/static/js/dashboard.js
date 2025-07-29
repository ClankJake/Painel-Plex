import { fetchAPI } from './utils.js';

document.addEventListener('DOMContentLoaded', () => {
    // --- ELEMENTOS E DADOS GLOBAIS ---
    const loadingIndicator = document.getElementById('loadingIndicator');
    const dashboardContainer = document.getElementById('dashboardContainer');
    const errorContainer = document.getElementById('errorContainer');
    const errorMessage = document.getElementById('errorMessage');
    const realtimeStatus = document.getElementById('realtime-status');

    const scriptTag = document.getElementById('dashboard-script');
    const summaryUrl = scriptTag.dataset.summaryUrl;
    const healthUrl = scriptTag.dataset.healthUrl;
    const i18n = {};
    for (const key in scriptTag.dataset) {
        if (key.startsWith('i18n')) {
            const i18nKey = key.charAt(4).toLowerCase() + key.slice(5);
            i18n[i18nKey] = scriptTag.dataset[key];
        }
    }

    let monthlyRevenueChart = null;
    let userStatusChart = null;

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
    
    // --- LÓGICA DE RENDERIZAÇÃO ---
    function createSummaryCard(icon, label, value, colorClass) {
        return `
            <div class="p-4 rounded-xl flex items-center gap-4 transition-all duration-300 ${colorClass}">
                <div class="p-3 bg-white/20 rounded-lg">${icon}</div>
                <div>
                    <p class="text-sm font-medium opacity-80">${label}</p>
                    <p class="text-2xl font-bold">${value}</p>
                </div>
            </div>
        `;
    }

    function renderSummaryCards(summary) {
        const summaryCardsContainer = document.getElementById('summaryCards');
        if (!summaryCardsContainer) return;

        summaryCardsContainer.innerHTML = `
            ${createSummaryCard('<svg class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" /></svg>', i18n.activeStreams, summary.active_streams, 'bg-blue-500 text-white')}
            ${createSummaryCard('<svg class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" /></svg>', i18n.totalUsers, summary.total_users, 'bg-purple-500 text-white')}
            ${createSummaryCard('<svg class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v.01" /></svg>', i18n.monthlyRevenue, formatCurrency(summary.monthly_revenue), 'bg-green-500 text-white')}
            ${createSummaryCard('<svg class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" /></svg>', i18n.upcomingRenewals, summary.upcoming_renewals, 'bg-yellow-500 text-white')}
        `;
    }

    function renderCharts(summary) {
        const colors = getChartColors();
        const revenueCanvas = document.getElementById('monthlyRevenueChart');
        const userStatusCanvas = document.getElementById('userStatusChart');

        // Atualiza ou cria o gráfico de receita
        if (revenueCanvas) {
            const dailyData = summary.daily_revenue || {};
            const daysInMonth = new Date(new Date().getFullYear(), new Date().getMonth() + 1, 0).getDate();
            const labels = Array.from({ length: daysInMonth }, (_, i) => i + 1);
            const data = labels.map(day => dailyData[day] || 0);

            if (monthlyRevenueChart) {
                monthlyRevenueChart.data.labels = labels;
                monthlyRevenueChart.data.datasets[0].data = data;
                monthlyRevenueChart.update('none'); // 'none' para evitar animações tremidas
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
        
        // Atualiza ou cria o gráfico de status de utilizador
        if (userStatusCanvas) {
            const data = [summary.active_users, summary.blocked_users];
            if (userStatusChart) {
                userStatusChart.data.datasets[0].data = data;
                userStatusChart.update('none'); // 'none' para evitar animações tremidas
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

    async function loadDashboardData() {
        loadingIndicator.style.display = 'block';
        dashboardContainer.classList.add('hidden');
        errorContainer.classList.add('hidden');

        try {
            const summaryPromise = fetchAPI(`${summaryUrl}?force=true`); // Força a atualização na carga inicial
            const healthPromise = fetchAPI(healthUrl);

            const [summaryData, healthData] = await Promise.all([summaryPromise, healthPromise]);

            if (summaryData.success) {
                renderSummaryCards(summaryData.summary);
                renderCharts(summaryData.summary);
            } else {
                throw new Error(summaryData.message);
            }

            if (healthData.success) {
                renderSystemHealth(healthData.health);
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
            console.log('Atualização recebida:', data);
            if (data.summary) {
                renderSummaryCards(data.summary);
                renderCharts(data.summary);
            }
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
    }

    // --- INICIALIZAÇÃO ---
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

    loadDashboardData();
    setupWebSocket(); // Inicia a conexão WebSocket
});
