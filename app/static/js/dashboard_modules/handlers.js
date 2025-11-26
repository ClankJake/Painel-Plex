// Funções de manipulação de eventos e lógica de negócios
import { dom, state } from './config.js';
import { deleteLog, clearAllLogs, sendBulkNotification } from './api.js';
import { renderTerminationLogs, updateSpecificUserLabel, openUserSelectionModal } from './ui.js';
import { showToast, createModal } from '../utils.js';

/**
 * Manipula a exclusão de um único log após confirmação.
 * @param {string} logId - O ID do log.
 */
export async function handleDeleteLog(logId) {
    const { i18n } = state;
    try {
        const result = await deleteLog(logId);
        showToast(result.message, result.success ? 'success' : 'error');
        if (result.success) {
            const logElement = document.querySelector(`[data-log-id="${logId}"]`);
            if (logElement) {
                logElement.style.transition = 'opacity 0.3s ease-out, max-height 0.3s ease-out';
                logElement.style.opacity = '0';
                logElement.style.maxHeight = '0';
                logElement.style.padding = '0';
                logElement.style.margin = '0';
                setTimeout(() => {
                    logElement.remove();
                    if (dom.auditLogContainer && dom.auditLogContainer.children.length === 0) {
                        renderTerminationLogs([]);
                    }
                }, 300);
            }
        }
    } catch (error) {
        showToast(error.message, 'error');
    }
}

/**
 * Manipula a limpeza de todos os logs após confirmação.
 */
export async function handleClearAllLogs() {
    const { i18n } = state;
    try {
        const result = await clearAllLogs();
        showToast(result.message, result.success ? 'success' : 'error');
        if (result.success && dom.auditLogContainer) {
            Array.from(dom.auditLogContainer.children).forEach((child, index) => {
                 setTimeout(() => {
                    child.style.transition = 'opacity 0.2s ease-out';
                    child.style.opacity = '0';
                }, index * 50);
            });
            setTimeout(() => renderTerminationLogs([]), dom.auditLogContainer.children.length * 50 + 200);
        }
    } catch (error) {
        showToast(error.message, 'error');
    }
}

/**
 * Atualiza o texto do botão de envio em massa com base no alvo selecionado.
 */
export function updateSendButtonText() {
    if (!dom.targetOptionsDiv || !dom.sendBulkBtnText) return;
    const { i18n } = state;
    const selectedTargetInput = dom.targetOptionsDiv.querySelector('input[name="notification_target"]:checked');
    if (!selectedTargetInput) return;

    const selectedTarget = selectedTargetInput.value;
    let buttonText = i18n.sendToAllActive; // Default

    if (selectedTarget === 'blocked') {
        buttonText = i18n.sendToAllBlocked;
    } else if (selectedTarget === 'all') {
        buttonText = i18n.sendToAllUsers;
    } else if (selectedTarget === 'specific') {
        const count = state.selectedUserIds.size;
        buttonText = i18n.sendToSpecificUsers.replace('{count}', count);
    }
    dom.sendBulkBtnText.textContent = buttonText;
}

/**
 * Reseta a UI de notificação em massa após um atraso.
 * @param {number} delay - Atraso em milissegundos.
 */
export function resetBulkNotificationUI(delay) {
    const { i18n } = state;

    // Limpa qualquer temporizador de segurança pendente
    if (state.safetyTimeout) {
        clearTimeout(state.safetyTimeout);
        state.safetyTimeout = null;
    }

    setTimeout(() => {
        if (dom.sendBulkNotificationBtn) {
            dom.sendBulkNotificationBtn.disabled = false;
        }
        const bulkMessage = document.getElementById('bulk_message');
        if (bulkMessage) {
            bulkMessage.value = '';
        }
        const targetActive = document.getElementById('target_active');
        if (targetActive) targetActive.checked = true;
        
        state.selectedUserIds.clear();
        updateSpecificUserLabel(0);
        updateSendButtonText();
        
        if (dom.openUserSelectionBtn) dom.openUserSelectionBtn.disabled = true;
        if (dom.progressContainer) dom.progressContainer.classList.add('hidden');
        if(dom.progressBar) {
            dom.progressBar.style.width = '0%';
            dom.progressBar.classList.remove('bg-red-600', 'bg-green-600');
            dom.progressBar.classList.add('bg-blue-600'); // Reseta para a cor azul
        }
        if(dom.progressPercent) dom.progressPercent.textContent = '0%';
        if(dom.progressText) dom.progressText.textContent = i18n.bulkSendStart || 'A iniciar...';

    }, delay);
}

