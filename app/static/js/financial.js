import { fetchAPI } from './utils.js';

document.addEventListener('DOMContentLoaded', () => {
    const loadingIndicator = document.getElementById('loadingIndicator');
    const dashboard = document.getElementById('financialDashboard');
    const errorContainer = document.getElementById('errorContainer');
    const errorMessage = document.getElementById('errorMessage');
    
    const scriptTag = document.getElementById('financial-script');
    const financialSummaryUrl = scriptTag.dataset.financialSummaryUrl;
    const i18n = {};
    for (const key in scriptTag.dataset) {
        if (key.startsWith('i18n')) {
            const i18nKey = key.charAt(4).toLowerCase() + key.slice(5);
            i18n[i18nKey] = scriptTag.dataset[key];
        }
    }

    let revenueChart = null;
    let currentDate = new Date();
    let activeChartView = 'daily'; // 'daily' ou 'weekly'
    let financialDataCache = null;

    // Elementos do DOM
    const renewalsFilter = document.getElementById('renewalsFilter');
    const upcomingRenewalsLabel = document.getElementById('upcomingRenewalsLabel');
    const renewalsList = document.getElementById('renewalsList');
    const prevMonthBtn = document.getElementById('prevMonthBtn');
    const nextMonthBtn = document.getElementById('nextMonthBtn');
    const monthLabel = document.getElementById('currentMonthLabel');
    const chartViewButtons = document.querySelectorAll('.chart-view-btn');

    function getChartColors() {
        const isDark = document.documentElement.classList.contains('dark');
        return {
            textColor: isDark ? '#E5E7EB' : '#1F2937',
            gridColor: isDark ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.1)',
            tooltipBg: isDark ? '#1F2937' : '#FFFFFF',
            barColor: 'rgba(34, 197, 94, 0.6)',
            barBorderColor: 'rgba(22, 163, 74, 1)',
        };
    }

    function formatCurrency(value) {
        return value.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
    }

    function updateMonthLabel() {
        const monthNames = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"];
        monthLabel.textContent = `${monthNames[currentDate.getMonth()]} de ${currentDate.getFullYear()}`;

        const now = new Date();
        const isCurrentMonth = currentDate.getFullYear() === now.getFullYear() && currentDate.getMonth() === now.getMonth();
        nextMonthBtn.disabled = isCurrentMonth;
        nextMonthBtn.classList.toggle('opacity-50', isCurrentMonth);
        nextMonthBtn.classList.toggle('cursor-not-allowed', isCurrentMonth);
    }

    function renderSummaryCards(summary) {
        document.getElementById('totalRevenue').textContent = formatCurrency(summary.total_revenue || 0);
        document.getElementById('salesCount').textContent = (summary.sales_count || 0).toLocaleString();
        document.getElementById('upcomingRenewals').textContent = (summary.upcoming_expirations?.length || 0).toLocaleString();
        
        const days = renewalsFilter.value;
        if (upcomingRenewalsLabel) {
            upcomingRenewalsLabel.textContent = `Renovações Próximas (${days}d)`;
        }
    }

    function renderRevenueChart() {
        if (!financialDataCache) return;

        const canvas = document.getElementById('revenueChart');
        if (!canvas) return;

        const colors = getChartColors();
        const queryDate = financialDataCache.query_date;
        
        let labels, data;

        if (activeChartView === 'weekly') {
            const weeklyData = financialDataCache.summary.weekly_revenue || {};
            labels = Object.keys(weeklyData);
            data = Object.values(weeklyData);
        } else { // daily view
            const dailyData = financialDataCache.summary.daily_revenue || {};
            const daysInMonth = new Date(queryDate.year, queryDate.month, 0).getDate();
            labels = Array.from({ length: daysInMonth }, (_, i) => i + 1);
            data = labels.map(day => dailyData[day] || 0);
        }

        if (revenueChart) {
            revenueChart.destroy();
        }

        revenueChart = new Chart(canvas.getContext('2d'), {
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
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: colors.tooltipBg,
                        titleColor: colors.textColor,
                        bodyColor: colors.textColor,
                        callbacks: {
                            label: (context) => `${context.dataset.label}: ${formatCurrency(context.parsed.y)}`
                        }
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        ticks: { 
                            color: colors.textColor,
                            callback: (value) => formatCurrency(value)
                        },
                        grid: { color: colors.gridColor }
                    },
                    x: {
                        ticks: { color: colors.textColor },
                        grid: { display: false }
                    }
                }
            }
        });
    }

    function renderTables(summary) {
        const transactionsList = document.getElementById('transactionsList');

        // Lista de Transações Recentes
        if (summary.recent_transactions && summary.recent_transactions.length > 0) {
            transactionsList.innerHTML = summary.recent_transactions.map(tx => {
                let planDescription = tx.description || '';
                if (!planDescription) {
                    planDescription = tx.screens > 0 ? `${tx.screens} Tela(s)` : 'Plano Padrão';
                }
                
                return `
                <div class="flex items-center justify-between p-3 rounded-lg bg-gray-50 dark:bg-gray-700/50">
                    <div>
                        <p class="font-semibold text-gray-800 dark:text-gray-200">${tx.username}</p>
                        <p class="text-xs text-gray-500 dark:text-gray-400">${new Date(tx.created_at).toLocaleString('pt-BR')} - <span class="font-medium text-gray-600 dark:text-gray-300">${planDescription}</span></p>
                    </div>
                    <div class="font-mono text-green-600 dark:text-green-400 font-semibold">${formatCurrency(tx.value)}</div>
                </div>
            `}).join('');
        } else {
            transactionsList.innerHTML = `<p class="py-4 text-center text-gray-500">${i18n.noTransactions}</p>`;
        }

        // --- INÍCIO DA ALTERAÇÃO ---
        // Lista de Próximas Renovações
        if (summary.upcoming_expirations && summary.upcoming_expirations.length > 0) {
            renewalsList.innerHTML = summary.upcoming_expirations.map(user => {
                // Usa o texto pré-formatado do backend
                const daysText = user.days_left_text; 
                
                // A lógica da cor continua baseada no valor numérico
                let textColor = 'text-yellow-600 dark:text-yellow-400';
                if (user.days_left < 0) {
                    textColor = 'text-red-600 dark:text-red-400';
                } else if (user.days_left > 15) {
                    textColor = 'text-gray-500 dark:text-gray-400';
                }

                const planDescription = user.screen_limit > 0 ? `${user.screen_limit} Tela(s)` : 'Padrão';

                return `
                    <div class="flex items-center justify-between p-3 rounded-lg bg-gray-50 dark:bg-gray-700/50">
                        <div>
                             <div class="flex items-center gap-2">
                                <p class="font-semibold text-gray-800 dark:text-gray-200">${user.username}</p>
                                <span class="px-2 py-0.5 text-xs font-medium rounded-full bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-300">${planDescription}</span>
                            </div>
                            <p class="text-xs text-gray-500 dark:text-gray-400">${user.expiration_date}</p>
                        </div>
                        <div class="font-semibold ${textColor}">${daysText}</div>
                    </div>
                `;
            }).join('');
        } else {
            const days = renewalsFilter.value;
            renewalsList.innerHTML = `<p class="py-4 text-center text-gray-500">${i18n.noRenewalsInDays.replace('{days}', days)}</p>`;
        }
        // --- FIM DA ALTERAÇÃO ---
    }

    async function loadFinancialData() {
        loadingIndicator.style.display = 'block';
        dashboard.classList.add('hidden');
        errorContainer.classList.add('hidden');

        const year = currentDate.getFullYear();
        const month = currentDate.getMonth() + 1; // JS month is 0-indexed
        const renewalDays = renewalsFilter.value;

        try {
            const data = await fetchAPI(`${financialSummaryUrl}?year=${year}&month=${month}&renewal_days=${renewalDays}`);
            if (data.success) {
                financialDataCache = data;
                renderSummaryCards(data.summary);
                renderRevenueChart();
                renderTables(data.summary);
                dashboard.classList.remove('hidden');
            } else {
                throw new Error(data.message);
            }
        } catch (error) {
            errorMessage.textContent = error.message;
            errorContainer.classList.remove('hidden');
        } finally {
            loadingIndicator.style.display = 'none';
        }
    }

    prevMonthBtn.addEventListener('click', () => {
        currentDate.setMonth(currentDate.getMonth() - 1);
        updateMonthLabel();
        loadFinancialData();
    });

    nextMonthBtn.addEventListener('click', () => {
        if (nextMonthBtn.disabled) return;
        currentDate.setMonth(currentDate.getMonth() + 1);
        updateMonthLabel();
        loadFinancialData();
    });

    chartViewButtons.forEach(button => {
        button.addEventListener('click', () => {
            chartViewButtons.forEach(btn => btn.classList.remove('active'));
            button.classList.add('active');
            activeChartView = button.dataset.chartView;
            renderRevenueChart();
        });
    });

    window.addEventListener('themeChanged', () => {
        if (revenueChart) {
            const colors = getChartColors();
            revenueChart.options.scales.y.ticks.color = colors.textColor;
            revenueChart.options.scales.y.grid.color = colors.gridColor;
            revenueChart.options.scales.x.ticks.color = colors.textColor;
            revenueChart.options.plugins.tooltip.backgroundColor = colors.tooltipBg;
            revenueChart.options.plugins.tooltip.titleColor = colors.textColor;
            revenueChart.options.plugins.tooltip.bodyColor = colors.textColor;
            revenueChart.update();
        }
    });

    renewalsFilter.addEventListener('change', loadFinancialData);
    
    // Initial Load
    updateMonthLabel();
    loadFinancialData();
});
