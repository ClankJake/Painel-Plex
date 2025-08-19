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
        const message = `${i18n.confirmDeleteInvite} <strong>${code}</strong>? ${i18n.actionCannotBeUndone}`;
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
    const message = `${i18n.confirmAddOneMonth} <strong>${user.username}</strong>?`;
    ui.showConfirmationModal({
        title: i18n.addOneMonth,
        message: message,
        confirmText: i18n.confirm,
        confirmClass: 'bg-green-600 text-white',
        onConfirm: async () => {
            try {
                const result = await api.renewSubscription(user.username, {
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
    // Ações que abrem modais
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

    // Ações que requerem confirmação
    const confirmationActions = {
        'remove': { title: i18n.removeUserTitle, message: `${i18n.confirmRemoveUser} <strong>${user.username}</strong>?`, confirmText: i18n.confirmRemoveButton, confirmClass: 'bg-red-600 text-white', apiCall: () => api.removeUser(user.email) },
        'block': { title: i18n.blockUserTitle, message: `${i18n.confirmBlockUser} <strong>${user.username}</strong>?`, confirmText: i18n.confirmBlockButton, confirmClass: 'bg-red-600 text-white', apiCall: () => api.blockUser(user.email) },
        'unblock': { title: i18n.unblockUserTitle, message: `${i18n.confirmUnblockUser} <strong>${user.username}</strong>?`, confirmText: i18n.confirmUnblockButton, confirmClass: 'bg-yellow-500 text-black', apiCall: () => api.unblockUser(user.email) }
    };

    const config = confirmationActions[action];
    if (config) {
        ui.showConfirmationModal({
            ...config,
            onConfirm: async () => {
                try {
                    const result = await config.apiCall();
                    showToast(result.message, result.success ? 'success' : 'error');
                    if (result.success) ui.loadStatus(true);
                } catch (error) {
                    showToast(error.message, 'error');
                }
            }
        });
    }
}
