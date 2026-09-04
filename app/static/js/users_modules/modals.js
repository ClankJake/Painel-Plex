import { createModal, showToast, sanitizeHTML, copyToClipboard } from '../utils.js';
import { i18n } from './config.js';
import * as state from './state.js';
import * as api from './api.js';
import * as ui from './ui.js';

/**
 * Módulo de Modais
 * Contém todas as funções para criar e gerir os diálogos modais
 * na página de utilizadores.
 */

// ==========================================
// FUNÇÕES AUXILIARES E SEGURANÇA
// ==========================================

/**
 * Alterna a seleção de todas as checkboxes dentro de um contentor.
 */
function toggleSelectAll(container, button) {
    const checkboxes = container.querySelectorAll('input[type="checkbox"]');
    const areAllSelected = Array.from(checkboxes).every(cb => cb.checked);
    checkboxes.forEach(cb => cb.checked = !areAllSelected);
    button.textContent = areAllSelected ? i18n.unselectAll : i18n.selectAll;
}

// Estilo padrão para o botão de Cancelar para manter a consistência em todos os modais
const btnCancelClass = "btn bg-gray-200 text-gray-800 dark:bg-gray-700 dark:text-gray-200 hover:bg-gray-300 dark:hover:bg-gray-600 transition-colors w-full sm:w-auto";

/**
 * Idioma ativo da página (de `<html lang="...">`). As datas estavam presas a
 * 'pt-BR' numa aplicação que é traduzida — quem usava o painel noutro idioma
 * continuava a ver datas em formato brasileiro.
 */
const pageLocale = document.documentElement.lang || undefined;

/** Formata uma data/hora no idioma ativo, tolerando valores em falta. */
const formatDateTime = (value) => {
    if (!value) return i18n.notAvailable || 'Não disponível';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? (i18n.notAvailable || 'Não disponível') : date.toLocaleString(pageLocale);
};

// ==========================================
// MODAIS GENÉRICOS
// ==========================================

export function showConfirmationModal({ title, message, confirmText, confirmClass, onConfirm }) {
    const modal = createModal('confirmationModal', title, `<p class="text-gray-700 dark:text-gray-300">${message}</p>`,
        `<button id="modalConfirm" class="btn ${confirmClass} w-full sm:w-auto transition-colors">${confirmText}</button>
         <button id="modalCancel" class="${btnCancelClass}">${i18n.cancel}</button>`
    );
    modal.querySelector('#modalConfirm').onclick = () => { onConfirm(); modal.classList.add('hidden'); };
    modal.querySelector('#modalCancel').onclick = () => modal.classList.add('hidden');
}

// ==========================================
// MODAIS DE CONVITES
// ==========================================

export function showInviteDetailsModal(details) {
    const { code, created_at, claimed_by_users, use_count, max_uses, libraries, screen_limit, telegram_id } = details;
    const claimedUsersList = claimed_by_users ? claimed_by_users : [];
    const safeCode = sanitizeHTML(code);
    
    const isExpired = details.expires_at && new Date(details.expires_at) < new Date();
    const isFull = use_count >= max_uses;
    const isActive = !isExpired && !isFull;

    let historyHtml = '';
    if (claimedUsersList.length > 0) {
        historyHtml = `
            <ul class="list-disc list-inside text-sm text-gray-700 dark:text-gray-300 bg-gray-50 dark:bg-gray-900/50 p-3 rounded-lg border border-gray-200 dark:border-gray-700 max-h-32 overflow-y-auto">
                ${claimedUsersList.map(user => `<li>${sanitizeHTML(user)}</li>`).join('')}
            </ul>`;
    } else {
        historyHtml = `<p class="text-sm text-gray-500 italic">${i18n.noUsesYet || 'Nenhum uso registado.'}</p>`;
    }

    const dateCreated = formatDateTime(created_at);
    const libList = libraries && libraries.length > 0
        ? sanitizeHTML(libraries.join(', '))
        : sanitizeHTML(i18n.allLibraries || 'Todas');

    const body = `
        <div class="space-y-4">
            <div class="grid grid-cols-2 gap-3 text-sm bg-gray-50 dark:bg-gray-800/50 p-4 rounded-lg border border-gray-200 dark:border-gray-700">
                <div><span class="font-bold block text-gray-500 dark:text-gray-400">${i18n.createdAt || 'Criado em:'}</span> <span class="text-gray-900 dark:text-white">${dateCreated}</span></div>
                <div><span class="font-bold block text-gray-500 dark:text-gray-400">${i18n.usage || 'Uso'}:</span> <span class="text-gray-900 dark:text-white">${use_count} / ${max_uses}</span></div>
                <div><span class="font-bold block text-gray-500 dark:text-gray-400">${i18n.screenLimitShort || 'Limite Telas'}:</span> <span class="text-gray-900 dark:text-white">${sanitizeHTML(screen_limit || i18n.default || 'Padrão')}</span></div>
                <div><span class="font-bold block text-gray-500 dark:text-gray-400">${i18n.libraries}:</span> <span class="truncate block text-gray-900 dark:text-white" title="${libList}">${libList}</span></div>
                ${telegram_id ? `<div class="col-span-2"><span class="font-bold block text-gray-500 dark:text-gray-400">${i18n.preassignedTelegramId || 'ID do Telegram pré-atribuído'}:</span> <span class="font-mono text-gray-900 dark:text-white">${sanitizeHTML(telegram_id)}</span></div>` : ''}
            </div>
            <div class="pt-2">
                <h4 class="text-sm font-bold text-gray-900 dark:text-white mb-2">${i18n.usageHistory || 'Histórico de Utilização'}:</h4>
                ${historyHtml}
            </div>
        </div>
    `;

    const reactivateButtonHtml = !isActive ? `
         <button id="btnReactivateInvite" class="btn bg-blue-600 hover:bg-blue-500 text-white flex-1 transition-colors">
            <svg class="w-4 h-4 mr-1 inline" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path></svg>
            ${i18n.reactivate || 'Reativar'}
        </button>
    ` : '';

    const footer = `
        <div class="flex flex-col sm:flex-row justify-between w-full gap-2">
            <button id="btnDeleteInvite" class="btn bg-red-600 hover:bg-red-500 text-white flex-1 transition-colors">
                <svg class="w-4 h-4 mr-1 inline" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"></path></svg>
                ${i18n.deleteInvite}
            </button>
            ${reactivateButtonHtml}
            <button id="btnCloseDetails" class="${btnCancelClass} flex-1">${i18n.close}</button>
        </div>
    `;

    const modal = createModal('inviteDetailsModal', `${sanitizeHTML(i18n.details || 'Detalhes')}: <span class="font-mono text-yellow-600 dark:text-yellow-500">${safeCode}</span>`, body, footer);
    
    modal.querySelector('#btnCloseDetails').onclick = () => modal.classList.add('hidden');
    
    modal.querySelector('#btnDeleteInvite').onclick = async () => {
        if (confirm(`${i18n.confirmDeleteInvite} ${safeCode}?`)) {
             try {
                const result = await api.deleteInvite(code);
                showToast(result.message, result.success ? 'success' : 'error');
                if (result.success) {
                    modal.classList.add('hidden');
                    ui.loadInvites();
                }
            } catch (error) {
                showToast(error.message, 'error');
            }
        }
    };

    const btnReactivate = modal.querySelector('#btnReactivateInvite');
    if (btnReactivate) {
        btnReactivate.onclick = async () => {
            try {
                const result = await api.reactivateInvite(code);
                showToast(result.message, result.success ? 'success' : 'error');
                if (result.success) {
                    modal.classList.add('hidden');
                    ui.loadInvites();
                }
            } catch (error) {
                showToast(error.message, 'error');
            }
        };
    }
}

