import { fetchAPI, showToast, createModal } from './utils.js';

document.addEventListener('DOMContentLoaded', () => {
    // --- ELEMENTOS E DADOS GLOBAIS ---
    const loadingIndicator = document.getElementById('loadingIndicator');
    const dashboard = document.getElementById('financialDashboard');
    const errorContainer = document.getElementById('errorContainer');
    const errorMessage = document.getElementById('errorMessage');
    
    const scriptTag = document.getElementById('financial-script');
    const urls = {};
    const i18n = {};
    if (scriptTag) {
        for (const key in scriptTag.dataset) {
            if (key.startsWith('i18n')) {
                const i18nKey = key.charAt(4).toLowerCase() + key.slice(5);
                i18n[i18nKey] = scriptTag.dataset[key];
            } else {
                // Converte kebab-case para camelCase para as URLs
                const urlKey = key.replace(/-(\w)/g, (match, letter) => letter.toUpperCase());
                urls[urlKey] = scriptTag.dataset[key];
            }
        }
    }

    let revenueChart = null;
    let currentDate = new Date();
    let activeChartView = 'daily';
    let financialDataCache = null;

    // Elementos do DOM
    const renewalsFilter = document.getElementById('renewalsFilter');
    const upcomingRenewalsLabel = document.getElementById('upcomingRenewalsLabel');
    const renewalsList = document.getElementById('renewalsList');
    const prevMonthBtn = document.getElementById('prevMonthBtn');
    const nextMonthBtn = document.getElementById('nextMonthBtn');
    const monthLabel = document.getElementById('currentMonthLabel');
    const chartViewButtons = document.querySelectorAll('.chart-view-btn');
    const couponsListContainer = document.getElementById('couponsListContainer');
    const createCouponForm = document.getElementById('createCouponForm');

    // --- FUNÇÃO DE AJUDA PARA MODAL DE CONFIRMAÇÃO ---
    function showConfirmationModal({ title, message, confirmText, confirmClass, onConfirm }) {
        const modal = createModal('confirmationModal', title, `<p>${message}</p>`,
            `<button id="modalConfirm" class="btn ${confirmClass} w-full sm:w-auto">${confirmText}</button>
             <button id="modalCancel" class="btn bg-gray-200 text-gray-800 hover:bg-gray-300 dark:bg-gray-600 dark:text-gray-200 dark:hover:bg-gray-500 w-full sm:w-auto">${i18n.cancel}</button>`
        );
        modal.querySelector('#modalConfirm').onclick = () => { onConfirm(); modal.classList.add('hidden'); };
        modal.querySelector('#modalCancel').onclick = () => modal.classList.add('hidden');
    }

    // --- FUNÇÕES AUXILIARES ---
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
        return (value || 0).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
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

        if (summary.recent_transactions && summary.recent_transactions.length > 0) {
            transactionsList.innerHTML = summary.recent_transactions.map(tx => {
                let planDescription;
                // CORREÇÃO: Se a descrição já indica que foi via cupão, usa-a diretamente.
                if (tx.description && (tx.description.toLowerCase().includes('cupão') || tx.description.toLowerCase().includes('coupon'))) {
                    planDescription = tx.description;
                } else {
                    // Caso contrário, monta a descrição padrão.
                    planDescription = tx.screens > 0 ? `${tx.screens} Tela(s)` : 'Plano Padrão';
                }

                // O couponHtml continua útil para pagamentos que tiveram desconto mas não foram 100%
                const couponHtml = tx.coupon_code && !planDescription.toLowerCase().includes('cupão')
                    ? `<span class="px-2 py-0.5 text-xs font-medium rounded-full bg-indigo-100 text-indigo-800 dark:bg-indigo-900 dark:text-indigo-300" title="Cupom Utilizado: ${tx.coupon_code}">🏷️ Cupom</span>`
                    : '';
                
                return `
                <div class="flex items-center justify-between p-3 rounded-lg bg-gray-50 dark:bg-gray-700/50">
                    <div>
                        <div class="flex items-center gap-2 flex-wrap">
                            <p class="font-semibold text-gray-800 dark:text-gray-200">${tx.username}</p>
                            <span class="px-2 py-0.5 text-xs font-medium rounded-full bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-300">${planDescription}</span>
                            ${couponHtml}
                        </div>
                        <p class="text-xs text-gray-500 dark:text-gray-400 mt-1">${new Date(tx.created_at).toLocaleString('pt-BR')}</p>
                    </div>
                    <div class="font-mono text-green-600 dark:text-green-400 font-semibold">${formatCurrency(tx.value)}</div>
                </div>
            `}).join('');
        } else {
            transactionsList.innerHTML = `<p class="py-4 text-center text-gray-500">${i18n.noTransactions}</p>`;
        }

        if (summary.upcoming_expirations && summary.upcoming_expirations.length > 0) {
            renewalsList.innerHTML = summary.upcoming_expirations.map(user => {
                const daysText = user.days_left_text; 
                
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
    }

    async function loadFinancialData() {
        loadingIndicator.style.display = 'block';
        dashboard.classList.add('hidden');
        errorContainer.classList.add('hidden');

        const year = currentDate.getFullYear();
        const month = currentDate.getMonth() + 1;
        const renewalDays = renewalsFilter.value;

        try {
            const data = await fetchAPI(`${urls.financialSummaryUrl}?year=${year}&month=${month}&renewal_days=${renewalDays}`);
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

    // --- LÓGICA DE CUPÕES ---

    function renderCouponsTable(coupons) {
        if (!couponsListContainer) return;

        if (coupons.length === 0) {
            couponsListContainer.innerHTML = `<p class="text-gray-500 dark:text-gray-400">${'Nenhum cupão encontrado.'}</p>`;
            return;
        }

        couponsListContainer.innerHTML = `
            <table class="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
                <thead class="bg-gray-50 dark:bg-gray-700/50">
                    <tr>
                        <th class="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase">Código</th>
                        <th class="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase">Desconto</th>
                        <th class="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase">Usos</th>
                        <th class="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase">Validade</th>
                        <th class="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase">Ações</th>
                    </tr>
                </thead>
                <tbody class="bg-white dark:bg-gray-800 divide-y divide-gray-200 dark:divide-gray-700">
                    ${coupons.map(c => `
                        <tr>
                            <td class="px-4 py-2 whitespace-nowrap text-sm font-mono">${c.code}</td>
                            <td class="px-4 py-2 whitespace-nowrap text-sm">${c.discount_type === 'percentage' ? `${c.value}%` : formatCurrency(c.value)}</td>
                            <td class="px-4 py-2 whitespace-nowrap text-sm">${c.use_count} / ${c.max_uses}</td>
                            <td class="px-4 py-2 whitespace-nowrap text-sm">${c.expires_at ? new Date(c.expires_at).toLocaleDateString() : 'Nunca'}</td>
                            <td class="px-4 py-2 whitespace-nowrap text-sm flex items-center gap-2">
                                <label class="relative inline-flex items-center cursor-pointer">
                                  <input type="checkbox" data-action="toggle" data-id="${c.id}" class="sr-only peer" ${c.is_active ? 'checked' : ''}>
                                  <div class="w-9 h-5 bg-gray-200 rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all dark:border-gray-600 peer-checked:bg-green-600"></div>
                                </label>
                                <button data-action="delete" data-id="${c.id}" data-code="${c.code}" class="text-red-500 hover:text-red-700">
                                    <svg class="w-5 h-5" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z" clip-rule="evenodd" /></svg>
                                </button>
                            </td>
                        </tr>
                    `).join('')}
                </tbody>
            </table>
        `;
        couponsListContainer.querySelectorAll('button[data-action="delete"], input[data-action="toggle"]').forEach(el => {
            if (el.tagName === 'BUTTON') {
                el.onclick = () => handleCouponAction(el.dataset.action, el.dataset.id, el.dataset.code);
            } else {
                el.onchange = () => handleCouponAction(el.dataset.action, el.dataset.id);
            }
        });
    }

    async function loadCoupons() {
        try {
            const data = await fetchAPI(urls.couponsListUrl);
            if (data.success) {
                renderCouponsTable(data.coupons);
            }
        } catch (error) {
            showToast(error.message, 'error');
        }
    }

    async function handleCouponAction(action, id, code) {
        if (action === 'delete') {
            const message = `${i18n.confirmDeleteCoupon} <strong>${code}</strong>? ${i18n.actionCannotBeUndone}`;
            showConfirmationModal({
                title: 'Apagar Cupão', message: message, confirmText: i18n.confirmDeleteButton, confirmClass: 'bg-red-600 text-white',
                onConfirm: async () => {
                    try {
                        const result = await fetchAPI(urls.couponsDeleteBaseUrl.replace('0', id), 'POST');
                        showToast(result.message, result.success ? 'success' : 'error');
                        if (result.success) loadCoupons();
                    } catch (error) {
                        showToast(error.message, 'error');
                    }
                }
            });
        } else if (action === 'toggle') {
            try {
                const result = await fetchAPI(urls.couponsToggleBaseUrl.replace('0', id), 'POST');
                showToast(result.message, result.success ? 'success' : 'error');
            } catch (error) {
                showToast(error.message, 'error');
                loadCoupons();
            }
        }
    }

    if (createCouponForm) {
        createCouponForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const button = e.target.querySelector('button[type="submit"]');
            button.disabled = true;

            const payload = {
                code: document.getElementById('couponCode').value,
                discount_type: document.getElementById('discountType').value,
                value: document.getElementById('discountValue').value,
                max_uses: document.getElementById('maxUses').value,
                expires_at: document.getElementById('expiresAt').value || null
            };

            try {
                const result = await fetchAPI(urls.couponsCreateUrl, 'POST', payload);
                showToast(result.message, result.success ? 'success' : 'error');
                if (result.success) {
                    createCouponForm.reset();
                    loadCoupons();
                }
            } catch (error) {
                showToast(error.message, 'error');
            } finally {
                button.disabled = false;
            }
        });
    }
    
    // --- INICIALIZAÇÃO E EVENTOS ---

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

    const financialTabs = document.getElementById('financial-tabs');
    if (financialTabs) {
        financialTabs.addEventListener('click', (e) => {
            const button = e.target.closest('button');
            if (button && button.dataset.tab) {
                const tabId = button.dataset.tab;
                document.querySelectorAll('#financial-tabs .tab-button').forEach(btn => btn.classList.remove('active'));
                button.classList.add('active');

                document.querySelectorAll('#financial-tab-content .tab-content').forEach(content => content.classList.remove('active'));
                document.getElementById(`tab-${tabId}`).classList.add('active');

                if (tabId === 'coupons') {
                    loadCoupons();
                }
            }
        });
    }
    
    // Initial Load
    updateMonthLabel();
    loadFinancialData();
});
