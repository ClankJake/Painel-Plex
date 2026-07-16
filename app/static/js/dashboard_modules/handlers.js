// Funções de manipulação de eventos e lógica de negócios
import { dom, state } from './config.js';
import { deleteLog, clearAllLogs, sendBulkNotification } from './api.js';
import { renderTerminationLogs, updateSpecificUserLabel, openUserSelectionModal } from './ui.js';
import { showToast, createModal } from '../utils.js';


// ==========================================
// HELPERS DE UI LOCAIS
// ==========================================

export const setProgressBarState = (status, percent = 0, text = '') => {
    if (!dom.progressBar || !dom.progressContainer) return;
    
    dom.progressBar.style.width = `${percent}%`;
    dom.progressBar.classList.remove('bg-blue-600', 'bg-green-600', 'bg-red-600');
    
    const statusColors = {
        'active': 'bg-blue-600',
        'success': 'bg-green-600',
        'error': 'bg-red-600'
    };
    
    if (statusColors[status]) {
        dom.progressBar.classList.add(statusColors[status]);
    }

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
                // Animação de remoção suave
                Object.assign(logElement.style, {
                    transition: 'all 0.3s ease-out',
                    opacity: '0',
                    maxHeight: '0',
                    padding: '0',
                    margin: '0',
                    border: 'none'
                });
                
                setTimeout(() => {
                    logElement.remove();
                    // Renderiza estado vazio se não houver mais logs
                    if (dom.auditLogContainer?.children.length === 0) {
                        renderTerminationLogs([]); 
                    }
                }, 300);
            }
        }
    } catch (error) {
        showToast(error.message || 'Erro ao apagar log.', 'error');
    }
}

export async function handleClearAllLogs() {
    try {
        const result = await clearAllLogs();
        showToast(result.message, result.success ? 'success' : 'error');
        
        if (result.success && dom.auditLogContainer) {
            const children = Array.from(dom.auditLogContainer.children);
            
            // Efeito cascata para apagar os logs visualmente
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
        showToast(error.message || 'Erro ao limpar logs.', 'error');
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


export function handleSendBulkNotification(e) {
    // 🛡️ Previne recarregamento nativo da página
    e?.preventDefault?.(); 

    const { i18n } = state;
    const messageEl = document.getElementById('bulk_message');
    const message = messageEl?.value.trim() || '';

    if (!message) {
        return showToast(i18n.writeAMessage, 'error');
    }

    const selectedTargetInput = document.querySelector('input[name="notification_target"]:checked');
    if (!selectedTargetInput) {
        return showToast(i18n.selectTargetAudience, 'error');
    }
    
    const targetAudience = selectedTargetInput.value;
    let confirmationMessage = i18n.confirmBulkSendMessage;
    let payload = { message, target_audience: targetAudience };

    if (targetAudience === 'specific') {
        const selectedUserIds = Array.from(state.selectedUserIds);
        if (selectedUserIds.length === 0) {
            showToast(i18n.selectAtLeastOneUser, 'warning');
            return openUserSelectionModal();
        }
        confirmationMessage = i18n.confirmBulkSendSpecificMessage.replace('{count}', selectedUserIds.length);
        payload.user_ids = selectedUserIds;
    }

    _promptBulkConfirmation(confirmationMessage, payload);
}

function _promptBulkConfirmation(message, payload) {
    const { i18n } = state;
    
    // 🛡️ Adicionado type="button" para evitar submits indesejados no modal
    createModal('confirmationModal', i18n.confirmBulkSendTitle,
        `<p>${message}</p>`,
        `<button type="button" id="modalConfirm" class="btn bg-red-600 text-white">${i18n.confirmSendButton}</button>
         <button type="button" id="modalCancel" class="btn bg-gray-200 dark:bg-gray-600">${i18n.cancel}</button>`
    );

    const confirmBtn = document.getElementById('modalConfirm');
    const cancelBtn = document.getElementById('modalCancel');
    const confirmationModal = document.getElementById('confirmationModal');

    const closeModal = () => confirmationModal?.classList.add('hidden');

    if (confirmBtn) {
        confirmBtn.onclick = (e) => {
            e.preventDefault();
            closeModal();
            _executeBulkNotification(payload);
        };
    }
    
    if (cancelBtn) {
        cancelBtn.onclick = (e) => {
            e.preventDefault();
            closeModal();
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
            showToast('A tarefa de envio em massa foi iniciada! Acompanhe o progresso.', 'success');

            // Temporizador de Segurança alargado (60s) para reset em caso de falha de socket
            state.safetyTimeout = setTimeout(() => {
                if (dom.sendBulkNotificationBtn?.disabled) {
                    showToast('O envio concluiu no servidor, mas a interface não recebeu o sinal final.', 'info');
                    resetBulkNotificationUI(0);
                }
            }, 60000);
        } else {
            showToast(result.message || 'Erro ao iniciar envio.', 'error');
            resetBulkNotificationUI(0);
        }
    } catch (error) {
        showToast(error.message || 'Erro de conexão.', 'error');
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