export function showCreateInviteModal() {
    const telegramIdField = state.telegramEnabled ? `
        <div>
            <label for="inviteTelegramId" class="block mb-1.5 text-sm font-bold text-gray-700 dark:text-gray-300">${i18n.telegramIdOptional || 'Telegram ID (Opcional)'}</label>
            <input type="text" id="inviteTelegramId" class="w-full p-2.5 text-sm text-gray-900 bg-gray-50 rounded-lg border border-gray-300 focus:ring-yellow-500 focus:border-yellow-500 dark:bg-gray-800 dark:border-gray-600 dark:text-white" placeholder="Ex: 123456789">
            <p class="text-xs text-gray-500 mt-1">${i18n.telegramIdHint || 'Se preenchido, a conta ficará logo vinculada a este ID.'}</p>
        </div>
    ` : '';

    const body = `
        <div class="space-y-4">
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                    <label for="inviteCustomCode" class="block mb-1.5 text-sm font-bold text-gray-700 dark:text-gray-300">${i18n.customCode}</label>
                    <input type="text" id="inviteCustomCode" maxlength="64" class="w-full p-2.5 text-sm font-mono text-gray-900 bg-gray-50 rounded-lg border border-gray-300 focus:ring-yellow-500 focus:border-yellow-500 dark:bg-gray-800 dark:border-gray-600 dark:text-white" placeholder="${i18n.optional}">
                </div>
                <div>
                    <label for="inviteMaxUses" class="block mb-1.5 text-sm font-bold text-gray-700 dark:text-gray-300">${i18n.numberOfUses}</label>
                    <input type="number" id="inviteMaxUses" value="1" min="1" class="w-full p-2.5 text-sm text-gray-900 bg-gray-50 rounded-lg border border-gray-300 focus:ring-yellow-500 focus:border-yellow-500 dark:bg-gray-800 dark:border-gray-600 dark:text-white">
                </div>
            </div>
            ${telegramIdField}
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                    <label for="inviteScreenLimit" class="block mb-1.5 text-sm font-bold text-gray-700 dark:text-gray-300">${i18n.screenLimit}</label>
                    <select id="inviteScreenLimit" class="w-full p-2.5 text-sm text-gray-900 bg-gray-50 rounded-lg border border-gray-300 focus:ring-yellow-500 focus:border-yellow-500 dark:bg-gray-800 dark:border-gray-600 dark:text-white">
                        <option value="0">${i18n.noLimit}</option><option value="1">1 ${i18n.screenSingular}</option><option value="2">2 ${i18n.screenPlural}</option><option value="3">3 ${i18n.screenPlural}</option><option value="4">4 ${i18n.screenPlural}</option><option value="5">5 ${i18n.screenPlural}</option><option value="6">6 ${i18n.screenPlural}</option>
                    </select>
                </div>
                <div>
                    <label for="inviteTrialDuration" class="block mb-1.5 text-sm font-bold text-gray-700 dark:text-gray-300">${i18n.trialDuration}</label>
                    <select id="inviteTrialDuration" class="w-full p-2.5 text-sm text-gray-900 bg-gray-50 rounded-lg border border-gray-300 focus:ring-yellow-500 focus:border-yellow-500 dark:bg-gray-800 dark:border-gray-600 dark:text-white">
                        <option value="0">${i18n.noTrial}</option><option value="15">15 ${i18n.minutes}</option><option value="30">30 ${i18n.minutes}</option><option value="60">1 ${i18n.hour}</option><option value="120">2 ${i18n.hours}</option><option value="1440">24 ${i18n.hours}</option><option value="2880">48 ${i18n.hours}</option>
                    </select>
                </div>
            </div>
            <div>
                <label for="inviteExpiration" class="block mb-1.5 text-sm font-bold text-gray-700 dark:text-gray-300">${i18n.inviteExpiresIn}</label>
                <select id="inviteExpiration" class="w-full p-2.5 text-sm text-gray-900 bg-gray-50 rounded-lg border border-gray-300 focus:ring-yellow-500 focus:border-yellow-500 dark:bg-gray-800 dark:border-gray-600 dark:text-white">
                    <option value="0">${i18n.never}</option><option value="15">15 ${i18n.minutes}</option><option value="30">30 ${i18n.minutes}</option><option value="60">1 ${i18n.hour}</option><option value="1440">1 ${i18n.day}</option><option value="10080">7 ${i18n.days}</option>
                </select>
            </div>
            <div>
                <div class="flex justify-between items-center mb-2">
                    <h3 class="text-sm font-bold text-gray-700 dark:text-gray-300">${i18n.libraries}</h3>
                    <button type="button" id="inviteSelectAllLibs" class="text-xs bg-gray-200 dark:bg-gray-700 text-gray-800 dark:text-gray-200 hover:bg-gray-300 dark:hover:bg-gray-600 px-3 py-1 rounded-md transition-colors">${i18n.selectAll}</button>
                </div>
                <div id="inviteLibrariesList" class="max-h-40 overflow-y-auto bg-gray-50 dark:bg-gray-800 p-2 rounded-lg border border-gray-200 dark:border-gray-700 modal-body">
                    ${state.allLibraries.map(lib => `<label class="flex items-center space-x-3 p-1.5 hover:bg-gray-200 dark:hover:bg-gray-700 rounded transition-colors cursor-pointer"><input type="checkbox" class="form-checkbox h-4 w-4 bg-white dark:bg-gray-900 border-gray-300 dark:border-gray-500 rounded text-yellow-500 focus:ring-yellow-500 focus:ring-offset-0" value="${sanitizeHTML(lib.title)}"><span class="text-sm text-gray-800 dark:text-gray-200">${sanitizeHTML(lib.title)}</span></label>`).join('')}
                </div>
            </div>
            <div class="flex flex-col sm:flex-row gap-4 pt-4 border-t border-gray-200 dark:border-gray-700">
                <label class="flex items-center justify-between w-full p-2 border border-gray-200 dark:border-gray-700 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-800 cursor-pointer transition-colors">
                    <span class="text-sm font-medium text-gray-700 dark:text-gray-300">${i18n.overseerrAccess}</span>
                    <input type="checkbox" id="inviteOverseerrAccess" class="form-checkbox h-5 w-5 rounded text-yellow-500 focus:ring-yellow-500">
                </label>
                <label class="flex items-center justify-between w-full p-2 border border-gray-200 dark:border-gray-700 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-800 cursor-pointer transition-colors">
                    <span class="text-sm font-medium text-gray-700 dark:text-gray-300">${i18n.allowDownloads}</span>
                    <input type="checkbox" id="inviteAllowDownloads" class="form-checkbox h-5 w-5 rounded text-yellow-500 focus:ring-yellow-500">
                </label>
            </div>
        </div>`;
        
    const footer = `<button id="generateInviteButton" class="btn bg-green-600 hover:bg-green-500 text-white w-full sm:w-auto transition-colors">${i18n.generateInviteLink}</button>
                    <button id="modalCancel" class="${btnCancelClass}">${i18n.cancel}</button>`;
                    
    const modal = createModal('createInviteModal', i18n.createInvite, body, footer);

    modal.querySelector('#inviteSelectAllLibs').onclick = () => toggleSelectAll(modal.querySelector('#inviteLibrariesList'), modal.querySelector('#inviteSelectAllLibs'));
    modal.querySelector('#modalCancel').onclick = () => modal.classList.add('hidden');
    
    modal.querySelector('#generateInviteButton').onclick = async () => {
        const selectedLibraries = Array.from(modal.querySelectorAll('#inviteLibrariesList input:checked')).map(input => input.value);

        if (selectedLibraries.length === 0) {
            showToast(i18n.selectOneLibrary, 'error');
            return;
        }

        const button = modal.querySelector('#generateInviteButton');
        button.disabled = true;
        button.textContent = i18n.generating;

        const telegramInput = modal.querySelector('#inviteTelegramId');
        const telegramId = telegramInput ? telegramInput.value.trim() || null : null;

        try {
            const result = await api.createInvite({
                libraries: selectedLibraries,
                screens: parseInt(modal.querySelector('#inviteScreenLimit').value),
                allow_downloads: modal.querySelector('#inviteAllowDownloads').checked,
                expires_in_minutes: parseInt(modal.querySelector('#inviteExpiration').value),
                trial_duration_minutes: parseInt(modal.querySelector('#inviteTrialDuration').value),
                overseerr_access: modal.querySelector('#inviteOverseerrAccess').checked,
                custom_code: modal.querySelector('#inviteCustomCode').value.trim() || null,
                max_uses: parseInt(modal.querySelector('#inviteMaxUses').value) || 1,
                telegram_id: telegramId
            });

            if (result && result.success) {
                modal.classList.add('hidden');
                showToast(result.message, 'success');
                showInviteLinkModal(result.invite_url);
                document.dispatchEvent(new CustomEvent('data-refresh-requested'));
            } else {
                throw new Error(result.message);
            }
        } catch (error) {
            showToast(error.message, 'error');
        } finally {
            button.disabled = false;
            button.textContent = i18n.generateInviteLink;
        }
    };
}