/**
 * Manipula o clique do botão "Enviar" - valida, confirma e chama a API.
 */
export async function handleSendBulkNotification() {
    const { i18n } = state;
    const messageEl = document.getElementById('bulk_message');
    const message = messageEl ? messageEl.value : '';

    if (!message.trim()) {
        showToast(i18n.writeAMessage, 'error');
        return;
    }

    const selectedTargetInput = document.querySelector('input[name="notification_target"]:checked');
    if (!selectedTargetInput) {
         showToast(i18n.selectTargetAudience, 'error');
        return;
    }
    
    const targetAudience = selectedTargetInput.value;
    let title = i18n.confirmBulkSendTitle; // Título genérico
    let confirmationMessage = i18n.confirmBulkSendMessage;
    let payload = { message, target_audience: targetAudience };
    let selectedUserIds = [];

    if (targetAudience === 'specific') {
        selectedUserIds = Array.from(state.selectedUserIds);
        if (selectedUserIds.length === 0) {
            showToast(i18n.selectAtLeastOneUser, 'warning');
            openUserSelectionModal();
            return;
        }
        confirmationMessage = i18n.confirmBulkSendSpecificMessage.replace('{count}', selectedUserIds.length);
        payload.user_ids = selectedUserIds;
    }

    // Modal de Confirmação
    createModal('confirmationModal', title,
        `<p>${confirmationMessage}</p>`,
        `<button id="modalConfirm" class="btn bg-red-600 text-white">${i18n.confirmSendButton}</button>
         <button id="modalCancel" class="btn bg-gray-200 dark:bg-gray-600">${i18n.cancel}</button>`
    );

    const confirmBtn = document.getElementById('modalConfirm');
    const cancelBtn = document.getElementById('modalCancel');
    const confirmationModal = document.getElementById('confirmationModal');

    if (confirmBtn) {
        confirmBtn.onclick = async () => {
           if (confirmationModal) confirmationModal.classList.add('hidden');

           // Desativa o botão e mostra o progresso imediatamente
           dom.sendBulkNotificationBtn.disabled = true;
           dom.sendBulkBtnText.textContent = i18n.sendingBulkNotification;
           if (dom.progressContainer) dom.progressContainer.classList.remove('hidden');
           // Garante que o texto 'A iniciar...' está correto
           if (dom.progressText) dom.progressText.textContent = i18n.bulkSendStart || 'A iniciar...';
           if (dom.progressBar) {
               dom.progressBar.style.width = '0%';
               dom.progressBar.classList.remove('bg-red-600', 'bg-green-600');
               dom.progressBar.classList.add('bg-blue-600');
           }
           if (dom.progressPercent) dom.progressPercent.textContent = '0%';


           try {
               const result = await sendBulkNotification(payload);
               
               if (result.success) {
                    // Inicia o temporizador de segurança.
                    // Se o evento 'bulk_notification_start' não chegar em 15s,
                    // a UI será redefinida.
                    state.safetyTimeout = setTimeout(() => {
                        if (dom.sendBulkNotificationBtn.disabled) { // Só se ainda estiver bloqueado
                            showToast('A tarefa foi enviada, mas não recebemos resposta do servidor. (Timeout)', 'error');
                            resetBulkNotificationUI(0);
                        }
                    }, 30000);
                   
               } else {
                    // FALHA IMEDIATA DA API
                    showToast(result.message, 'error');
                    resetBulkNotificationUI(0); // Reset imediato
               }
           } catch (error) {
               showToast(error.message, 'error');
               resetBulkNotificationUI(0); // Reset imediato
           }
        };
    }
    if (cancelBtn) {
        cancelBtn.onclick = () => {
            if (confirmationModal) confirmationModal.classList.add('hidden');
        }
    }
}

