// Funções de manipulação de eventos e lógica de negócios
import { dom, state } from './config.js';
import { deleteLog, clearAllLogs, sendBulkNotification } from './api.js';
import { renderTerminationLogs, updateSpecificUserLabel, openUserSelectionModal } from './ui.js';
import { showToast, createModal } from '../utils.js';

// ==========================================
// HELPERS DE UI LOCAIS (AGORA EXPORTADOS)
// ==========================================

export const setProgressBarState = (status, percent = 0, text = '') => {
    if (!dom.progressBar || !dom.progressContainer) return;
    
    dom.progressBar.style.width = `${percent}%`;
    dom.progressBar.classList.remove('bg-blue-600', 'bg-green-600', 'bg-red-600');
    
    if (status === 'active') dom.progressBar.classList.add('bg-blue-600');
    if (status === 'success') dom.progressBar.classList.add('bg-green-600');
    if (status === 'error') dom.progressBar.classList.add('bg-red-600');

    if (dom.progressPercent) dom.progressPercent.textContent = `${percent}%`;
    if (dom.progressText && text) dom.progressText.textContent = text;
};

// ==========================================
// LOGS DE AUDITORIA (TERMINATION)
// ==========================================

export async function handleDeleteLog(logId) {
    try {
        const result = await deleteLog(logId);
        showToast(result.message, result.success ? 'success' : 'error');
        
        if (result.success) {
            const logElement = document.querySelector(`[data-log-id="${logId}"]`);
            if (logElement) {
                Object.assign(logElement.style, {
                    transition: 'all 0.3s ease-out', opacity: '0', maxHeight: '0', padding: '0', margin: '0', border: 'none'
                });
                
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

export async function handleClearAllLogs() {
    try {
        const result = await clearAllLogs();
        showToast(result.message, result.success ? 'success' : 'error');
        
        if (result.success && dom.auditLogContainer) {
            const children = Array.from(dom.auditLogContainer.children);
            
            children.forEach((child, index) => {
                setTimeout(() => {
                    child.style.transition = 'opacity 0.2s ease-out';
                    child.style.opacity = '0';
                }, Math.min(index * 30, 500)); 
            });
            
            const totalDelay = Math.min(children.length * 30, 500) + 200;
            setTimeout(() => renderTerminationLogs([]), totalDelay);
        }
    } catch (error) {
        showToast(error.message, 'error');
    }
}

// ==========================================
// NOTIFICAÇÕES EM MASSA (BULK)
// ==========================================

export function updateSendButtonText() {
    if (!dom.targetOptionsDiv || !dom.sendBulkBtnText) return;
    
    const { i18n } = state;
    const selectedTargetInput = dom.targetOptionsDiv.querySelector('input[name="notification_target"]:checked');
    if (!selectedTargetInput) return;

    const target = selectedTargetInput.value;
    const targetTexts = {
        'active': i18n.sendToAllActive,
        'blocked': i18n.sendToAllBlocked,
        'all': i18n.sendToAllUsers
    };

    if (target === 'specific') {
        dom.sendBulkBtnText.textContent = i18n.sendToSpecificUsers.replace('{count}', state.selectedUserIds.size);
    } else {
        dom.sendBulkBtnText.textContent = targetTexts[target] || i18n.sendToAllActive;
    }
}

export function resetBulkNotificationUI(delay = 0) {
    const { i18n } = state;

    if (state.safetyTimeout) {
        clearTimeout(state.safetyTimeout);
        state.safetyTimeout = null;
    }

    setTimeout(() => {
        if (dom.sendBulkNotificationBtn) dom.sendBulkNotificationBtn.disabled = false;
        
        const bulkMessage = document.getElementById('bulk_message');
        if (bulkMessage) bulkMessage.value = '';
        
        const targetActive = document.getElementById('target_active');
        if (targetActive) targetActive.checked = true;
        
        state.selectedUserIds.clear();
        updateSpecificUserLabel(0);
        updateSendButtonText();
        
        if (dom.openUserSelectionBtn) dom.openUserSelectionBtn.disabled = true;
        if (dom.progressContainer) dom.progressContainer.classList.add('hidden');
        
        setProgressBarState('active', 0, i18n.bulkSendStart || 'A iniciar...');
    }, delay);
}

// 🛡️ CORREÇÃO PRINCIPAL: Recebe o evento "e" e impede o reload da página!
export function handleSendBulkNotification(e) {
    if (e && typeof e.preventDefault === 'function') {
        e.preventDefault(); // Impede que o formulário recarregue a página
    }

    const { i18n } = state;
    const messageEl = document.getElementById('bulk_message');
    const message = messageEl ? messageEl.value.trim() : '';

    if (!message) {
        showToast(i18n.writeAMessage, 'error');
        return;
    }

    const selectedTargetInput = document.querySelector('input[name="notification_target"]:checked');
    if (!selectedTargetInput) {
        showToast(i18n.selectTargetAudience, 'error');
        return;
    }
    
    const targetAudience = selectedTargetInput.value;
    let confirmationMessage = i18n.confirmBulkSendMessage;
    let payload = { message, target_audience: targetAudience };

    if (targetAudience === 'specific') {
        const selectedUserIds = Array.from(state.selectedUserIds);
        if (selectedUserIds.length === 0) {
            showToast(i18n.selectAtLeastOneUser, 'warning');
            openUserSelectionModal();
            return;
        }
        confirmationMessage = i18n.confirmBulkSendSpecificMessage.replace('{count}', selectedUserIds.length);
        payload.user_ids = selectedUserIds;
    }

    _promptBulkConfirmation(confirmationMessage, payload);
}

function _promptBulkConfirmation(message, payload) {
    const { i18n } = state;
    // 🛡️ CORREÇÃO: Adicionado type="button" para não acionar submits nativos
    createModal('confirmationModal', i18n.confirmBulkSendTitle,
        `<p>${message}</p>`,
        `<button type="button" id="modalConfirm" class="btn bg-red-600 text-white">${i18n.confirmSendButton}</button>
         <button type="button" id="modalCancel" class="btn bg-gray-200 dark:bg-gray-600">${i18n.cancel}</button>`
    );

    const confirmBtn = document.getElementById('modalConfirm');
    const cancelBtn = document.getElementById('modalCancel');
    const confirmationModal = document.getElementById('confirmationModal');

    if (confirmBtn) {
        confirmBtn.onclick = (e) => {
            e.preventDefault();
            if (confirmationModal) confirmationModal.classList.add('hidden');
            _executeBulkNotification(payload);
        };
    }
    if (cancelBtn) {
        cancelBtn.onclick = (e) => {
            e.preventDefault();
            if (confirmationModal) confirmationModal.classList.add('hidden');
        };
    }
}

async function _executeBulkNotification(payload) {
    const { i18n } = state;
    
    if (dom.sendBulkNotificationBtn) dom.sendBulkNotificationBtn.disabled = true;
    if (dom.sendBulkBtnText) dom.sendBulkBtnText.textContent = i18n.sendingBulkNotification || 'A enviar...';
    if (dom.progressContainer) dom.progressContainer.classList.remove('hidden');
    
    setProgressBarState('active', 0, i18n.bulkSendStart || 'A iniciar...');

    try {
        const result = await sendBulkNotification(payload);
        
        if (result.success) {
            // MOSTRA FEEDBACK INICIAL
            showToast('A tarefa de envio em massa foi iniciada! Acompanhe o progresso.', 'success');

            // Temporizador de Segurança alargado (60s)
            state.safetyTimeout = setTimeout(() => {
                if (dom.sendBulkNotificationBtn && dom.sendBulkNotificationBtn.disabled) {
                    showToast('O envio concluiu no servidor, mas a interface não recebeu o sinal final.', 'info');
                    resetBulkNotificationUI(0);
                }
            }, 60000);
        } else {
            showToast(result.message, 'error');
            resetBulkNotificationUI(0);
        }
    } catch (error) {
        showToast(error.message, 'error');
        resetBulkNotificationUI(0);
    }
}

// ==========================================
// EXPORTAÇÕES PARA O WEBSOCKET
// Estas funções devem ser chamadas pelo seu websocket.js!
// ==========================================

export function handleBulkProgressUpdate(current, total) {
    const percent = total > 0 ? Math.round((current / total) * 100) : 0;
    setProgressBarState('active', percent, `Enviando: ${current} / ${total}`);
}

export function handleBulkProgressEnd(message) {
    if (state.safetyTimeout) {
        clearTimeout(state.safetyTimeout);
        state.safetyTimeout = null;
    }
    setProgressBarState('success', 100, message || 'Envio Concluído!');
    showToast(message || 'Envio em massa concluído com sucesso!', 'success');
    resetBulkNotificationUI(3000); // Retira a barra após 3 segundos
}

export function handleBulkProgressError(message) {
    if (state.safetyTimeout) {
        clearTimeout(state.safetyTimeout);
        state.safetyTimeout = null;
    }
    setProgressBarState('error', 100, message || 'Falha no envio.');
    showToast(message || 'Ocorreu um erro durante o envio.', 'error');
    resetBulkNotificationUI(4000);
}