export function showInviteLinkModal(inviteUrl) {
    const body = `
        <p class="mb-4 text-gray-700 dark:text-gray-300">${i18n.shareThisLink}</p>
        <div class="flex items-center relative">
            <input type="text" id="inviteLinkInput" readonly class="w-full pl-3 pr-24 py-3 text-sm font-mono text-gray-600 bg-gray-100 rounded-lg border border-gray-200 dark:bg-gray-800 dark:border-gray-700 dark:text-gray-300 focus:outline-none focus:ring-2 focus:ring-yellow-500/50">
            <button id="copyInviteLink" class="absolute right-1.5 px-4 py-1.5 rounded-md bg-blue-600 text-white text-sm font-bold hover:bg-blue-500 transition-colors">${i18n.copy}</button>
        </div>`;
        
    const footer = `<button id="modalClose" class="${btnCancelClass} w-full">${i18n.close}</button>`;
    const modal = createModal('showInviteLinkModal', i18n.inviteLinkGenerated, body, footer);

    // Atribuição de valor via JS previne XSS de injeção de atributo
    modal.querySelector('#inviteLinkInput').value = inviteUrl;

    modal.querySelector('#modalClose').onclick = () => modal.classList.add('hidden');
    // 🐛 CORREÇÃO: `copyToClipboard` devolve false quando falha (não lança), pelo
    // que o `catch` nunca corria e a mensagem de sucesso aparecia à mesma.
    modal.querySelector('#copyInviteLink').onclick = async () => {
        const copied = await copyToClipboard(inviteUrl).catch(() => false);
        showToast(
            copied ? i18n.linkCopied : (i18n.copyFailed || 'Falha ao copiar. Por favor, copie manualmente.'),
            copied ? 'success' : 'error'
        );
    };
}

// ==========================================
// MODAIS DE AÇÕES EM LOTE (BULK) E LIMITES
// ==========================================

export function showBulkActionsModal() {
    const body = `
        <p class="mb-5 text-gray-700 dark:text-gray-300">${i18n.bulkActionsDescription}</p>
        <div class="space-y-3">
            <button id="bulkUpdateLibs" class="btn bg-blue-600 hover:bg-blue-500 text-white w-full transition-colors">${i18n.updateAllLibsButton}</button>
            <button id="bulkUpdateLimits" class="btn bg-blue-600 hover:bg-blue-500 text-white w-full transition-colors">${i18n.updateAllLimitsButton}</button>
        </div>`;
    const footer = `<button id="modalCancel" class="${btnCancelClass} w-full">${i18n.cancel}</button>`;
    const modal = createModal('bulkActionsModal', i18n.bulkActionsTitle, body, footer);

    modal.querySelector('#modalCancel').onclick = () => modal.classList.add('hidden');
    modal.querySelector('#bulkUpdateLibs').onclick = () => {
        modal.classList.add('hidden');
        showLibraryManagementModal(null);
    };
    modal.querySelector('#bulkUpdateLimits').onclick = () => {
        modal.classList.add('hidden');
        showBulkScreenLimitModal();
    };
}

