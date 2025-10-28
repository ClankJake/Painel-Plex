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
                   document.getElementById('target-specific-count').textContent = '';
                }
                updateSendButtonText();
            }
        });
    }

    // Listener para botão "Selecionar..."
    if (dom.openUserSelectionBtn) {
        dom.openUserSelectionBtn.addEventListener('click', () => {
            const targetSpecific = document.getElementById('target_specific');
            if (targetSpecific) targetSpecific.checked = true;
            openUserSelectionModal();
        });
    }

    // Listener para botão "Enviar"
    if (dom.sendBulkNotificationBtn) {
        dom.sendBulkNotificationBtn.addEventListener('click', handleSendBulkNotification);
    }

    // Listener para botão "Limpar Todos os Logs"
    if (dom.clearAllLogsBtn) {
        dom.clearAllLogsBtn.addEventListener('click', () => {
            createModal('confirmationModal', i18n.confirmClearLogsTitle,
                `<p>${i18n.confirmClearLogsMessage}</p>`,
                `<button id="modalConfirm" class="btn bg-red-600 text-white">${i18n.confirmClearLogsButton}</button>
                 <button id="modalCancel" class="btn bg-gray-200 dark:bg-gray-600">${i18n.cancel}</button>`
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
                const logId = deleteButton.closest('[data-log-id]').dataset.logId;
                createModal('confirmationModal', i18n.confirmDeleteLogTitle,
                    `<p>${i18n.confirmDeleteLogMessage}</p>`,
                    `<button id="modalConfirm" class="btn bg-red-600 text-white">${i18n.confirmDeleteLogButton}</button>
                     <button id="modalCancel" class="btn bg-gray-200 dark:bg-gray-600">${i18n.cancel}</button>`
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
