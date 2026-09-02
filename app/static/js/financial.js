import { fetchAPI, showToast, createModal } from './utils.js';

document.addEventListener('DOMContentLoaded', () => {
    // ==========================================
    // 1. DADOS GLOBAIS E CONFIGURAÇÃO INICIAL
    // ==========================================
    const scriptTag = document.getElementById('financial-script');
    const urls = {};
    const i18n = {};
    
    if (scriptTag) {
        for (const key in scriptTag.dataset) {
            if (key.startsWith('i18n')) {
                const i18nKey = key.charAt(4).toLowerCase() + key.slice(5);
                i18n[i18nKey] = scriptTag.dataset[key];
            } else {
                const urlKey = key.replace(/-(\w)/g, (match, letter) => letter.toUpperCase());
                urls[urlKey] = scriptTag.dataset[key];
            }
        }
    }

    let revenueChart = null;
    let currentDate = new Date();
    let activeChartView = 'daily';
    let financialDataCache = null;
    let isFetchingData = false;

    // Cache de Elementos DOM Reutilizáveis
    const dom = {
        tabs: document.getElementById('financial-tabs'),
        loadingIndicator: document.getElementById('loadingIndicator'),
        dashboard: document.getElementById('financialDashboard'),
        errorContainer: document.getElementById('errorContainer'),
        errorMessage: document.getElementById('errorMessage'),
        renewalsFilter: document.getElementById('renewalsFilter'),
        upcomingRenewalsLabel: document.getElementById('upcomingRenewalsLabel'),
        renewalsList: document.getElementById('renewalsList'),
        prevMonthBtn: document.getElementById('prevMonthBtn'),
        nextMonthBtn: document.getElementById('nextMonthBtn'),
        monthLabel: document.getElementById('currentMonthLabel'),
        chartViewButtons: document.querySelectorAll('.chart-view-btn'),
        couponsListContainer: document.getElementById('couponsListContainer'),
        createCouponForm: document.getElementById('createCouponForm'),
        transactionsList: document.getElementById('transactionsList')
    };

    // ==========================================
    // 2. FUNÇÕES DE UTILIDADE
    // ==========================================
    
    const sanitizeHTML = (str) => {
        if (str == null || str === '') return '';
        const temp = document.createElement('div');
        temp.textContent = str;
        return temp.innerHTML;
    };

    const formatCurrency = (value) => {
        return (Number(value) || 0).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
    };

    const getChartColors = () => {
        const isDark = document.documentElement.classList.contains('dark');
        return {
            textColor: isDark ? '#9CA3AF' : '#4B5563', 
            gridColor: isDark ? 'rgba(255, 255, 255, 0.05)' : 'rgba(0, 0, 0, 0.05)',
            tooltipBg: isDark ? '#1F2937' : '#FFFFFF', 
            tooltipText: isDark ? '#F9FAFB' : '#111827', 
            barColor: isDark ? 'rgba(34, 197, 94, 0.8)' : 'rgba(34, 197, 94, 0.6)', 
            barHoverColor: 'rgba(22, 163, 74, 0.9)', 
        };
    };

    function showConfirmationModal({ title, message, confirmText, confirmClass, onConfirm }) {
        const btnCancelClass = "btn bg-gray-200 text-gray-800 dark:bg-gray-700 dark:text-gray-200 hover:bg-gray-300 dark:hover:bg-gray-600 transition-colors w-full sm:w-auto";
        const modal = createModal('confirmationModal', title, `<p class="text-gray-700 dark:text-gray-300">${message}</p>`,
            `<button type="button" id="modalConfirm" class="btn ${confirmClass} transition-colors w-full sm:w-auto">${confirmText}</button>
             <button type="button" id="modalCancel" class="${btnCancelClass}">${i18n.cancel || 'Cancelar'}</button>`
        );
        
        const confirmBtn = modal.querySelector('#modalConfirm');
        const cancelBtn = modal.querySelector('#modalCancel');
        
        confirmBtn.onclick = (e) => { e.preventDefault(); onConfirm(); modal.classList.add('hidden'); };
        cancelBtn.onclick = (e) => { e.preventDefault(); modal.classList.add('hidden'); };
    }

    // ==========================================
    // 3. GESTÃO DE ABAS (TABS)
    // ==========================================
    
    if (dom.tabs) {
        dom.tabs.addEventListener('click', (e) => {
            const button = e.target.closest('button[data-tab]');
            if (!button) return;
            
            const tabId = button.dataset.tab;
            
            // Alterna estilos ativos nos botões
            dom.tabs.querySelectorAll('button.tab-button').forEach(btn => btn.classList.remove('active'));
            button.classList.add('active');

            // Alterna conteúdos
            document.querySelectorAll('.tab-content').forEach(content => {
                content.classList.add('hidden');
                content.classList.remove('active');
            });
            
            const targetPanel = document.getElementById(`tab-${tabId}`);
            if (targetPanel) {
                targetPanel.classList.remove('hidden');
                // Pequeno delay para animação de fade (caso use CSS transitions)
                setTimeout(() => targetPanel.classList.add('active'), 10);
            }

            // Ações Específicas da Aba
            if (tabId === 'coupons') {
                loadCoupons();
            } else if (tabId === 'reports') {
                const today = new Date();
                const y = today.getFullYear();
                const m = String(today.getMonth() + 1).padStart(2, '0');
                const lastDate = new Date(y, today.getMonth() + 1, 0).getDate();
                
                const startInput = document.getElementById('startDate');
                const endInput = document.getElementById('endDate');
                if(startInput && !startInput.value) startInput.value = `${y}-${m}-01`;
                if(endInput && !endInput.value) endInput.value = `${y}-${m}-${lastDate}`;
            } else if (tabId === 'summary') {
                // Força o redesenho se a tela tiver mudado de tamanho
                if (financialDataCache) setTimeout(renderRevenueChart, 50); 
            }
        });
    }

    // ==========================================
    // 4. LÓGICA DO DASHBOARD (GRÁFICOS E TABELAS)
    // ==========================================

    function updateMonthLabel() {
        if (!dom.monthLabel || !dom.nextMonthBtn) return;
        
        const monthNames = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"];
        dom.monthLabel.textContent = `${monthNames[currentDate.getMonth()]} de ${currentDate.getFullYear()}`;

        const now = new Date();
        const isCurrentMonth = currentDate.getFullYear() === now.getFullYear() && currentDate.getMonth() === now.getMonth();
        dom.nextMonthBtn.disabled = isCurrentMonth;
        dom.nextMonthBtn.classList.toggle('opacity-50', isCurrentMonth);
        dom.nextMonthBtn.classList.toggle('cursor-not-allowed', isCurrentMonth);
    }

    function renderSummaryCards(summary) {
        document.getElementById('totalRevenue').textContent = formatCurrency(summary.total_revenue || 0);
        document.getElementById('salesCount').textContent = (summary.sales_count || 0).toLocaleString();
        document.getElementById('upcomingRenewals').textContent = (summary.upcoming_expirations?.length || 0).toLocaleString();
        
        if (dom.upcomingRenewalsLabel && dom.renewalsFilter) {
            dom.upcomingRenewalsLabel.textContent = `Renovações Próximas (${dom.renewalsFilter.value}d)`;
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

        if (revenueChart) revenueChart.destroy();

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
                        callbacks: { label: (context) => formatCurrency(context.parsed.y) }
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        ticks: { color: colors.textColor, callback: (value) => formatCurrency(value), font: { family: "'Inter', sans-serif", size: 11 } },
                        grid: { color: colors.gridColor, drawBorder: false }
                    },
                    x: {
                        ticks: { color: colors.textColor, font: { family: "'Inter', sans-serif", size: 11 } },
                        grid: { display: false, drawBorder: false }
                    }
                },
                interaction: { intersect: false, mode: 'index' },
            }
        });
    }

    function renderTables(summary) {
        // --- 1. Histórico de Transações ---
        if (dom.transactionsList) {
            if (summary.recent_transactions && summary.recent_transactions.length > 0) {
                dom.transactionsList.innerHTML = summary.recent_transactions.map(tx => {
                    const safeUsername = sanitizeHTML(tx.username);
                    const safeDesc = sanitizeHTML(tx.description);
                    const safeCoupon = sanitizeHTML(tx.coupon_code);
                    
                    let actualScreens = tx.screens || 0;
                    
                    // Se a transação for 0 (Renovação Manual), cruza os dados com a lista de usuários para achar o plano real
                    if (actualScreens === 0 && summary.upcoming_expirations) {
                        const matchedUser = summary.upcoming_expirations.find(u => u.username === tx.username);
                        if (matchedUser && matchedUser.screen_limit) {
                            actualScreens = matchedUser.screen_limit;
                        }
                    }

                    let planDescription = actualScreens > 0 ? `${actualScreens} Tela(s)` : 'Plano Padrão';
                    
                    // Se foi uma renovação manual e tem descrição customizada do admin
                    if ((!tx.screens || tx.screens === 0) && safeDesc && !safeDesc.toLowerCase().includes('cupão') && !safeDesc.toLowerCase().includes('coupon')) {
                        planDescription = actualScreens > 0 ? `Manual: ${actualScreens} Tela(s)` : safeDesc;
                    } else if (safeDesc && (safeDesc.toLowerCase().includes('cupão') || safeDesc.toLowerCase().includes('coupon'))) {
                        planDescription = safeDesc;
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
                            <button data-action="delete-tx" data-txid="${tx.txid}" title="Apagar Transação" class="p-2 text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg transition-colors">
                                <svg class="w-5 h-5 pointer-events-none" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z" clip-rule="evenodd" /></svg>
                            </button>
                        </div>
                    </div>
                `}).join('');
            } else {
                dom.transactionsList.innerHTML = `
                    <div class="flex flex-col items-center justify-center py-10 text-gray-400 dark:text-gray-500">
                        <svg class="w-12 h-12 mb-3 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path></svg>
                        <p class="font-medium">${i18n.noTransactions || 'Nenhuma transação registada neste mês.'}</p>
                    </div>`;
            }
        }

        // --- 2. Próximas Renovações ---
        if (dom.renewalsList) {
            if (summary.upcoming_expirations && summary.upcoming_expirations.length > 0) {
                dom.renewalsList.innerHTML = summary.upcoming_expirations.map(user => {
                    const safeUsername = sanitizeHTML(user.username);
                    
                    let daysLeft = parseInt(user.days_left, 10);
                    if (user.expiration_date) {
                        const parts = user.expiration_date.split('/'); 
                        if (parts.length === 3) {
                            const expDate = new Date(parts[2], parts[1] - 1, parts[0]);
                            const today = new Date();
                            today.setHours(0, 0, 0, 0);
                            daysLeft = Math.ceil((expDate.getTime() - today.getTime()) / (1000 * 3600 * 24));
                        }
                    }

                    let daysText = daysLeft > 1 ? `${daysLeft} dias restantes` : (daysLeft === 1 ? '1 dia restante' : 'Hoje');
                    if (daysLeft < 0) daysText = 'Expirado';

                    let textColor = 'text-yellow-600 dark:text-yellow-500';
                    let alertBadge = '';

                    if (daysLeft < 0) {
                        textColor = 'text-red-600 dark:text-red-500';
                    } else if (daysLeft === 0) {
                        textColor = 'text-orange-600 dark:text-orange-500 font-black';
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
                const days = parseInt(dom.renewalsFilter?.value) || 7;
                dom.renewalsList.innerHTML = `
                    <div class="flex flex-col items-center justify-center py-8 text-gray-400 dark:text-gray-500">
                        <svg class="w-12 h-12 mb-3 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14.828 14.828a4 4 0 01-5.656 0M9 10h.01M15 10h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
                        <p class="font-medium text-center px-4">${(i18n.noRenewalsInDays || 'Nenhum vencimento previsto para os próximos {days} dias.').replace('{days}', days)}</p>
                    </div>`;
            }
        }
    }

    // ==========================================
    // 5. EVENT DELEGATION GLOBAL E API
    // ==========================================

    async function loadFinancialData() {
        if (isFetchingData) return;
        isFetchingData = true;

        if (dom.loadingIndicator) dom.loadingIndicator.style.display = 'flex';
        if (dom.dashboard) dom.dashboard.classList.add('hidden');
        if (dom.errorContainer) dom.errorContainer.classList.add('hidden');

        const year = currentDate.getFullYear();
        const month = currentDate.getMonth() + 1;
        const renewalDays = dom.renewalsFilter?.value || 7;

        try {
            const data = await fetchAPI(`${urls.financialSummaryUrl}?year=${year}&month=${month}&renewal_days=${renewalDays}`);
            if (data.success) {
                financialDataCache = data;
                renderSummaryCards(data.summary);
                renderRevenueChart();
                renderTables(data.summary);
                if (dom.dashboard) dom.dashboard.classList.remove('hidden');
            } else {
                throw new Error(data.message);
            }
        } catch (error) {
            if (dom.errorMessage) dom.errorMessage.textContent = error.message;
            if (dom.errorContainer) dom.errorContainer.classList.remove('hidden');
        } finally {
            if (dom.loadingIndicator) dom.loadingIndicator.style.display = 'none';
            isFetchingData = false;
        }
    }

    // Delegação de Eventos para Transações (Apagar)
    if (dom.transactionsList) {
        dom.transactionsList.addEventListener('click', (e) => {
            const btn = e.target.closest('button[data-action="delete-tx"]');
            if (!btn) return;
            
            const txid = btn.dataset.txid;
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
                        if (result.success) loadFinancialData(); 
                    } catch (error) {
                        showToast(error.message, 'error');
                    }
                }
            });
        });
    }

    function renderCouponsTable(coupons) {
        if (!dom.couponsListContainer) return;

        if (!coupons || coupons.length === 0) {
            dom.couponsListContainer.innerHTML = `
                <div class="flex flex-col items-center justify-center py-10 text-gray-400 dark:text-gray-500 bg-white dark:bg-gray-800/50 rounded-xl border border-gray-200 dark:border-gray-700">
                    <svg class="w-12 h-12 mb-3 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 5v2m0 4v2m0 4v2M5 5a2 2 0 00-2 2v3a2 2 0 110 4v3a2 2 0 002 2h14a2 2 0 002-2v-3a2 2 0 110-4V7a2 2 0 00-2-2H5z"></path></svg>
                    <p class="font-medium">Nenhum cupão ativo ou criado.</p>
                </div>`;
            return;
        }

        dom.couponsListContainer.innerHTML = `
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
                                <td class="px-5 py-3 whitespace-nowrap text-sm text-gray-600 dark:text-gray-300">${c.use_count} / ${c.max_uses > 0 ? c.max_uses : '∞'}</td>
                                <td class="px-5 py-3 whitespace-nowrap text-sm text-gray-600 dark:text-gray-300">${expireDate}</td>
                                <td class="px-5 py-3 whitespace-nowrap text-sm flex items-center justify-end gap-3">
                                    <label class="relative inline-flex items-center cursor-pointer" title="Ativar/Desativar">
                                      <input type="checkbox" data-action="toggle-coupon" data-id="${c.id}" class="sr-only peer" ${c.is_active ? 'checked' : ''}>
                                      <div class="w-9 h-5 bg-gray-200 rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all dark:border-gray-600 peer-checked:bg-green-500 shadow-inner"></div>
                                    </label>
                                    <button data-action="delete-coupon" data-id="${c.id}" data-code="${safeCode}" title="Apagar Cupão" class="text-gray-400 hover:text-red-500 transition-colors p-1.5 rounded-lg hover:bg-red-50 dark:hover:bg-red-900/20">
                                        <svg class="w-5 h-5 pointer-events-none" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z" clip-rule="evenodd" /></svg>
                                    </button>
                                </td>
                            </tr>
                        `}).join('')}
                    </tbody>
                </table>
            </div>
        `;
    }

    async function loadCoupons() {
        try {
            const data = await fetchAPI(urls.couponsListUrl);
            if (data.success) renderCouponsTable(data.coupons);
        } catch (error) {
            showToast(error.message, 'error');
        }
    }

    // Delegação de Eventos para a Lista de Cupões
    if (dom.couponsListContainer) {
        dom.couponsListContainer.addEventListener('click', async (e) => {
            const deleteBtn = e.target.closest('button[data-action="delete-coupon"]');
            if (deleteBtn) {
                const { id, code } = deleteBtn.dataset;
                const safeCode = sanitizeHTML(code);
                const message = `${i18n.confirmDeleteCoupon || 'Tem a certeza que deseja apagar o cupão'} <strong class="text-gray-900 dark:text-white">${safeCode}</strong>? ${i18n.actionCannotBeUndone || 'Ação irreversível.'}`;
                
                showConfirmationModal({
                    title: 'Apagar Cupão', message: message, confirmText: i18n.confirmDeleteButton || 'Sim, Apagar', 
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
            }
        });

        dom.couponsListContainer.addEventListener('change', async (e) => {
            const toggleInput = e.target.closest('input[data-action="toggle-coupon"]');
            if (toggleInput) {
                try {
                    const result = await fetchAPI(urls.couponsToggleBaseUrl.replace('0', toggleInput.dataset.id), 'POST');
                    showToast(result.message, result.success ? 'success' : 'error');
                } catch (error) {
                    showToast(error.message, 'error');
                    loadCoupons(); // Reverte o switch caso dê erro
                }
            }
        });
    }

    if (dom.createCouponForm) {
        // 1. Converte o texto para maiúsculas fisicamente enquanto o utilizador digita
        const couponInput = document.getElementById('couponCode');
        if (couponInput) {
            couponInput.addEventListener('input', (e) => {
                e.target.value = e.target.value.toUpperCase();
            });
        }

        dom.createCouponForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const button = e.target.querySelector('button[type="submit"]');
            const originalText = button.textContent;
            
            button.disabled = true;
            button.textContent = 'A Criar...';

            const payload = {
                // 2. Garante que o valor enviado para a API está em maiúsculas (Caixa Alta)
                code: document.getElementById('couponCode').value.trim().toUpperCase(),
                discount_type: document.getElementById('discountType').value,
                value: document.getElementById('discountValue').value,
                // Vazio significa "sem limite", que o backend representa como 0.
                max_uses: document.getElementById('maxUses').value === '' ? 0 : Number(document.getElementById('maxUses').value),
                expires_at: document.getElementById('expiresAt').value || null
            };

            try {
                const result = await fetchAPI(urls.couponsCreateUrl, 'POST', payload);
                showToast(result.message, result.success ? 'success' : 'error');
                if (result.success) {
                    dom.createCouponForm.reset();
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
    
    // ==========================================
    // 6. EVENTOS ESTÁTICOS DE NAVEGAÇÃO
    // ==========================================

    if (dom.prevMonthBtn) {
        dom.prevMonthBtn.addEventListener('click', () => {
            currentDate.setMonth(currentDate.getMonth() - 1);
            updateMonthLabel();
            loadFinancialData();
        });
    }

    if (dom.nextMonthBtn) {
        dom.nextMonthBtn.addEventListener('click', () => {
            if (dom.nextMonthBtn.disabled) return;
            currentDate.setMonth(currentDate.getMonth() + 1);
            updateMonthLabel();
            loadFinancialData();
        });
    }

    if (dom.chartViewButtons) {
        dom.chartViewButtons.forEach(button => {
            button.addEventListener('click', () => {
                dom.chartViewButtons.forEach(btn => btn.classList.remove('active', 'bg-white', 'dark:bg-gray-700', 'shadow-sm'));
                button.classList.add('active', 'bg-white', 'dark:bg-gray-700', 'shadow-sm');
                activeChartView = button.dataset.chartView;
                renderRevenueChart();
            });
        });
    }

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

    if (dom.renewalsFilter) {
        dom.renewalsFilter.addEventListener('change', loadFinancialData);
    }

    const exportCsvBtn = document.getElementById('exportCsvBtn');
    // Tem de acompanhar CSV_MAX_INTERVALO_DIAS no servidor.
    const MAX_DIAS_EXPORTACAO = 366;
    if (exportCsvBtn) {
        exportCsvBtn.addEventListener('click', () => {
            const startDate = document.getElementById('startDate').value;
            const endDate = document.getElementById('endDate').value;
            if (!startDate || !endDate) {
                showToast('Por favor, selecione as datas de início e fim.', 'error');
                return;
            }

            // A exportação abre numa aba nova: sem estas verificações, um período
            // inválido só se manifestava como um JSON de erro em ecrã inteiro.
            const inicio = new Date(`${startDate}T00:00:00`);
            const fim = new Date(`${endDate}T00:00:00`);
            if (fim < inicio) {
                showToast('A data de fim não pode ser anterior à data de início.', 'error');
                return;
            }
            const dias = Math.round((fim - inicio) / 86400000);
            if (dias > MAX_DIAS_EXPORTACAO) {
                showToast(`O período não pode exceder ${MAX_DIAS_EXPORTACAO} dias. Exporte por partes.`, 'error');
                return;
            }

            const exportUrl = `${urls.exportCsvUrl}?start_date=${encodeURIComponent(startDate)}&end_date=${encodeURIComponent(endDate)}`;
            window.open(exportUrl, '_blank');
        });
    }
    
    // ==========================================
    // 7. ARRANQUE INICIAL
    // ==========================================
    updateMonthLabel();
    loadFinancialData();
});