export function showBulkScreenLimitModal() {
    const body = `
        <p class="text-gray-700 dark:text-gray-300 mb-4">${i18n.selectNewLimitForAll}.</p>
        <div class="grid grid-cols-2 gap-3">
            <button data-screens="1" class="btn w-full bg-blue-600 hover:bg-blue-500 text-white transition-colors">1 ${i18n.screenSingular}</button>
            <button data-screens="2" class="btn w-full bg-blue-600 hover:bg-blue-500 text-white transition-colors">2 ${i18n.screenPlural}</button>
            <button data-screens="3" class="btn w-full bg-blue-600 hover:bg-blue-500 text-white transition-colors">3 ${i18n.screenPlural}</button>
            <button data-screens="4" class="btn w-full bg-blue-600 hover:bg-blue-500 text-white transition-colors">4 ${i18n.screenPlural}</button>
            <button data-screens="5" class="btn w-full bg-blue-600 hover:bg-blue-500 text-white transition-colors">5 ${i18n.screenPlural}</button>
            <button data-screens="6" class="btn w-full bg-blue-600 hover:bg-blue-500 text-white transition-colors">6 ${i18n.screenPlural}</button>
        </div>
        <button data-screens="-1" class="btn w-full bg-red-600 hover:bg-red-500 mt-3 text-white transition-colors">${i18n.removeAllLimits}</button>`;
        
    const modal = createModal('bulkScreenLimitModal', i18n.bulkScreenLimitTitle, body, `<button id="limitCancel" class="${btnCancelClass} w-full">${i18n.cancel}</button>`);
    
    modal.querySelectorAll('button[data-screens]').forEach(button => {
        button.onclick = async () => {
            const screens = parseInt(button.dataset.screens);
            try {
                const result = await api.updateAllLimits(screens);
                showToast(result.message, result.success ? 'success' : 'error');
                if (result.success) document.dispatchEvent(new CustomEvent('data-refresh-requested'));
                modal.classList.add('hidden');
            } catch (error) {
                showToast(error.message, 'error');
            }
        };
    });
    modal.querySelector('#limitCancel').onclick = () => modal.classList.add('hidden');
}

export function showScreenLimitModal(user) {
    const safeName = sanitizeHTML(user.username);
    const body = `
        <p class="text-gray-700 dark:text-gray-300 mb-4">${i18n.selectNewLimitFor} <strong class="text-gray-900 dark:text-white">${safeName}</strong>.</p>
        <div class="grid grid-cols-2 gap-3">
            <button data-screens="1" class="btn w-full bg-blue-600 hover:bg-blue-500 text-white transition-colors">1 ${i18n.screenSingular}</button>
            <button data-screens="2" class="btn w-full bg-blue-600 hover:bg-blue-500 text-white transition-colors">2 ${i18n.screenPlural}</button>
            <button data-screens="3" class="btn w-full bg-blue-600 hover:bg-blue-500 text-white transition-colors">3 ${i18n.screenPlural}</button>
            <button data-screens="4" class="btn w-full bg-blue-600 hover:bg-blue-500 text-white transition-colors">4 ${i18n.screenPlural}</button>
            <button data-screens="5" class="btn w-full bg-blue-600 hover:bg-blue-500 text-white transition-colors">5 ${i18n.screenPlural}</button>
            <button data-screens="6" class="btn w-full bg-blue-600 hover:bg-blue-500 text-white transition-colors">6 ${i18n.screenPlural}</button>
        </div>
        <button data-screens="0" class="btn w-full bg-gray-500 hover:bg-gray-400 mt-3 text-white transition-colors">${i18n.removeLimit}</button>`;
        
    const modal = createModal('screenLimitModal', i18n.manageScreenLimitTitle, body, `<button id="limitCancel" class="${btnCancelClass} w-full">${i18n.cancel}</button>`);
    
    modal.querySelectorAll('button[data-screens]').forEach(button => {
        button.onclick = async () => {
            const screens = parseInt(button.dataset.screens);
            try {
                const result = await api.updateUserLimit(user.id, screens);
                showToast(result.message, result.success ? 'success' : 'error');
                if (result.success) document.dispatchEvent(new CustomEvent('data-refresh-requested'));
                modal.classList.add('hidden');
            } catch (error) {
                showToast(error.message, 'error');
            }
        };
    });
    modal.querySelector('#limitCancel').onclick = () => modal.classList.add('hidden');
}

// ==========================================
// MODAIS DE PERFIL E EDIÇÃO DO UTILIZADOR
// ==========================================

