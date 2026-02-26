// app/static/js/users_modules/handlers.js

import * as api from './api.js';
import * as ui from './ui.js';
import * as modals from './modals.js';
import * as state from './state.js';
import { i18n, urls } from './config.js';
import { showToast } from '../utils.js';

/**
 * Módulo de Handlers (Manipuladores de Eventos)
 * Contém a lógica que é executada em resposta às interações do usuário,
 * atuando como intermediário entre a UI e a API.
 */

// ==========================================
// HELPERS E UTILITÁRIOS
// ==========================================

/**
 * Sanitiza entradas de texto para prevenir ataques de XSS (Cross-Site Scripting).
 */
const sanitizeHTML = (str) => {
    if (!str) return '';
    const temp = document.createElement('div');
    temp.textContent = str;
    return temp.innerHTML;
};

/**
 * Helper universal para copiar texto, contornando bloqueios de segurança
 * do navigator.clipboard em ambientes sem HTTPS estrito ou iFrames.
 */
const copyToClipboardFallback = (text) => {
    const el = document.createElement('textarea');
    el.value = text;
    el.setAttribute('readonly', '');
    el.style.position = 'absolute';
    el.style.left = '-9999px';
    document.body.appendChild(el);
    el.select();
    document.execCommand('copy');
    document.body.removeChild(el);
};

/**
 * Pega o URL base configurado (APP_BASE_URL) para criar links públicos precisos,
 * evitando que os links fiquem com "localhost" se o admin os copiar localmente.
 */
const getBaseUrl = () => {
    const scriptTag = document.getElementById('users-script');
    let appBaseUrl = scriptTag && scriptTag.dataset.appBaseUrl ? scriptTag.dataset.appBaseUrl.trim() : '';
    
    if (appBaseUrl) {
        // Remove a barra final se existir, para não ficar duplo: https://site.com//pay/...
        return appBaseUrl.replace(/\/+$/, '');
    }
    
    // Fallback de segurança se nada for encontrado
    return window.location.origin;
};

/**
 * Determina se a renovação deve ser a partir de hoje ou da data de expiração.
 */
const _determineRenewalBase = (expirationDateStr) => {
    if (!expirationDateStr) return 'today';
    try {
        const expDate = new Date(expirationDateStr);
        return expDate < new Date() ? 'today' : 'expiry_date';
    } catch (e) {
        return 'today';
    }
};

// ==========================================
// HANDLERS PRINCIPAIS
// ==========================================

/**
 * Manipula ações nos cartões de convite (copiar, apagar, detalhes).
 */
export function handleInviteAction(action, code, details = null) {
    if (action === 'copy-invite') {
        // Corrigido para respeitar o domínio público configurado
        const inviteUrl = `${getBaseUrl()}${urls.baseInvitePage}${code}`;
        modals.showInviteLinkModal(inviteUrl);
        return;
    } 
    
    if (action === 'details' && details) {
        modals.showInviteDetailsModal(details);
        return;
    }

    if (action === 'delete-invite') {
        modals.showConfirmationModal({
            title: i18n.deleteInvite,
            message: `${i18n.confirmDeleteInvite} <strong>${sanitizeHTML(code)}</strong>? ${i18n.actionCannotBeUndone}`,
            confirmText: i18n.confirmDeleteButton,
            confirmClass: 'bg-red-600 hover:bg-red-500 text-white',
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
 */
async function handleQuickRenewal(user) {
    modals.showConfirmationModal({
        title: i18n.addOneMonth,
        message: `${i18n.confirmAddOneMonth} <strong>${sanitizeHTML(user.username)}</strong>?`,
        confirmText: i18n.confirm,
        confirmClass: 'bg-green-600 hover:bg-green-500 text-white',
        onConfirm: async () => {
            try {
                const payload = { 
                    months: 1, 
                    base: _determineRenewalBase(user.expiration_date) 
                };

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
 * Roteador principal: Mapeia a ação do botão para a respetiva função no cartão do utilizador.
 */
export function handleUserAction(action, user) {
    if (action === 'reactivate') {
        modals.showReactivationModal(user);
        return;
    }

    // Ações que abrem Modais ou executam ações instantâneas simples
    const modalActions = {
        'manage-profile':    () => modals.showUserProfileModal(user),
        'manage-limit':      () => modals.showScreenLimitModal(user),
        'manage-libraries':  () => modals.showLibraryManagementModal(user),
        'payment-history':   () => modals.showPaymentHistoryModal(user),
        'renew-month':       () => handleQuickRenewal(user),
        'extend-trial':      () => modals.showExtendTrialModal(user),
        'copy-payment-link': () => {
            if (user.payment_token) {
                // Corrigido para respeitar o domínio público configurado
                const paymentUrl = `${getBaseUrl()}/pay/${user.payment_token}`;
                try {
                    copyToClipboardFallback(paymentUrl);
                    showToast(i18n.paymentLinkCopied || 'Link de pagamento copiado!', 'success');
                } catch (err) {
                    showToast(i18n.copyFailed || 'Falha ao copiar o link.', 'error');
                }
            } else {
                showToast('Token de pagamento não gerado para este utilizador.', 'error');
            }
        }
    };

    if (modalActions[action]) {
        modalActions[action]();
        return;
    }

    // Ações destrutivas ou de estado que exigem confirmação de segurança (Modais de Confirmação)
    const confirmationActions = {
        'remove': {
            title: i18n.removeUserTitle,
            message: `${i18n.confirmRemoveUser} <strong>${sanitizeHTML(user.username)}</strong>?`,
            confirmText: i18n.confirmRemoveButton,
            confirmClass: 'bg-red-600 hover:bg-red-500 text-white',
            apiCall: () => api.removeUser(user.id),
        },
        'block': {
            title: i18n.blockUserTitle,
            message: `${i18n.confirmBlockUser} <strong>${sanitizeHTML(user.username)}</strong>?`,
            confirmText: i18n.confirmBlockButton,
            confirmClass: 'bg-red-600 hover:bg-red-500 text-white',
            apiCall: () => api.blockUser(user.id),
        },
        'unblock': {
            title: i18n.unblockUserTitle,
            message: `${i18n.confirmUnblockUser} <strong>${sanitizeHTML(user.username)}</strong>?`,
            confirmText: i18n.confirmUnblockButton,
            confirmClass: 'bg-yellow-500 hover:bg-yellow-400 text-black',
            apiCall: () => api.unblockUser(user.id),
        },
        'delete-permanently': {
            title: i18n.deletePermanentlyTitle,
            message: `${i18n.confirmDeletePermanently} <strong>${sanitizeHTML(user.username)}</strong>? ${i18n.actionCannotBeUndone}`,
            confirmText: i18n.confirmDeleteButton,
            confirmClass: 'bg-red-800 hover:bg-red-700 text-white',
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
                    
                    // Atualiza a UI independentemente do resultado (sincroniza o estado)
                    ui.loadStatus(true); 
                } catch (error) {
                    showToast(error.message, 'error');
                    ui.loadStatus(true);
                }
            }
        });
    }
}
