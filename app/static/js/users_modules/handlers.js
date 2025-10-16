// app/static/js/users_modules/handlers.js

import * as api from './api.js';
import * as ui from './ui.js';
import * as state from './state.js';
import { i18n, urls } from './config.js';
import { showToast } from '../utils.js';

/**
 * Módulo de Handlers (Manipuladores de Eventos)
 * * Contém a lógica que é executada em resposta às interações do usuário,
 * como cliques em botões. Ele atua como um intermediário, chamando
 * funções da API e da UI conforme necessário.
 */

// CORREÇÃO: Função de sanitização para prevenir XSS
function sanitizeHTML(str) {
    const temp = document.createElement('div');
    temp.textContent = str;
    return temp.innerHTML;
}


/**
 * Manipula ações nos cartões de convite (copiar, apagar).
 * @param {string} action - A ação a ser executada ('copy-invite' ou 'delete-invite').
 * @param {string} code - O código do convite.
 */
export function handleInviteAction(action, code) {
    if (action === 'copy-invite') {
        const inviteUrl = `${window.location.origin}${urls.baseInvitePage}${code}`;
        ui.showInviteLinkModal(inviteUrl);
    } else if (action === 'delete-invite') {
        const message = `${i18n.confirmDeleteInvite} <strong>${sanitizeHTML(code)}</strong>? ${i18n.actionCannotBeUndone}`;
        ui.showConfirmationModal({
            title: i18n.deleteInvite,
            message: message,
            confirmText: i18n.confirmDeleteButton,
            confirmClass: 'bg-red-600 text-white',
            onConfirm: async () => {
                try {
                    const result = await api.deleteInvite(code);
                    showToast(result.message, result.success ? 'success' : 'error');
                    if (result.success) ui.loadInvites();
                } catch (error) {
                    showToast(error.message, 'error');
                }
            }
        });
    }
}

/**
 * Manipula a renovação rápida de assinatura (+1 mês).
 * @param {object} user - O objeto do usuário.
 */
async function handleQuickRenewal(user) {
    const message = `${i18n.confirmAddOneMonth} <strong>${sanitizeHTML(user.username)}</strong>?`;
    ui.showConfirmationModal({
        title: i18n.addOneMonth,
        message: message,
        confirmText: i18n.confirm,
        confirmClass: 'bg-green-600 text-white',
        onConfirm: async () => {
            try {
                const result = await api.renewSubscription(user.id, {
                    months: 1,
                    base: 'expiry_date'
                });
                showToast(result.message, result.success ? 'success' : 'error');
                if (result.success) ui.loadStatus(true);
            } catch (error) {
                showToast(error.message, 'error');
            }
        }
    });
}

/**
 * Manipula todas as ações nos cartões de usuário.
 * @param {string} action - A ação a ser executada.
 * @param {object} user - O objeto do usuário.
 */
export function handleUserAction(action, user) {
    const modalActions = {
        'manage-profile': () => ui.showUserProfileModal(user),
        'manage-limit': () => ui.showScreenLimitModal(user),
        'manage-libraries': () => ui.showLibraryManagementModal(user),
        'payment-history': () => ui.showPaymentHistoryModal(user),
        'renew-month': () => handleQuickRenewal(user),
        'copy-payment-link': () => {
            if (user.payment_token) {
                const paymentUrl = `${window.location.origin}/pay/${user.payment_token}`;
                navigator.clipboard.writeText(paymentUrl)
                    .then(() => showToast(i18n.paymentLinkCopied, 'success'))
                    .catch(() => showToast(i18n.copyFailed, 'error'));
            } else {
                showToast('Token de pagamento não encontrado para este usuário.', 'error');
            }
        }
    };

    if (modalActions[action]) {
        modalActions[action]();
        return;
    }

    const confirmationActions = {
        'remove': {
            title: i18n.removeUserTitle,
            message: `${i18n.confirmRemoveUser} <strong>${sanitizeHTML(user.username)}</strong>?`,
            confirmText: i18n.confirmRemoveButton,
            confirmClass: 'bg-red-600 text-white',
            apiCall: () => api.removeUser(user.id),
        },
        'block': {
            title: i18n.blockUserTitle,
            message: `${i18n.confirmBlockUser} <strong>${sanitizeHTML(user.username)}</strong>?`,
            confirmText: i18n.confirmBlockButton,
            confirmClass: 'bg-red-600 text-white',
            apiCall: () => api.blockUser(user.id),
        },
        'unblock': {
            title: i18n.unblockUserTitle,
            message: `${i18n.confirmUnblockUser} <strong>${sanitizeHTML(user.username)}</strong>?`,
            confirmText: i18n.confirmUnblockButton,
            confirmClass: 'bg-yellow-500 text-black',
            apiCall: () => api.unblockUser(user.id),
        },
        'reactivate': {
            title: i18n.reactivateUserTitle,
            message: i18n.confirmReactivateUser.replace('{username}', `<strong>${sanitizeHTML(user.username)}</strong>`),
            confirmText: i18n.confirmReactivateButton,
            confirmClass: 'bg-green-600 text-white',
            apiCall: () => api.reactivateUser(user.id),
        },
        'delete-permanently': {
            title: i18n.deletePermanentlyTitle,
            message: `${i18n.confirmDeletePermanently} <strong>${sanitizeHTML(user.username)}</strong>? ${i18n.actionCannotBeUndone}`,
            confirmText: i18n.confirmDeleteButton,
            confirmClass: 'bg-red-800 text-white',
            apiCall: () => api.deleteUserPermanently(user.id),
        }
    };

    const config = confirmationActions[action];
    if (config) {
        ui.showConfirmationModal({
            title: config.title,
            message: config.message,
            confirmText: config.confirmText,
            confirmClass: config.confirmClass,
            onConfirm: async () => {
                try {
                    const result = await config.apiCall();
                    showToast(result.message, result.success ? 'success' : 'error');
                    if (result.success) {
                        ui.loadStatus(true); // Always refresh on success
                    }
                } catch (error) {
                    showToast(error.message, 'error');
                    ui.loadStatus(true); // Refresh on error too
                }
            }
        });
    }
}

