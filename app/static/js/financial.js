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

    // --- FUNÇÕES DE SEGURANÇA E UTILIDADE ---
    
    const sanitizeHTML = (str) => {
        if (str == null) return '';
        const temp = document.createElement('div');
        temp.textContent = str;
        return temp.innerHTML;
    };

    function showConfirmationModal({ title, message, confirmText, confirmClass, onConfirm }) {
        const btnCancelClass = "btn bg-gray-200 text-gray-800 dark:bg-gray-700 dark:text-gray-200 hover:bg-gray-300 dark:hover:bg-gray-600 transition-colors w-full sm:w-auto";
        const modal = createModal('confirmationModal', title, `<p class="text-gray-700 dark:text-gray-300">${message}</p>`,
            `<button id="modalConfirm" class="btn ${confirmClass} transition-colors w-full sm:w-auto">${confirmText}</button>
             <button id="modalCancel" class="${btnCancelClass}">${i18n.cancel || 'Cancelar'}</button>`
        );
        modal.querySelector('#modalConfirm').onclick = () => { onConfirm(); modal.classList.add('hidden'); };
        modal.querySelector('#modalCancel').onclick = () => modal.classList.add('hidden');
    }

    function formatCurrency(value) {
        return (value || 0).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
    }

    // --- GRÁFICO E INTERFACE ---

    function getChartColors() {
        const isDark = document.documentElement.classList.contains('dark');
        return {
            textColor: isDark ? '#9CA3AF' : '#4B5563', 
            gridColor: isDark ? 'rgba(255, 255, 255, 0.05)' : 'rgba(0, 0, 0, 0.05)',
            tooltipBg: isDark ? '#1F2937' : '#FFFFFF', 
            tooltipText: isDark ? '#F9FAFB' : '#111827', 
            barColor: isDark ? 'rgba(34, 197, 94, 0.8)' : 'rgba(34, 197, 94, 0.6)', 
            barHoverColor: 'rgba(22, 163, 74, 0.9)', 
        };
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
            labels = Object.keys(weeklyData).map(k => `Semana ${k}`);
            data = Object.values(weeklyData);
        } else { 
            const dailyData = financialDataCache.summary.daily_revenue || {};
            const daysInMonth = new Date(queryDate.year, queryDate.month, 0).getDate();
            labels = Array.from({ length: daysInMonth }, (_, i) => `${i + 1}`);
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
                    label: i18n.revenueLabel || 'Receita',
                    data: data,
                    backgroundColor: colors.barColor,
                    hoverBackgroundColor: colors.barHoverColor,
                    borderRadius: 6,
                    borderSkipped: false,
                    barPercentage: 0.7,
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: colors.tooltipBg,
                        titleColor: colors.tooltipText,
                        bodyColor: colors.tooltipText,
                        borderColor: colors.gridColor,
                        borderWidth: 1,
                        padding: 10,
                        displayColors: false,
                        callbacks: {
                            label: (context) => formatCurrency(context.parsed.y)
                        }
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        ticks: { 
                            color: colors.textColor,
                            callback: (value) => formatCurrency(value),
                            font: { family: "'Inter', sans-serif", size: 11 }
                        },
                        grid: { color: colors.gridColor, drawBorder: false }
                    },
                    x: {
                        ticks: { 
                            color: colors.textColor,
                            font: { family: "'Inter', sans-serif", size: 11 }
                        },
                        grid: { display: false, drawBorder: false }
                    }
                },
                interaction: {
                    intersect: false,
                    mode: 'index',
                },
            }
        });
    }

    // --- RENDERIZAÇÃO DE TABELAS ---

    function renderTables(summary) {
        const transactionsList = document.getElementById('transactionsList');

        // Renderização do Histórico de Transações
        if (summary.recent_transactions && summary.recent_transactions.length > 0) {
            transactionsList.innerHTML = summary.recent_transactions.map(tx => {
                const safeUsername = sanitizeHTML(tx.username);
                const safeDesc = sanitizeHTML(tx.description);
                const safeCoupon = sanitizeHTML(tx.coupon_code);
                
                let planDescription;
                if (safeDesc && (safeDesc.toLowerCase().includes('cupão') || safeDesc.toLowerCase().includes('coupon'))) {
                    planDescription = safeDesc;
                } else {
                    planDescription = tx.screens > 0 ? `${tx.screens} Tela(s)` : 'Plano Padrão';
                }

                const couponHtml = safeCoupon && !planDescription.toLowerCase().includes('cupão')
                    ? `<span class="px-2.5 py-0.5 text-xs font-bold rounded-full bg-indigo-100 text-indigo-800 dark:bg-indigo-900/60 dark:text-indigo-300 border border-indigo-200 dark:border-indigo-800" title="Cupom Utilizado: ${safeCoupon}">🏷️ ${safeCoupon}</span>`
                    : '';
                
                return `
                <div class="flex items-center justify-between p-3.5 rounded-xl bg-gray-50 dark:bg-gray-800/50 border border-gray-100 dark:border-gray-700/50 hover:shadow-sm transition-shadow">
                    <div>
                        <div class="flex items-center gap-2.5 flex-wrap mb-1">
                            <p class="font-bold text-gray-900 dark:text-gray-100">${safeUsername}</p>
                            <span class="px-2.5 py-0.5 text-xs font-bold rounded-full bg-blue-100 text-blue-800 dark:bg-blue-900/60 dark:text-blue-300 border border-blue-200 dark:border-blue-800">${planDescription}</span>
                            ${couponHtml}
                        </div>
                        <p class="text-xs text-gray-500 dark:text-gray-400 font-medium">${new Date(tx.created_at).toLocaleString('pt-BR')}</p>
                    </div>
                    <div class="flex items-center gap-4">
                        <div class="font-mono text-green-600 dark:text-green-400 font-black text-lg">${formatCurrency(tx.value)}</div>
                        <button data-txid="${tx.txid}" title="Apagar Transação" class="delete-transaction-btn p-2 text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg transition-colors">
                            <svg class="w-5 h-5" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z" clip-rule="evenodd" /></svg>
                        </button>
                    </div>
                </div>
            `}).join('');
            
            transactionsList.querySelectorAll('.delete-transaction-btn').forEach(button => {
                button.addEventListener('click', handleDeleteTransaction);
            });
        } else {
            // Estado Vazio
            transactionsList.innerHTML = `
                <div class="flex flex-col items-center justify-center py-10 text-gray-400 dark:text-gray-500">
                    <svg class="w-12 h-12 mb-3 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path></svg>
                    <p class="font-medium">${i18n.noTransactions || 'Nenhuma transação registada neste mês.'}</p>
                </div>`;
        }

        // Renderização de Próximas Renovações
        if (summary.upcoming_expirations && summary.upcoming_expirations.length > 0) {
            renewalsList.innerHTML = summary.upcoming_expirations.map(user => {
                const safeUsername = sanitizeHTML(user.username);
                const daysText = sanitizeHTML(user.days_left_text) || 'Hoje'; 
                
                const daysLeft = parseInt(user.days_left, 10);
                let textColor = 'text-yellow-600 dark:text-yellow-500';
                let alertBadge = '';

                if (daysLeft < 0) {
                    textColor = 'text-red-600 dark:text-red-500';
                } else if (daysLeft === 0) {
                    textColor = 'text-orange-600 dark:text-orange-500 font-black animate-pulse';
                    alertBadge = `<span class="ml-2 px-2 py-0.5 text-[10px] uppercase font-black tracking-widest rounded-full bg-orange-100 text-orange-800 dark:bg-orange-900/60 dark:text-orange-300 border border-orange-200 dark:border-orange-800 animate-pulse">Vence Hoje!</span>`;
                } else if (daysLeft > 15) {
                    textColor = 'text-gray-500 dark:text-gray-400';
                }

                const planDescription = user.screen_limit > 0 ? `${user.screen_limit} Tela(s)` : 'Padrão';

                return `
                    <div class="flex items-center justify-between p-3.5 rounded-xl bg-gray-50 dark:bg-gray-800/50 border border-gray-100 dark:border-gray-700/50 hover:shadow-sm transition-shadow">
                        <div>
                             <div class="flex items-center gap-2.5 mb-1">
                                <p class="font-bold text-gray-900 dark:text-gray-100">${safeUsername}</p>
                                <span class="px-2.5 py-0.5 text-xs font-bold rounded-full bg-blue-100 text-blue-800 dark:bg-blue-900/60 dark:text-blue-300 border border-blue-200 dark:border-blue-800">${planDescription}</span>
                                ${alertBadge}
                            </div>
                            <p class="text-xs text-gray-500 dark:text-gray-400 font-medium"><svg class="w-3 h-3 inline mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"></path></svg>${sanitizeHTML(user.expiration_date)}</p>
                        </div>
                        <div class="font-black ${textColor}">${daysText}</div>
                    </div>
                `;
            }).join('');
        } else {
            const days = parseInt(renewalsFilter.value) || 7;
            renewalsList.innerHTML = `
                <div class="flex flex-col items-center justify-center py-8 text-gray-400 dark:text-gray-500">
                    <svg class="w-12 h-12 mb-3 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14.828 14.828a4 4 0 01-5.656 0M9 10h.01M15 10h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
                    <p class="font-medium text-center px-4">${(i18n.noRenewalsInDays || 'Nenhum vencimento previsto para os próximos {days} dias.').replace('{days}', days)}</p>
                </div>`;
        }
    }

    async function loadFinancialData() {
        loadingIndicator.style.display = 'flex';
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

    function handleDeleteTransaction(event) {
        const button = event.currentTarget;
        const txid = button.dataset.txid;
        if (!txid) return;
    
        showConfirmationModal({
            title: i18n.confirmDeleteTransaction || 'Apagar Transação',
            message: i18n.actionCannotBeUndone || 'Esta ação não pode ser desfeita e removerá o registo financeiro permanentemente.',
            confirmText: i18n.confirmDeleteButton || 'Sim, Apagar',
            confirmClass: 'bg-red-600 hover:bg-red-500 text-white',
            onConfirm: async () => {
                try {
                    const url = urls.deleteTransactionBaseUrl.replace('__TXID__', txid);
                    const result = await fetchAPI(url, 'POST');
                    showToast(result.message, result.success ? 'success' : 'error');
                    if (result.success) {
                        loadFinancialData(); 
                    }
                } catch (error) {
                    showToast(error.message, 'error');
                }
            }
        });
    }

    // --- LÓGICA DE CUPÕES ---

    function renderCouponsTable(coupons) {
        if (!couponsListContainer) return;

        if (coupons.length === 0) {
            couponsListContainer.innerHTML = `
                <div class="flex flex-col items-center justify-center py-10 text-gray-400 dark:text-gray-500 bg-white dark:bg-gray-800/50 rounded-xl border border-gray-200 dark:border-gray-700">
                    <svg class="w-12 h-12 mb-3 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 5v2m0 4v2m0 4v2M5 5a2 2 0 00-2 2v3a2 2 0 110 4v3a2 2 0 002 2h14a2 2 0 002-2v-3a2 2 0 110-4V7a2 2 0 00-2-2H5z"></path></svg>
                    <p class="font-medium">Nenhum cupão ativo ou criado.</p>
                </div>`;
            return;
        }

        couponsListContainer.innerHTML = `
            <div class="overflow-x-auto rounded-xl border border-gray-200 dark:border-gray-700 shadow-sm">
                <table class="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
                    <thead class="bg-gray-50 dark:bg-gray-900/80">
                        <tr>
                            <th class="px-5 py-3 text-left text-xs font-bold text-gray-500 dark:text-gray-400 uppercase tracking-wider">Código</th>
                            <th class="px-5 py-3 text-left text-xs font-bold text-gray-500 dark:text-gray-400 uppercase tracking-wider">Desconto</th>
                            <th class="px-5 py-3 text-left text-xs font-bold text-gray-500 dark:text-gray-400 uppercase tracking-wider">Usos</th>
                            <th class="px-5 py-3 text-left text-xs font-bold text-gray-500 dark:text-gray-400 uppercase tracking-wider">Validade</th>
                            <th class="px-5 py-3 text-right text-xs font-bold text-gray-500 dark:text-gray-400 uppercase tracking-wider">Ações</th>
                        </tr>
                    </thead>
                    <tbody class="bg-white dark:bg-gray-800 divide-y divide-gray-200 dark:divide-gray-700">
                        ${coupons.map(c => {
                            const safeCode = sanitizeHTML(c.code);
                            const discountDisplay = c.discount_type === 'percentage' ? `${c.value}%` : formatCurrency(c.value);
                            const expireDate = c.expires_at ? new Date(c.expires_at).toLocaleDateString('pt-BR') : 'Sem Validade';
                            
                            return `
                            <tr class="hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors">
                                <td class="px-5 py-3 whitespace-nowrap text-sm font-bold font-mono text-gray-900 dark:text-white">${safeCode}</td>
                                <td class="px-5 py-3 whitespace-nowrap text-sm font-medium text-green-600 dark:text-green-400 bg-green-50 dark:bg-transparent">${discountDisplay}</td>
                                <td class="px-5 py-3 whitespace-nowrap text-sm text-gray-600 dark:text-gray-300">${c.use_count} / ${c.max_uses || '∞'}</td>
                                <td class="px-5 py-3 whitespace-nowrap text-sm text-gray-600 dark:text-gray-300">${expireDate}</td>
                                <td class="px-5 py-3 whitespace-nowrap text-sm flex items-center justify-end gap-3">
                                    <label class="relative inline-flex items-center cursor-pointer" title="Ativar/Desativar">
                                      <input type="checkbox" data-action="toggle" data-id="${c.id}" class="sr-only peer" ${c.is_active ? 'checked' : ''}>
                                      <div class="w-9 h-5 bg-gray-200 rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all dark:border-gray-600 peer-checked:bg-green-500 shadow-inner"></div>
                                    </label>
                                    <button data-action="delete" data-id="${c.id}" data-code="${safeCode}" title="Apagar Cupão" class="text-gray-400 hover:text-red-500 transition-colors p-1.5 rounded-lg hover:bg-red-50 dark:hover:bg-red-900/20">
                                        <svg class="w-5 h-5" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z" clip-rule="evenodd" /></svg>
                                    </button>
                                </td>
                            </tr>
                        `}).join('')}
                    </tbody>
                </table>
            </div>
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
            const safeCode = sanitizeHTML(code);
            const message = `${i18n.confirmDeleteCoupon || 'Tem a certeza que deseja apagar o cupão'} <strong class="text-gray-900 dark:text-white">${safeCode}</strong>? ${i18n.actionCannotBeUndone || 'Ação irreversível.'}`;
            showConfirmationModal({
                title: 'Apagar Cupão', 
                message: message, 
                confirmText: i18n.confirmDeleteButton || 'Sim, Apagar', 
                confirmClass: 'bg-red-600 hover:bg-red-500 text-white',
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
            const originalText = button.textContent;
            
            button.disabled = true;
            button.textContent = 'A Criar...';

            const payload = {
                code: document.getElementById('couponCode').value.trim(),
                discount_type: document.getElementById('discountType').value,
                value: document.getElementById('discountValue').value,
                max_uses: document.getElementById('maxUses').value || 0,
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
                button.textContent = originalText;
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
            chartViewButtons.forEach(btn => btn.classList.remove('active', 'bg-white', 'dark:bg-gray-700', 'shadow-sm'));
            button.classList.add('active', 'bg-white', 'dark:bg-gray-700', 'shadow-sm');
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
            revenueChart.options.plugins.tooltip.titleColor = colors.tooltipText;
            revenueChart.options.plugins.tooltip.bodyColor = colors.tooltipText;
            revenueChart.data.datasets[0].backgroundColor = colors.barColor;
            revenueChart.data.datasets[0].hoverBackgroundColor = colors.barHoverColor;
            revenueChart.update();
        }
    });

    renewalsFilter.addEventListener('change', loadFinancialData);

    // ==========================================
    // GESTÃO DE ABAS (TABS) - BLINDADO
    // ==========================================
    const tabButtons = document.querySelectorAll('#financial-tabs button[data-tab]');
    const tabContents = document.querySelectorAll('.tab-content');

    if (tabButtons.length > 0) {
        tabButtons.forEach(button => {
            button.addEventListener('click', (e) => {
                e.preventDefault(); 
                const tabId = button.dataset.tab;
                
                // 1. Limpa as classes de ativo de TODOS os botões
                tabButtons.forEach(btn => {
                    btn.classList.remove('active', 'bg-white', 'dark:bg-gray-700', 'text-gray-900', 'dark:text-white', 'shadow-sm', 'ring-1', 'ring-gray-200', 'dark:ring-gray-600');
                    btn.classList.add('text-gray-500', 'dark:text-gray-400');
                });
                
                // 2. Adiciona as classes de ativo APENAS ao botão clicado
                button.classList.remove('text-gray-500', 'dark:text-gray-400');
                button.classList.add('active', 'bg-white', 'dark:bg-gray-700', 'text-gray-900', 'dark:text-white', 'shadow-sm', 'ring-1', 'ring-gray-200', 'dark:ring-gray-600');

                // 3. Oculta todos os painéis de conteúdo
                tabContents.forEach(content => {
                    content.classList.add('hidden');
                });
                
                // 4. Mostra o painel de conteúdo alvo
                const targetPanel = document.getElementById(`tab-${tabId}`);
                if (targetPanel) {
                    targetPanel.classList.remove('hidden');
                }

                // 5. Aciona as ações específicas de cada aba
                if (tabId === 'coupons') {
                    loadCoupons();
                } else if (tabId === 'reports') {
                    const today = new Date();
                    const y = today.getFullYear();
                    const m = String(today.getMonth() + 1).padStart(2, '0');
                    const lastDate = new Date(y, today.getMonth() + 1, 0).getDate();
                    
                    document.getElementById('startDate').value = `${y}-${m}-01`;
                    document.getElementById('endDate').value = `${y}-${m}-${lastDate}`;
                } else if (tabId === 'summary') {
                    if (financialDataCache) renderRevenueChart();
                }
            });
        });
    }

    const exportCsvBtn = document.getElementById('exportCsvBtn');
    if (exportCsvBtn) {
        exportCsvBtn.addEventListener('click', () => {
            const startDate = document.getElementById('startDate').value;
            const endDate = document.getElementById('endDate').value;
            if (!startDate || !endDate) {
                showToast('Por favor, selecione as datas de início e fim.', 'error');
                return;
            }
            const exportUrl = `${urls.exportCsvUrl}?start_date=${startDate}&end_date=${endDate}`;
            window.open(exportUrl, '_blank');
        });
    }
    
    // Initial Load
    updateMonthLabel();
    loadFinancialData();
});