export async function showLibraryManagementModal(user = null) {
    const isBulkUpdate = user === null;
    const title = isBulkUpdate ? i18n.updateLibsForAll : `${i18n.manageLibsFor} ${sanitizeHTML(user.username)}`;
    
    const body = `
        <div id="lib-modal-dynamic-body">
            <div class="flex flex-col justify-center items-center py-10">
                <div class="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500 mb-4"></div>
                <span class="text-gray-500 dark:text-gray-400 font-medium">${i18n.loadingLibraries || i18n.loadingLibs || 'Carregando bibliotecas...'}</span>
            </div>
        </div>`;
        
    const footer = `<div id="lib-modal-dynamic-footer" class="w-full flex justify-end"></div>`;
        
    const modal = createModal('libraryManagementModal', title, body, footer);

    try {
        let userLibraries = [];
        let allowSync = false;

        // 🐛 CORREÇÃO: este bloco chamava `fetch('/api/users/libraries/...')` com o
        // caminho escrito à mão, ignorando os URLs gerados por `url_for` e o
        // helper `fetchAPI` — logo, sem redirecionamento para o login quando a
        // sessão expira e sem tratamento de respostas que não sejam JSON.
        if (!isBulkUpdate) {
            const responseData = await api.fetchUserLibraries(user.id);
            if (!responseData || !responseData.success) {
                throw new Error((responseData && responseData.message) || i18n.errorLoadingLibs || 'Erro ao carregar bibliotecas.');
            }
            userLibraries = responseData.libraries || [];
            allowSync = responseData.allow_sync || false;
        }

        const allLibraries = state.allLibraries || [];
        
        let checkboxesHtml = allLibraries.map(lib => {
            const isChecked = userLibraries.includes(lib.title) ? 'checked' : '';
            return `
                <label class="flex items-center p-3 border border-gray-200 dark:border-gray-700 rounded-xl hover:bg-gray-50 dark:hover:bg-gray-700/50 cursor-pointer transition-colors">
                    <input type="checkbox" value="${sanitizeHTML(lib.title)}" class="library-checkbox w-5 h-5 text-blue-600 rounded focus:ring-blue-500 dark:bg-gray-800 dark:border-gray-600" ${isChecked}>
                    <span class="ml-3 text-gray-700 dark:text-gray-200 font-medium">${sanitizeHTML(lib.title)}</span>
                </label>
            `;
        }).join('');

        if (allLibraries.length === 0) {
            checkboxesHtml = `<p class="text-yellow-600 p-4 bg-yellow-50 dark:bg-yellow-900/30 rounded-xl text-center">${i18n.noLibrariesAvailable || 'Nenhuma biblioteca disponível para partilhar no seu Servidor Plex.'}</p>`;
        }

        // 🛡️ CORREÇÃO: Toggle Switch Padronizado TailwindCSS
        const allowSyncHtml = isBulkUpdate ? '' : `
            <div class="mt-4 pt-4 border-t border-gray-200 dark:border-gray-700">
                <label class="flex items-center justify-between w-full p-3 border border-gray-200 dark:border-gray-700 rounded-xl hover:bg-gray-50 dark:hover:bg-gray-800 cursor-pointer transition-colors group">
                    <div class="pr-4">
                        <span class="text-sm font-bold text-gray-900 dark:text-white block group-hover:text-blue-600 transition-colors">${i18n.allowDownloadsShort || 'Permitir Downloads'}</span>
                        <span class="text-xs text-gray-500 dark:text-gray-400 block">${i18n.allowDownloadsHint || 'Permite que o utilizador descarregue filmes e séries (Offline)'}</span>
                    </div>
                    <div class="relative inline-flex items-center cursor-pointer flex-shrink-0">
                        <input type="checkbox" id="modalAllowSync" class="sr-only peer" ${allowSync ? 'checked' : ''}>
                        <div class="w-11 h-6 bg-gray-200 rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all dark:border-gray-600 peer-checked:bg-blue-600"></div>
                    </div>
                </label>
            </div>
        `;

        const modalBody = modal.querySelector('#lib-modal-dynamic-body');
        const modalFooter = modal.querySelector('#lib-modal-dynamic-footer');
        
        modalBody.innerHTML = `
            <div class="flex justify-between items-center mb-4">
                <p class="text-sm text-gray-500 dark:text-gray-400">${i18n.selectAllowedAccess || 'Selecione o acesso permitido:'}</p>
                <button type="button" id="modalSelectAllLibs" class="text-xs bg-gray-200 dark:bg-gray-700 text-gray-800 dark:text-gray-200 hover:bg-gray-300 dark:hover:bg-gray-600 px-3 py-1.5 rounded-md transition-colors font-bold">${i18n.selectAll}</button>
            </div>
            <div class="space-y-2 max-h-[40vh] overflow-y-auto custom-scrollbar pr-2">
                ${checkboxesHtml}
            </div>
            ${allowSyncHtml}
        `;

        modalFooter.className = "flex flex-col sm:flex-row justify-end gap-3 w-full";
        modalFooter.innerHTML = `
            <button id="saveLibraryBtn" class="btn bg-blue-600 hover:bg-blue-500 text-white shadow-lg shadow-blue-500/30 w-full sm:w-auto transition-transform transform hover:-translate-y-0.5">${i18n.saveChanges || 'Guardar Alterações'}</button>
            <button id="libMgmtCancel" class="${btnCancelClass}">${i18n.cancel}</button>
        `;

        const selectAllButton = modal.querySelector('#modalSelectAllLibs');
        const saveButton = modal.querySelector('#saveLibraryBtn');
        const libsContainer = modalBody;

        selectAllButton.onclick = () => toggleSelectAll(libsContainer, selectAllButton);
        modal.querySelector('#libMgmtCancel').onclick = () => modal.classList.add('hidden');

        saveButton.onclick = async () => {
            const selectedLibs = Array.from(libsContainer.querySelectorAll('.library-checkbox:checked')).map(cb => cb.value);
            
            // Validação de segurança: Obriga a selecionar pelo menos uma biblioteca
            if (selectedLibs.length === 0) {
                showToast(i18n.selectOneLibrary || 'Pelo menos uma biblioteca deve ser selecionada.', 'error');
                return;
            }

            saveButton.disabled = true;
            saveButton.textContent = i18n.saving || 'A guardar...';
            
            const allowSyncToggle = modal.querySelector('#modalAllowSync');
            const allowSyncChecked = allowSyncToggle ? allowSyncToggle.checked : null;
            
            try {
                const result = isBulkUpdate
                    ? await api.updateAllLibraries(selectedLibs)
                    : await api.updateUserLibraries(user.id, selectedLibs, allowSyncChecked);

                if (result.success) {
                    showToast(result.message, 'success');
                    modal.classList.add('hidden');
                    document.dispatchEvent(new CustomEvent('data-refresh-requested'));
                } else {
                    throw new Error(result.message);
                }
            } catch (err) {
                showToast(err.message, 'error');
                saveButton.disabled = false;
                saveButton.textContent = i18n.saveChanges || 'Guardar Alterações';
            }
        };

    } catch (e) {
        const modalBody = modal.querySelector('#lib-modal-dynamic-body');
        const modalFooter = modal.querySelector('#lib-modal-dynamic-footer');
        
        modalBody.innerHTML = `
            <div class="text-center">
                <div class="inline-flex p-4 bg-red-100 dark:bg-red-900/30 text-red-500 rounded-full mb-4"><svg class="w-10 h-10" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg></div>
                <p class="text-red-500 dark:text-red-400 font-medium">${sanitizeHTML(i18n.syncError || 'Erro ao sincronizar')}: ${sanitizeHTML(e.message)}</p>
            </div>`;
            
        modalFooter.className = "w-full flex justify-end";
        modalFooter.innerHTML = `<button id="libMgmtCancel" class="${btnCancelClass} w-full sm:w-auto">${i18n.close}</button>`;
        modal.querySelector('#libMgmtCancel').onclick = () => modal.classList.add('hidden');
    }
}

