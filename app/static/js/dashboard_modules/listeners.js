// Anexa todos os event listeners da página
import { dom, state } from './config.js';
import { renderCharts, openUserSelectionModal } from './ui.js';
import { getChartColors } from './formatters.js';
import {
    updateSendButtonText,
    handleSendBulkNotification,
    handleClearAllLogs,
    handleDeleteLog
} from './handlers.js';
import { createModal } from '../utils.js';

export function attachEventListeners() {
    const { i18n } = state;

    // Listener de mudança de tema
    window.addEventListener('themeChanged', () => {
       if(dom.dashboardContainer.classList.contains('hidden')) return;

        const colors = getChartColors();
        if (state.monthlyRevenueChart) {
            state.monthlyRevenueChart.options.scales.y.ticks.color = colors.textColor;
            state.monthlyRevenueChart.options.scales.y.grid.color = colors.gridColor;
            state.monthlyRevenueChart.options.scales.x.ticks.color = colors.textColor;
            state.monthlyRevenueChart.update();
        }
        if (state.userStatusChart) {
            state.userStatusChart.data.datasets[0].borderColor = colors.tooltipBg;
            state.userStatusChart.options.plugins.legend.labels.color = colors.textColor;
            state.userStatusChart.update();
        }
    });

    // Listener para opções de público-alvo
    if (dom.targetOptionsDiv) {
        dom.targetOptionsDiv.addEventListener('change', (e) => {
            if (e.target.name === 'notification_target') {
                const selectedValue = e.target.value;
                if (dom.openUserSelectionBtn) {
                   dom.openUserSelectionBtn.disabled = (selectedValue !== 'specific');
                }
                if (selectedValue !== 'specific') {
                   state.selectedUserIds.clear();
                   const countLabel = document.getElementById('target-specific-count');
                   if (countLabel) countLabel.textContent = '';
                }
                updateSendButtonText();
            }
        });
    }

    // Listener para botão "Selecionar..."
    if (dom.openUserSelectionBtn) {
        dom.openUserSelectionBtn.addEventListener('click', (e) => {
            e.preventDefault(); // Impede saltos na página
            const targetSpecific = document.getElementById('target_specific');
            if (targetSpecific) targetSpecific.checked = true;
            openUserSelectionModal();
        });
    }

    // 🛡️ CORREÇÃO PRINCIPAL: Captura o Formulário e o Botão para impedir o Reload!
    if (dom.sendBulkNotificationBtn) {
        const bulkForm = dom.sendBulkNotificationBtn.closest('form');
        
        if (bulkForm) {
            // Se existir um formulário à volta do botão, bloqueia a submissão nativa
            bulkForm.addEventListener('submit', (e) => {
                e.preventDefault();
                handleSendBulkNotification(e);
            });
        } else {
            // Se for só o botão solto, bloqueia o clique
            dom.sendBulkNotificationBtn.addEventListener('click', (e) => {
                e.preventDefault();
                handleSendBulkNotification(e);
            });
        }
    }

    // Listener para botão "Limpar Todos os Logs"
    if (dom.clearAllLogsBtn) {
        dom.clearAllLogsBtn.addEventListener('click', (e) => {
            e.preventDefault();
            createModal('confirmationModal', i18n.confirmClearLogsTitle,
                `<p>${i18n.confirmClearLogsMessage}</p>`,
                `<button type="button" id="modalConfirm" class="btn bg-red-600 text-white">${i18n.confirmClearLogsButton}</button>
                 <button type="button" id="modalCancel" class="btn bg-gray-200 dark:bg-gray-600">${i18n.cancel}</button>`
            );
            document.getElementById('modalConfirm').onclick = () => {
                document.getElementById('confirmationModal').classList.add('hidden');
                handleClearAllLogs();
            };
            document.getElementById('modalCancel').onclick = () => {
                document.getElementById('confirmationModal').classList.add('hidden');
            };
        });
    }

    // Delegação de eventos para apagar logs individuais
    if (dom.auditLogContainer) {
        dom.auditLogContainer.addEventListener('click', (e) => {
            const deleteButton = e.target.closest('.delete-log-btn');
            if (deleteButton) {
                e.preventDefault();
                const logId = deleteButton.closest('[data-log-id]').dataset.logId;
                createModal('confirmationModal', i18n.confirmDeleteLogTitle,
                    `<p>${i18n.confirmDeleteLogMessage}</p>`,
                    `<button type="button" id="modalConfirm" class="btn bg-red-600 text-white">${i18n.confirmDeleteLogButton}</button>
                     <button type="button" id="modalCancel" class="btn bg-gray-200 dark:bg-gray-600">${i18n.cancel}</button>`
                );
                document.getElementById('modalConfirm').onclick = () => {
                    document.getElementById('confirmationModal').classList.add('hidden');
                    handleDeleteLog(logId);
                };
                document.getElementById('modalCancel').onclick = () => {
                    document.getElementById('confirmationModal').classList.add('hidden');
                };
            }
        });
    }
}
