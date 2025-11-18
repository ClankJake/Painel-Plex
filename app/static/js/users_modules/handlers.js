// app/static/js/users_modules/handlers.js

import * as api from './api.js';
import * as ui from './ui.js';
import * as modals from './modals.js';
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
 * Manipula ações nos cartões de convite (copiar, apagar, detalhes).
 * @param {string} action - A ação a ser executada.
 * @param {string} code - O código do convite.
 * @param {object} details - (Opcional) Os detalhes completos do convite para o modal de info.
 */
export function handleInviteAction(action, code, details = null) {
    if (action === 'copy-invite') {
        const inviteUrl = `${window.location.origin}${urls.baseInvitePage}${code}`;
        modals.showInviteLinkModal(inviteUrl);
    } else if (action === 'delete-invite') {
        const message = `${i18n.confirmDeleteInvite} <strong>${sanitizeHTML(code)}</strong>? ${i18n.actionCannotBeUndone}`;
        modals.showConfirmationModal({
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
    } else if (action === 'details' && details) {
        modals.showInviteDetailsModal(details);
    }
}

/**
 * Manipula a renovação rápida de assinatura (+1 mês).
 * @param {object} user - O objeto do usuário.
 */
async function handleQuickRenewal(user) {
    const message = `${i18n.confirmAddOneMonth} <strong>${sanitizeHTML(user.username)}</strong>?`;
    modals.showConfirmationModal({
        title: i18n.addOneMonth,
        message: message,
        confirmText: i18n.confirm,
        confirmClass: 'bg-green-600 text-white',
        onConfirm: async () => {
            try {
                // Tenta renovar a partir da data de expiração, se existir e for futura
                const payload = { months: 1, base: 'expiry_date' };
                if (user.expiration_date) {
                    try {
                        const expDate = new Date(user.expiration_date);
                        if (expDate < new Date()) {
                            payload.base = 'today'; // Se já expirou, renova a partir de hoje
                        }
                    } catch (e) {
                         payload.base = 'today'; // Fallback se a data for inválida
                    }
                } else {
                    payload.base = 'today'; // Se não há data, renova a partir de hoje
                }

                const result = await api.renewSubscription(user.id, payload);
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
    if (action === 'reactivate') {
        modals.showReactivationModal(user);
        return;
    }

    const modalActions = {
        'manage-profile': () => modals.showUserProfileModal(user),
        'manage-limit': () => modals.showScreenLimitModal(user),
        'manage-libraries': () => modals.showLibraryManagementModal(user),
        'payment-history': () => modals.showPaymentHistoryModal(user),
        'renew-month': () => handleQuickRenewal(user),
        'extend-trial': () => modals.showExtendTrialModal(user), // NOVO: Chama o novo modal
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
        modals.showConfirmationModal({
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