export async function showUserProfileModal(user) {
    const footer = `
        <button id="saveProfileButton" class="btn bg-green-600 hover:bg-green-500 text-white transition-colors w-full sm:w-auto disabled:opacity-50">${i18n.save}</button>
        <button id="modalCancel" class="${btnCancelClass}">${i18n.cancel}</button>`;
        
    const modal = createModal('userProfileModal', `${i18n.manageProfileTitle} <span class="text-blue-600 dark:text-blue-500">${sanitizeHTML(user.username)}</span>`, 
        `<div id="profile-modal-dynamic-body"><div class="flex justify-center py-10"><div class="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500"></div></div></div>`, footer);
        
    const modalBody = modal.querySelector('#profile-modal-dynamic-body');

    try {
        const data = await api.fetchUserProfile(user.id);
        if (!data.success) throw new Error(data.message);

        const profile = data.profile || {};
        const notificationSettings = data.notification_settings || {};
        const universalExpiration = data.universal_expiration_settings || {};

        let expirationDateValue = '';
        let expirationTimeValue = '00:00';
        if (profile.expiration_date) {
            try {
                const expDate = new Date(profile.expiration_date);
                const year = expDate.getFullYear();
                const month = String(expDate.getMonth() + 1).padStart(2, '0');
                const day = String(expDate.getDate()).padStart(2, '0');
                expirationDateValue = `${year}-${month}-${day}`;
                // 🐛 CORREÇÃO: a hora vinha de `toLocaleTimeString('pt-BR', ...)`, mas
                // <input type="time"> exige sempre "HH:MM" em 24 horas. Noutro idioma
                // o resultado seria "11:30 PM" e o campo ficaria vazio. Construir a
                // partir dos getters locais (como a data, acima) é inequívoco em
                // qualquer idioma e evita as diferenças entre os ciclos h23/h24.
                const hours = String(expDate.getHours()).padStart(2, '0');
                const minutes = String(expDate.getMinutes()).padStart(2, '0');
                expirationTimeValue = `${hours}:${minutes}`;
            } catch (e) {}
        }

        // Criar estrutura de HTML limpa. Injeção de valores é feita por JS para evitar XSS de aspas.
        modalBody.innerHTML = `
            <div class="space-y-4">
                <div>
                    <label for="profileName" class="block mb-1.5 text-sm font-bold text-gray-700 dark:text-gray-300">${i18n.fullName}</label>
                    <input type="text" id="profileName" class="w-full p-2.5 text-sm bg-gray-50 border border-gray-300 text-gray-900 rounded-lg focus:ring-2 focus:ring-blue-500 dark:bg-gray-800 dark:border-gray-600 dark:text-white transition-shadow">
                </div>
                <div id="telegram-field-container" class="${notificationSettings.telegram_enabled ? '' : 'hidden'}">
                    <label for="profileTelegram" class="block mb-1.5 text-sm font-bold text-gray-700 dark:text-gray-300">${i18n.telegramUser}</label>
                    <input type="text" id="profileTelegram" class="w-full p-2.5 text-sm bg-gray-50 border border-gray-300 text-gray-900 rounded-lg focus:ring-2 focus:ring-blue-500 dark:bg-gray-800 dark:border-gray-600 dark:text-white transition-shadow">
                </div>
                <div id="discord-field-container" class="${notificationSettings.discord_enabled ? '' : 'hidden'}">
                    <label for="profileDiscord" class="block mb-1.5 text-sm font-bold text-gray-700 dark:text-gray-300">${i18n.discordUserId}</label>
                    <input type="text" id="profileDiscord" class="w-full p-2.5 text-sm bg-gray-50 border border-gray-300 text-gray-900 rounded-lg focus:ring-2 focus:ring-blue-500 dark:bg-gray-800 dark:border-gray-600 dark:text-white transition-shadow">
                </div>
                <!-- 🐛 CORREÇÃO: o número de telefone estava amarrado APENAS ao Webhook
                     genérico. Com a integração de WhatsApp, desligar o webhook escondia
                     o campo, impedindo o administrador de preencher o número — e sem
                     número não há notificação de WhatsApp nenhuma. O campo deve aparecer
                     se QUALQUER canal que use telefone estiver ativo. -->
                <div id="phone-field-container" class="${(notificationSettings.webhook_enabled || notificationSettings.whatsapp_enabled) ? '' : 'hidden'}">
                    <label for="profilePhone" class="block mb-1.5 text-sm font-bold text-gray-700 dark:text-gray-300">${i18n.phoneNumber}</label>
                    <input type="tel" id="profilePhone" class="w-full p-2.5 text-sm bg-gray-50 border border-gray-300 text-gray-900 rounded-lg focus:ring-2 focus:ring-blue-500 dark:bg-gray-800 dark:border-gray-600 dark:text-white transition-shadow">
                </div>
                
                <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                        <label for="profileExpiration" class="block mb-1.5 text-sm font-bold text-gray-700 dark:text-gray-300">${i18n.expirationDate}</label>
                        <input type="date" id="profileExpiration" class="w-full p-2.5 text-sm bg-gray-50 border border-gray-300 text-gray-900 rounded-lg focus:ring-2 focus:ring-blue-500 dark:bg-gray-800 dark:border-gray-600 dark:text-white transition-shadow">
                    </div>
                    <div id="expiration-time-container">
                        <label for="profileExpirationTime" class="block mb-1.5 text-sm font-bold text-gray-700 dark:text-gray-300">${i18n.blockTime}</label>
                        <input type="time" id="profileExpirationTime" class="w-full p-2.5 text-sm bg-gray-50 border border-gray-300 text-gray-900 rounded-lg focus:ring-2 focus:ring-blue-500 dark:bg-gray-800 dark:border-gray-600 dark:text-white disabled:opacity-50 transition-shadow" ${universalExpiration.enabled ? 'disabled' : ''}>
                        <p id="universal-time-notice" class="${universalExpiration.enabled ? '' : 'hidden'} text-xs font-semibold text-blue-600 dark:text-blue-500 mt-1">${i18n.universalTimeActive}: ${universalExpiration.time}</p>
                    </div>
                </div>
                
                <div class="flex items-center justify-between pt-4 border-t border-gray-200 dark:border-gray-700">
                    <label for="profileOverseerrAccess" class="text-sm font-bold text-gray-700 dark:text-gray-300 cursor-pointer">${i18n.overseerrAccess}</label>
                    <input type="checkbox" id="profileOverseerrAccess" class="form-checkbox h-5 w-5 rounded text-blue-500 focus:ring-blue-500" ${profile.overseerr_access ? 'checked' : ''}>
                </div>
                
                <div class="mt-4 pt-4 border-t border-gray-200 dark:border-gray-700/50">
                    <label class="block mb-2 text-sm font-bold text-gray-700 dark:text-gray-300">${i18n.renewSubscription}</label>
                    <div class="space-y-2">
                        <div class="flex items-center gap-2">
                            <input type="number" id="renew-months" value="1" min="1" class="w-24 text-center bg-gray-50 border border-gray-300 dark:bg-gray-800 dark:border-gray-600 rounded-lg p-2 focus:ring-blue-500">
                            <button type="button" id="confirm-renew" class="btn bg-sky-600 hover:bg-sky-500 text-white flex-1 transition-colors">${i18n.addMonths}</button>
                        </div>
                        <button type="button" id="renew-same-day" class="btn bg-gray-600 hover:bg-gray-500 text-white w-full transition-colors">${i18n.renewSameDay}</button>
                    </div>
                </div>
                
                <div class="mt-4 pt-4 border-t border-gray-200 dark:border-gray-700/50">
                    <label class="block mb-2 text-sm font-bold text-gray-700 dark:text-gray-300">${i18n.manualActions}</label>
                    <button id="sendNotificationButton" class="btn bg-blue-600 hover:bg-blue-500 text-white w-full transition-colors disabled:opacity-50 disabled:cursor-not-allowed" ${!profile.expiration_date ? 'disabled' : ''}>${i18n.sendExpirationNotification}</button>
                </div>
            </div>
        `;

        // Preenchimento seguro de valores para prevenir XSS
        modal.querySelector('#profileName').value = profile.name || '';
        modal.querySelector('#profileTelegram').value = profile.telegram_user || '';
        modal.querySelector('#profileDiscord').value = profile.discord_user_id || '';
        modal.querySelector('#profilePhone').value = profile.phone_number || '';
        modal.querySelector('#profileExpiration').value = expirationDateValue;
        modal.querySelector('#profileExpirationTime').value = expirationTimeValue;

        const expirationTimeInput = modal.querySelector('#profileExpirationTime');
        const saveButton = modal.querySelector('#saveProfileButton');
        
        modal.querySelector('#modalCancel').onclick = () => modal.classList.add('hidden');
        
        saveButton.onclick = async () => {
            saveButton.disabled = true;
            const dateValue = modal.querySelector('#profileExpiration').value;
            const timeValue = expirationTimeInput.value || '00:00';
            const localDateTimeString = dateValue ? `${dateValue}T${timeValue}` : null;
            
            const profileData = {
                name: modal.querySelector('#profileName').value.trim(),
                telegram_user: modal.querySelector('#profileTelegram').value.trim(),
                discord_user_id: modal.querySelector('#profileDiscord').value.trim(),
                phone_number: modal.querySelector('#profilePhone').value.trim(),
                expiration_datetime_local: localDateTimeString
            };
            
            try {
                const result = await api.updateUserProfile(user.id, profileData);
                showToast(result.message, result.success ? 'success' : 'error');
                if (result.success) { 
                    modal.classList.add('hidden'); 
                    document.dispatchEvent(new CustomEvent('data-refresh-requested')); 
                }
            } catch (error) {
                showToast(error.message, 'error');
            } finally {
                saveButton.disabled = false;
            }
        };

        modal.querySelector('#profileOverseerrAccess').onchange = async (e) => {
            try {
                const result = await api.toggleOverseerr(user.id, e.target.checked);
                showToast(result.message, result.success ? 'success' : 'error');
            } catch (error) {
                showToast(error.message, 'error');
                e.target.checked = !e.target.checked; // revert
            }
        };

        const sendNotificationButton = modal.querySelector('#sendNotificationButton');
        if(sendNotificationButton) {
            sendNotificationButton.onclick = async () => {
                sendNotificationButton.disabled = true;
                const origText = sendNotificationButton.textContent;
                sendNotificationButton.textContent = i18n.sending || 'Enviando...';
                try {
                    const result = await api.notifyUser(user.id);
                    showToast(result.message, result.success ? 'success' : 'error');
                } catch (error) {
                    showToast(error.message, 'error');
                } finally {
                    sendNotificationButton.textContent = origText;
                    sendNotificationButton.disabled = false;
                }
            };
        }

        const handleRenewal = async (button, payload) => {
            const origText = button.textContent;
            button.disabled = true;
            button.textContent = '...';
            
            const expirationInput = modal.querySelector('#profileExpiration');
            if (expirationInput && expirationInput.value) payload.base_date = expirationInput.value;
            const timeInput = expirationTimeInput;
            if (timeInput && timeInput.value && !timeInput.disabled) payload.expiration_time = timeInput.value;

            try {
                const result = await api.renewSubscription(user.id, payload);
                showToast(result.message, result.success ? 'success' : 'error');
                if (result.success) { 
                    modal.classList.add('hidden'); 
                    document.dispatchEvent(new CustomEvent('data-refresh-requested')); 
                }
            } catch (error) {
                showToast(error.message, 'error');
            } finally {
                button.textContent = origText;
                button.disabled = false;
            }
        };

        modal.querySelector('#confirm-renew').onclick = (e) => {
            const months = parseInt(modal.querySelector('#renew-months').value) || 1;
            handleRenewal(e.target, { months, base: 'today' });
        };
        
        modal.querySelector('#renew-same-day').onclick = (e) => {
            handleRenewal(e.target, { months: 1, base: 'expiry_date' });
        };

    } catch (error) {
        modalBody.innerHTML = `<div class="text-center py-6 text-red-500"><svg class="w-12 h-12 mx-auto mb-2" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg><p>${i18n.errorLoadingProfile}: ${error.message}</p></div>`;
    }
}

