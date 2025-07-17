import { fetchAPI } from './utils.js';

document.addEventListener('DOMContentLoaded', () => {
    const loadingIndicator = document.getElementById('loadingIndicator');
    const dashboardContainer = document.getElementById('dashboardContainer');
    const errorContainer = document.getElementById('errorContainer');
    const errorMessage = document.getElementById('errorMessage');
    const refreshButton = document.getElementById('refreshButton');

    const scriptTag = document.getElementById('dashboard-script');
    const summaryUrl = scriptTag.dataset.summaryUrl;
    const i18n = {};
    for (const key in scriptTag.dataset) {
        if (key.startsWith('i18n')) {
            const i18nKey = key.charAt(4).toLowerCase() + key.slice(5);
            i18n[i18nKey] = scriptTag.dataset[key];
        }
    }

    let monthlyRevenueChart = null;
    let userStatusChart = null;

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

        if (monthlyRevenueChart) monthlyRevenueChart.destroy();
        if (revenueCanvas) {
            const dailyData = summary.daily_revenue || {};
            const daysInMonth = new Date(new Date().getFullYear(), new Date().getMonth() + 1, 0).getDate();
            const labels = Array.from({ length: daysInMonth }, (_, i) => i + 1);
            const data = labels.map(day => dailyData[day] || 0);

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

        if (userStatusChart) userStatusChart.destroy();
        if (userStatusCanvas) {
            userStatusChart = new Chart(userStatusCanvas.getContext('2d'), {
                type: 'doughnut',
                data: {
                    labels: [i18n.activeUsersLabel, i18n.blockedUsersLabel],
                    datasets: [{
                        data: [summary.active_users, summary.blocked_users],
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

    async function loadDashboardData(force = false) {
        loadingIndicator.style.display = 'block';
        dashboardContainer.classList.add('hidden');
        errorContainer.classList.add('hidden');

        if (refreshButton) {
            refreshButton.disabled = true;
            refreshButton.querySelector('svg')?.classList.add('animate-spin');
        }

        try {
            const data = await fetchAPI(`${summaryUrl}?force=${force}`);
            if (data.success) {
                renderSummaryCards(data.summary);
                renderCharts(data.summary);
                dashboardContainer.classList.remove('hidden');
            } else {
                throw new Error(data.message);
            }
        } catch (error) {
            errorMessage.textContent = error.message;
            errorContainer.classList.remove('hidden');
        } finally {
            loadingIndicator.style.display = 'none';
            if (refreshButton) {
                refreshButton.disabled = false;
                refreshButton.querySelector('svg')?.classList.remove('animate-spin');
            }
        }
    }

    if (refreshButton) {
        refreshButton.addEventListener('click', () => loadDashboardData(true));
    }
    
    window.addEventListener('themeChanged', () => {
        if(dashboardContainer.classList.contains('hidden')) return;
        
        const summary = { /* Precisamos de uma forma de obter os dados novamente ou do cache */ };
        // Para simplificar, vamos apenas recarregar os dados
        loadDashboardData();
    });

    loadDashboardData();
});