export function showExtendTrialModal(user) {
    const safeUsername = sanitizeHTML(user.username);
    let currentTrialEndDateFormatted = i18n.notAvailable || 'Não disponível';
    
    if (user.trial_end_date) {
        try {
            currentTrialEndDateFormatted = formatDateTime(user.trial_end_date);
        } catch (e) {}
    }

    const body = `
        <p class="mb-4 text-sm text-gray-600 dark:text-gray-400 bg-gray-50 dark:bg-gray-800/50 p-3 rounded-lg border border-gray-200 dark:border-gray-700">
            <span class="font-bold text-gray-800 dark:text-gray-200">${i18n.currentTrialEnds}:</span><br>
            ${currentTrialEndDateFormatted}
        </p>
        <div class="grid grid-cols-2 gap-4">
            <div>
                <label for="extend-trial-hours-modal" class="block mb-1.5 text-sm font-bold text-gray-700 dark:text-gray-300">${i18n.hours}</label>
                <input type="number" id="extend-trial-hours-modal" value="24" min="0" class="w-full text-center bg-gray-50 border border-gray-300 dark:bg-gray-800 dark:border-gray-600 rounded-lg p-2 focus:ring-blue-500">
            </div>
            <div>
                <label for="extend-trial-minutes-modal" class="block mb-1.5 text-sm font-bold text-gray-700 dark:text-gray-300">${i18n.minutes}</label>
                <input type="number" id="extend-trial-minutes-modal" value="0" min="0" step="15" class="w-full text-center bg-gray-50 border border-gray-300 dark:bg-gray-800 dark:border-gray-600 rounded-lg p-2 focus:ring-blue-500">
            </div>
        </div>
    `;

    const footer = `
        <button id="modalConfirmExtendTrial" class="btn bg-orange-600 hover:bg-orange-500 text-white w-full sm:w-auto transition-colors">${i18n.extendTrial}</button>
        <button id="modalCancelExtendTrial" class="${btnCancelClass}">${i18n.cancel}</button>
    `;
    
    const modal = createModal('extendTrialModal', `${i18n.extendTrialPeriod} - <span class="text-orange-600 dark:text-orange-500">${safeUsername}</span>`, body, footer);

    const confirmButton = modal.querySelector('#modalConfirmExtendTrial');
    modal.querySelector('#modalCancelExtendTrial').onclick = () => modal.classList.add('hidden');

    confirmButton.onclick = async () => {
        const hours = parseInt(modal.querySelector('#extend-trial-hours-modal').value) || 0; 
        const minutes = parseInt(modal.querySelector('#extend-trial-minutes-modal').value) || 0; 
        const extend_minutes = (hours * 60) + minutes; 

        if (isNaN(extend_minutes) || extend_minutes <= 0) {
            showToast(i18n.invalidExtensionDuration || 'Duração inválida.', 'error');
            return;
        }

        confirmButton.disabled = true;
        confirmButton.textContent = i18n.extending || 'A estender...';

        try {
            const result = await api.extendTrial(user.id, { extend_minutes });
            showToast(result.message, result.success ? 'success' : 'error');
            if (result.success) {
                modal.classList.add('hidden');
                document.dispatchEvent(new CustomEvent('data-refresh-requested'));
            }
        } catch (error) {
            showToast(error.message, 'error');
        } finally {
            confirmButton.disabled = false;
            confirmButton.textContent = i18n.extendTrial;
        }
    };
}

export async function showPaymentHistoryModal(user) {
    const loadingHtml = `
        <div class="text-center py-10">
            <svg class="animate-spin h-8 w-8 text-blue-500 mx-auto" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
            <p class="mt-4 text-gray-500 dark:text-gray-400 font-medium">${i18n.loadingHistory}</p>
        </div>`;
        
    const body = `<div id="paymentHistoryContainer" class="max-h-[60vh] overflow-y-auto pr-2 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50">${loadingHtml}</div>`;
    const footer = `<button id="modalClose" class="${btnCancelClass} w-full">${i18n.close}</button>`;
    
    const modal = createModal('paymentHistoryModal', `${i18n.paymentHistory} - <span class="text-blue-600 dark:text-blue-500">${sanitizeHTML(user.username)}</span>`, body, footer);
    modal.querySelector('#modalClose').onclick = () => modal.classList.add('hidden');

    const container = modal.querySelector('#paymentHistoryContainer');
    
    try {
        const result = await api.fetchPaymentHistory(user.id);
        if (result.success && result.payments.length > 0) {
            container.innerHTML = `
                <table class="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
                    <thead class="bg-gray-100 dark:bg-gray-900/80 sticky top-0 shadow-sm z-10">
                        <tr>
                            <th class="px-4 py-3 text-left text-xs font-bold text-gray-600 dark:text-gray-400 uppercase tracking-wider">${i18n.date}</th>
                            <th class="px-4 py-3 text-left text-xs font-bold text-gray-600 dark:text-gray-400 uppercase tracking-wider">${i18n.description}</th>
                            <th class="px-4 py-3 text-left text-xs font-bold text-gray-600 dark:text-gray-400 uppercase tracking-wider">${i18n.value}</th>
                            <th class="px-4 py-3 text-left text-xs font-bold text-gray-600 dark:text-gray-400 uppercase tracking-wider">${i18n.status}</th>
                        </tr>
                    </thead>
                    <tbody class="bg-white dark:bg-gray-800 divide-y divide-gray-200 dark:divide-gray-700">
                        ${result.payments.map(p => {
                            const screensLabel = p.screens > 1 ? i18n.screenPlural : i18n.screenSingular;
                            const desc = p.description || `${p.provider} - ${p.screens > 0 ? `${p.screens} ${screensLabel}` : (i18n.default || 'Padrão')}`;
                            const isOk = p.status === 'CONCLUIDA';
                            const badgeClass = isOk ? 'bg-green-100 text-green-800 dark:bg-green-900/50 dark:text-green-300' : 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/50 dark:text-yellow-300';
                            
                            return `
                                <tr class="hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors">
                                    <td class="px-4 py-3 whitespace-nowrap text-sm text-gray-700 dark:text-gray-300">${formatDateTime(p.created_at)}</td>
                                    <td class="px-4 py-3 text-sm text-gray-800 dark:text-gray-200">${sanitizeHTML(desc)}</td>
                                    <td class="px-4 py-3 text-sm font-mono font-bold text-gray-900 dark:text-white">${p.value.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })}</td>
                                    <td class="px-4 py-3 text-sm"><span class="px-2.5 py-1 inline-flex text-xs leading-5 font-semibold rounded-full ${badgeClass}">${sanitizeHTML(p.status)}</span></td>
                                </tr>
                            `;
                        }).join('')}
                    </tbody>
                </table>
            `;
        } else {
            container.innerHTML = `<p class="text-gray-500 dark:text-gray-400 text-center py-8">${i18n.noPaymentsFound}</p>`;
        }
    } catch (error) {
        container.innerHTML = `<div class="text-center py-6 text-red-500"><svg class="w-12 h-12 mx-auto mb-2" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg><p>${error.message}</p></div>`;
    }
}

export async function showReactivationModal(user) {
    const title = `${i18n.reactivateUserTitle} - <span class="text-green-600 dark:text-green-500">${sanitizeHTML(user.username)}</span>`;
    const body = `
        <p class="mb-4 text-sm text-gray-700 dark:text-gray-300">${i18n.selectLibsForReactivation.replace('{username}', `<strong class="text-gray-900 dark:text-white">${sanitizeHTML(user.username)}</strong>`)}</p>
        <div class="flex justify-end mb-2">
            <button type="button" id="modalSelectAllLibs" class="text-xs bg-gray-200 dark:bg-gray-700 text-gray-800 dark:text-gray-200 hover:bg-gray-300 dark:hover:bg-gray-600 px-3 py-1 rounded-md transition-colors">${i18n.selectAll}</button>
        </div>
        <div id="modalLibsContainer" class="max-h-48 overflow-y-auto bg-gray-50 dark:bg-gray-800 p-2 rounded-lg border border-gray-200 dark:border-gray-700 space-y-1 modal-body">
            ${state.allLibraries.map(lib => `
                <label class="flex items-center space-x-3 p-1.5 hover:bg-gray-200 dark:hover:bg-gray-700 rounded transition-colors cursor-pointer">
                    <input type="checkbox" class="library-checkbox form-checkbox h-4 w-4 bg-white dark:bg-gray-900 border-gray-300 dark:border-gray-500 rounded text-blue-500 focus:ring-blue-500 focus:ring-offset-0" value="${sanitizeHTML(lib.title)}">
                    <span class="text-sm text-gray-800 dark:text-gray-200">${sanitizeHTML(lib.title)}</span>
                </label>`).join('')}
        </div>`;
        
    const footer = `
        <button id="modalConfirm" class="btn bg-green-600 hover:bg-green-500 text-white w-full sm:w-auto transition-colors">${i18n.confirmReactivateButton}</button>
        <button id="modalCancel" class="${btnCancelClass}">${i18n.cancel}</button>`;

    const modal = createModal('reactivationModal', title, body, footer);

    const modalLibsContainer = modal.querySelector('#modalLibsContainer');
    const selectAllButton = modal.querySelector('#modalSelectAllLibs');
    const confirmButton = modal.querySelector('#modalConfirm');

    selectAllButton.onclick = () => toggleSelectAll(modalLibsContainer, selectAllButton);
    modal.querySelector('#modalCancel').onclick = () => modal.classList.add('hidden');

    confirmButton.onclick = async () => {
        const selectedLibraries = Array.from(modalLibsContainer.querySelectorAll('input:checked')).map(input => input.value);
        if (selectedLibraries.length === 0) {
            showToast(i18n.selectOneLibrary, 'error');
            return;
        }

        confirmButton.disabled = true;
        confirmButton.textContent = i18n.reactivating || 'A reativar...';

        try {
            const result = await api.reactivateUser(user.id, selectedLibraries);
            showToast(result.message, result.success ? 'success' : 'error');
            if (result.success) {
                modal.classList.add('hidden');
                document.dispatchEvent(new CustomEvent('data-refresh-requested'));
            }
        } catch (error) {
            showToast(error.message, 'error');
        } finally {
            confirmButton.disabled = false;
            confirmButton.textContent = i18n.confirmReactivateButton;
        }
    };
}