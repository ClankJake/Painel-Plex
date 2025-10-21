// app/static/js/users_modules/modals.js

import { createModal, showToast } from '../utils.js';
import { i18n } from './config.js';
import * as state from './state.js';
import * as api from './api.js';

/**
 * Módulo de Modais
 * * Contém todas as funções para criar e gerir os diálogos modais
 * na página de utilizadores.
 */

// --- Funções Auxiliares de UI ---

function toggleSelectAll(container, button) {
    const checkboxes = container.querySelectorAll('input[type="checkbox"]');
    const areAllSelected = Array.from(checkboxes).every(cb => cb.checked);
    checkboxes.forEach(cb => cb.checked = !areAllSelected);
    button.textContent = areAllSelected ? i18n.selectAll : i18n.unselectAll;
}

// --- Funções de Modais ---

export function showConfirmationModal({ title, message, confirmText, confirmClass, onConfirm }) {
    const modal = createModal('confirmationModal', title, `<p>${message}</p>`,
        `<button id="modalConfirm" class="btn ${confirmClass} w-full sm:w-auto">${confirmText}</button>
         <button id="modalCancel" class="btn bg-gray-200 text-gray-800 hover:bg-gray-300 dark:bg-gray-600 dark:text-gray-200 dark:hover:bg-gray-500 w-full sm:w-auto">${i18n.cancel}</button>`
    );
    modal.querySelector('#modalConfirm').onclick = () => { onConfirm(); modal.classList.add('hidden'); };
    modal.querySelector('#modalCancel').onclick = () => modal.classList.add('hidden');
}

export function showCreateInviteModal() {
    const body = `<div class="space-y-4">
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div>
                            <label for="inviteCustomCode" class="block mb-2 text-sm font-medium text-gray-500 dark:text-gray-400">${i18n.customCode}</label>
                            <input type="text" id="inviteCustomCode" class="w-full p-2.5 text-sm text-gray-900 bg-gray-50 rounded-lg border border-gray-300 focus:ring-yellow-500 focus:border-yellow-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white" placeholder="${i18n.optional}">
                        </div>
                        <div>
                            <label for="inviteMaxUses" class="block mb-2 text-sm font-medium text-gray-500 dark:text-gray-400">${i18n.numberOfUses}</label>
                            <input type="number" id="inviteMaxUses" value="1" min="1" class="w-full p-2.5 text-sm text-gray-900 bg-gray-50 rounded-lg border border-gray-300 focus:ring-yellow-500 focus:border-yellow-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white">
                        </div>
                    </div>
                    <div>
                        <label for="inviteScreenLimit" class="block mb-2 text-sm font-medium text-gray-500 dark:text-gray-400">${i18n.screenLimit}</label>
                        <select id="inviteScreenLimit" class="w-full p-2.5 text-sm text-gray-900 bg-gray-50 rounded-lg border border-gray-300 focus:ring-yellow-500 focus:border-yellow-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white">
                            <option value="0">${i18n.noLimit}</option><option value="1">1 ${i18n.screenSingular}</option><option value="2">2 ${i18n.screenPlural}</option><option value="3">3 ${i18n.screenPlural}</option><option value="4">4 ${i18n.screenPlural}</option>
                        </select>
                    </div>
                    <div>
                        <label for="inviteExpiration" class="block mb-2 text-sm font-medium text-gray-500 dark:text-gray-400">${i18n.inviteExpiresIn}</label>
                        <select id="inviteExpiration" class="w-full p-2.5 text-sm text-gray-900 bg-gray-50 rounded-lg border border-gray-300 focus:ring-yellow-500 focus:border-yellow-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white">
                            <option value="0">${i18n.never}</option><option value="15">15 ${i18n.minutes}</option><option value="30">30 ${i18n.minutes}</option><option value="60">1 ${i18n.hour}</option><option value="1440">1 ${i18n.day}</option><option value="10080">7 ${i18n.days}</option>
                        </select>
                    </div>
                    <div>
                        <label for="inviteTrialDuration" class="block mb-2 text-sm font-medium text-gray-500 dark:text-gray-400">${i18n.trialDuration}</label>
                        <select id="inviteTrialDuration" class="w-full p-2.5 text-sm text-gray-900 bg-gray-50 rounded-lg border border-gray-300 focus:ring-yellow-500 focus:border-yellow-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white">
                            <option value="0">${i18n.noTrial}</option><option value="15">15 ${i18n.minutes}</option><option value="30">30 ${i18n.minutes}</option><option value="60">1 ${i18n.hour}</option><option value="120">2 ${i18n.hour}s</option><option value="1440">24 ${i18n.hour}s</option><option value="2880">48 ${i18n.hour}s</option>
                        </select>
                    </div>
                    <div>
                        <div class="flex justify-between items-center mb-2">
                            <h3 class="text-sm font-medium text-gray-500 dark:text-gray-400">${i18n.libraries}</h3>
                            <button type="button" id="inviteSelectAllLibs" class="text-xs bg-gray-200 dark:bg-gray-600 hover:bg-gray-300 dark:hover:bg-gray-500 px-2 py-1 rounded-md">${i18n.selectAll}</button>
                        </div>
                        <div id="inviteLibrariesList" class="max-h-40 overflow-y-auto bg-gray-100 dark:bg-gray-900/50 p-2 rounded-lg border border-gray-300 dark:border-gray-600 modal-body">
                            ${state.allLibraries.map(lib => `<label class="flex items-center space-x-2 p-1 hover:bg-gray-200 dark:hover:bg-gray-600 rounded"><input type="checkbox" class="form-checkbox bg-gray-100 dark:bg-gray-900 border-gray-300 dark:border-gray-500 rounded text-yellow-500" value="${lib.title}"><span>${lib.title}</span></label>`).join('')}
                        </div>
                    </div>
                    <div class="flex items-center justify-between pt-3 border-t border-gray-200 dark:border-gray-700">
                        <label for="inviteOverseerrAccess" class="text-sm font-medium text-gray-600 dark:text-gray-300">${i18n.overseerrAccess}</label>
                        <input type="checkbox" id="inviteOverseerrAccess" class="form-checkbox h-5 w-5 rounded text-yellow-500">
                    </div>
                    <div class="flex items-center justify-between pt-3 border-t border-gray-200 dark:border-gray-700">
                        <label for="inviteAllowDownloads" class="text-sm font-medium text-gray-600 dark:text-gray-300">${i18n.allowDownloads}</label>
                        <input type="checkbox" id="inviteAllowDownloads" class="form-checkbox h-5 w-5 rounded text-yellow-500 bg-gray-200 dark:bg-gray-700 border-gray-300 dark:border-gray-600 focus:ring-yellow-600 focus:ring-offset-0">
                    </div>
                </div>`;
    const footer = `<button id="generateInviteButton" class="btn bg-green-600 hover:bg-green-500 text-white w-full sm:w-auto">${i18n.generateInviteLink}</button>
                   <button id="modalCancel" class="btn bg-gray-200 text-gray-800 dark:bg-gray-600 dark:text-gray-200 w-full sm:w-auto">${i18n.cancel}</button>`;
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
            });

            if (result && result.success) {
                modal.classList.add('hidden');
                showToast(result.message, 'success');
                showInviteLinkModal(result.invite_url);
                document.dispatchEvent(new CustomEvent('data-refresh-requested'));
            } else {
                showToast(result.message, 'error');
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
    const body = `<p class="mb-4">${i18n.shareThisLink}</p>
                  <div class="flex items-center space-x-2">
                    <input type="text" id="inviteLinkInput" readonly value="${inviteUrl}" class="w-full p-2 text-sm text-gray-900 bg-gray-200 rounded-lg border-none dark:bg-gray-700 dark:text-white">
                    <button id="copyInviteLink" class="p-2 rounded-md bg-blue-500 text-white hover:bg-blue-600">${i18n.copy}</button>
                  </div>`;
    const footer = `<button id="modalClose" class="btn bg-gray-200 text-gray-800 dark:bg-gray-600 dark:text-gray-200 w-full">${i18n.close}</button>`;
    const modal = createModal('showInviteLinkModal', i18n.inviteLinkGenerated, body, footer);
    
    modal.querySelector('#modalClose').onclick = () => modal.classList.add('hidden');
    modal.querySelector('#copyInviteLink').onclick = () => {
        const input = modal.querySelector('#inviteLinkInput');
        input.select();
        document.execCommand('copy');
        showToast(i18n.linkCopied, 'success');
    };
}

export function showBulkActionsModal() {
    const body = `<p class="mb-4">${i18n.bulkActionsDescription}</p>
                  <div class="space-y-3">
                    <button id="bulkUpdateLibs" class="btn bg-blue-600 hover:bg-blue-500 text-white w-full">${i18n.updateAllLibsButton}</button>
                    <button id="bulkUpdateLimits" class="btn bg-blue-600 hover:bg-blue-500 text-white w-full">${i18n.updateAllLimitsButton}</button>
                  </div>`;
    const footer = `<button id="modalCancel" class="btn bg-gray-200 text-gray-800 dark:bg-gray-600 dark:text-gray-200 w-full">${i18n.cancel}</button>`;
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
    const body = `<p>${i18n.selectNewLimitForAll}.</p>
                  <div class="grid grid-cols-2 gap-4 mt-4">
                    <button data-screens="1" class="btn w-full bg-blue-600 hover:bg-blue-700 text-white">1 ${i18n.screenSingular}</button>
                    <button data-screens="2" class="btn w-full bg-blue-600 hover:bg-blue-700 text-white">2 ${i18n.screenPlural}</button>
                    <button data-screens="3" class="btn w-full bg-blue-600 hover:bg-blue-700 text-white">3 ${i18n.screenPlural}</button>
                    <button data-screens="4" class="btn w-full bg-blue-600 hover:bg-blue-700 text-white">4 ${i18n.screenPlural}</button>
                  </div>
                  <button data-screens="-1" class="btn w-full bg-gray-500 hover:bg-gray-600 mt-4 text-white">${i18n.removeAllLimits}</button>`;
    const modal = createModal('bulkScreenLimitModal', i18n.bulkScreenLimitTitle, body, `<button id="limitCancel" class="btn bg-gray-200 text-gray-800 dark:bg-gray-600 dark:text-gray-200 w-full">${i18n.cancel}</button>`);
    modal.querySelectorAll('button[data-screens]').forEach(button => {
        button.onclick = async () => {
            const screens = parseInt(button.dataset.screens);
            try {
                const result = await api.updateAllLimits(screens);
                showToast(result.message, result.success ? 'success' : 'error');
                if (result.success) {
                    document.dispatchEvent(new CustomEvent('data-refresh-requested'));
                }
                modal.classList.add('hidden');
            } catch (error) {
                showToast(error.message, 'error');
            }
        };
    });
    modal.querySelector('#limitCancel').onclick = () => modal.classList.add('hidden');
}

export function showScreenLimitModal(user) {
    const body = `<p>${i18n.selectNewLimitFor} <strong>${user.username}</strong>.</p>
                  <div class="grid grid-cols-2 gap-4 mt-4">
                    <button data-screens="1" class="btn w-full bg-blue-600 hover:bg-blue-700 text-white">1 ${i18n.screenSingular}</button>
                    <button data-screens="2" class="btn w-full bg-blue-600 hover:bg-blue-700 text-white">2 ${i18n.screenPlural}</button>
                    <button data-screens="3" class="btn w-full bg-blue-600 hover:bg-blue-700 text-white">3 ${i18n.screenPlural}</button>
                    <button data-screens="4" class="btn w-full bg-blue-600 hover:bg-blue-700 text-white">4 ${i18n.screenPlural}</button>
                  </div>
                  <button data-screens="0" class="btn w-full bg-gray-500 hover:bg-gray-600 mt-4 text-white">${i18n.removeLimit}</button>`;
    const modal = createModal('screenLimitModal', i18n.manageScreenLimitTitle, body, `<button id="limitCancel" class="btn bg-gray-200 text-gray-800 dark:bg-gray-600 dark:text-gray-200 w-full">${i18n.cancel}</button>`);
    modal.querySelectorAll('button[data-screens]').forEach(button => {
        button.onclick = async () => {
            const screens = parseInt(button.dataset.screens);
            try {
                const result = await api.updateUserLimit(user.id, screens);
                showToast(result.message, result.success ? 'success' : 'error');
                if (result.success) {
                    document.dispatchEvent(new CustomEvent('data-refresh-requested'));
                }
                modal.classList.add('hidden');
            } catch (error) {
                showToast(error.message, 'error');
            }
        };
    });
    modal.querySelector('#limitCancel').onclick = () => modal.classList.add('hidden');
}

export async function showLibraryManagementModal(user = null) {
    const isBulkUpdate = user === null;
    const title = isBulkUpdate ? i18n.updateLibsForAll : `${i18n.manageLibsFor} ${user.username}`;
    const body = `<div class="flex justify-end mb-2"><button type="button" id="modalSelectAllLibs" class="text-xs bg-gray-200 dark:bg-gray-600 hover:bg-gray-300 dark:hover:bg-gray-500 px-2 py-1 rounded-md">${i18n.selectAll}</button></div><div id="modalLibsContainer" class="max-h-40 overflow-y-auto bg-gray-100 dark:bg-gray-900/50 p-2 rounded-lg border border-gray-300 dark:border-gray-600 space-y-1 modal-body"><p>${i18n.loadingLibs}</p></div>`;
    const modal = createModal('libraryManagementModal', title, body, `<button id="libMgmtSave" class="btn bg-green-600 hover:bg-green-500 text-white w-full sm:w-auto">${i18n.save}</button><button id="libMgmtCancel" class="btn bg-gray-200 text-gray-800 dark:bg-gray-600 dark:text-gray-200 w-full sm:w-auto mt-3 sm:mt-0">${i18n.cancel}</button>`);

    const modalLibsContainer = modal.querySelector('#modalLibsContainer');
    const selectAllButton = modal.querySelector('#modalSelectAllLibs');
    const saveButton = modal.querySelector('#libMgmtSave');
    
    modal.querySelector('#libMgmtCancel').onclick = () => modal.classList.add('hidden');
    saveButton.disabled = true;

    let userLibraries = [];
    if (!isBulkUpdate) {
        try {
            const userLibsResponse = await api.fetchUserLibraries(user.id);
            if (userLibsResponse && userLibsResponse.success && Array.isArray(userLibsResponse.libraries)) {
                userLibraries = userLibsResponse.libraries;
            } else {
                throw new Error( (userLibsResponse && userLibsResponse.message) || i18n.errorLoadingLibs);
            }
        } catch (e) {
             modalLibsContainer.innerHTML = `<p class="text-red-500">${e.message}</p>`;
             return;
        }
    }
    
    modalLibsContainer.innerHTML = state.allLibraries.map(lib => `<label class="flex items-center space-x-2 p-1 hover:bg-gray-200 dark:hover:bg-gray-600 rounded"><input type="checkbox" class="form-checkbox bg-gray-100 dark:bg-gray-900 border-gray-300 dark:border-gray-500 rounded text-yellow-500" value="${lib.title}" ${userLibraries.includes(lib.title) ? 'checked' : ''}><span>${lib.title}</span></label>`).join('');
    saveButton.disabled = false;
    selectAllButton.onclick = () => toggleSelectAll(modalLibsContainer, selectAllButton);

    saveButton.onclick = async () => {
        const selectedLibraries = Array.from(modalLibsContainer.querySelectorAll('input:checked')).map(input => input.value);
        const apiCall = isBulkUpdate ? api.updateAllLibraries(selectedLibraries) : api.updateUserLibraries(user.id, selectedLibraries);
        try {
            const result = await apiCall;
            showToast(result.message, result.success ? 'success' : 'error');
            if (result.success) {
                document.dispatchEvent(new CustomEvent('data-refresh-requested'));
            }
            modal.classList.add('hidden');
        } catch (error) {
            showToast(error.message, 'error');
        }
    };
}

export async function showUserProfileModal(user) {
    const body = `
        <div class="space-y-4">
            <div><label for="profileName" class="block mb-2 text-sm font-medium text-gray-500 dark:text-gray-400">${i18n.fullName}</label><input type="text" id="profileName" class="w-full p-2.5 text-sm rounded-lg border bg-gray-50 border-gray-300 text-gray-900 dark:bg-gray-700 dark:border-gray-600 dark:text-white"></div>
            <div id="telegram-field-container"><label for="profileTelegram" class="block mb-2 text-sm font-medium text-gray-500 dark:text-gray-400">${i18n.telegramUser}</label><input type="text" id="profileTelegram" class="w-full p-2.5 text-sm rounded-lg border bg-gray-50 border-gray-300 text-gray-900 dark:bg-gray-700 dark:border-gray-600 dark:text-white"></div>
            <div id="discord-field-container"><label for="profileDiscord" class="block mb-2 text-sm font-medium text-gray-500 dark:text-gray-400">${i18n.discordUserId}</label><input type="text" id="profileDiscord" class="w-full p-2.5 text-sm rounded-lg border bg-gray-50 border-gray-300 text-gray-900 dark:bg-gray-700 dark:border-gray-600 dark:text-white"></div>
            <div><label for="profilePhone" class="block mb-2 text-sm font-medium text-gray-500 dark:text-gray-400">${i18n.phoneNumber}</label><input type="tel" id="profilePhone" class="w-full p-2.5 text-sm rounded-lg border bg-gray-50 border-gray-300 text-gray-900 dark:bg-gray-700 dark:border-gray-600 dark:text-white"></div>
            <div class="grid grid-cols-2 gap-4">
                <div>
                    <label for="profileExpiration" class="block mb-2 text-sm font-medium text-gray-500 dark:text-gray-400">${i18n.expirationDate}</label>
                    <input type="date" id="profileExpiration" class="w-full p-2.5 text-sm rounded-lg border bg-gray-50 border-gray-300 text-gray-900 dark:bg-gray-700 dark:border-gray-600 dark:text-white">
                </div>
                <div id="expiration-time-container">
                    <label for="profileExpirationTime" class="block mb-2 text-sm font-medium text-gray-500 dark:text-gray-400">${i18n.blockTime}</label>
                    <input type="time" id="profileExpirationTime" class="w-full p-2.5 text-sm rounded-lg border bg-gray-50 border-gray-300 text-gray-900 dark:bg-gray-700 dark:border-gray-600 dark:text-white">
                    <p id="universal-time-notice" class="hidden text-xs text-yellow-500 mt-1"></p>
                </div>
            </div>
            <div class="flex items-center justify-between pt-3 border-t border-gray-200 dark:border-gray-700"><label for="profileOverseerrAccess" class="text-sm font-medium text-gray-600 dark:text-gray-300">${i18n.overseerrAccess}</label><input type="checkbox" id="profileOverseerrAccess" class="form-checkbox h-5 w-5 rounded text-yellow-500"></div>
            <div class="mt-4 pt-4 border-t border-gray-200 dark:border-gray-700/50">
                <label class="block mb-2 text-sm font-medium text-gray-500 dark:text-gray-400">${i18n.renewSubscription}</label>
                <div class="space-y-2">
                    <div class="flex items-center gap-2"><input type="number" id="renew-months" value="1" min="1" class="w-full text-center bg-transparent border border-gray-300 dark:border-gray-600 rounded-lg p-2"><button type="button" id="confirm-renew" class="btn bg-sky-600 hover:bg-sky-500 text-white flex-1">${i18n.addMonths}</button></div>
                    <button type="button" id="renew-same-day" class="btn bg-gray-600 hover:bg-gray-500 text-white w-full">${i18n.renewSameDay}</button>
                </div>
               <div class="mt-4 pt-4 border-t border-gray-200 dark:border-gray-700/50">
                    <label class="block mb-2 text-sm font-medium text-gray-500 dark:text-gray-400">${i18n.manualActions}</label>
                    <button id="sendNotificationButton" class="btn bg-blue-600 hover:bg-blue-500 text-white w-full" disabled>${i18n.sendExpirationNotification}</button>
               </div>
            </div>
        </div>`;
    const footer = `<button id="saveProfileButton" class="btn bg-green-600 hover:bg-green-500 text-white">${i18n.save}</button><button id="modalCancel" class="btn bg-gray-200 text-gray-800 dark:bg-gray-600 dark:text-gray-200">${i18n.cancel}</button>`;
    const modal = createModal('userProfileModal', `${i18n.manageProfileTitle} ${user.username}`, body, footer);

    const sendNotificationButton = modal.querySelector('#sendNotificationButton');
    const expirationTimeInput = modal.querySelector('#profileExpirationTime');
    const universalTimeNotice = modal.querySelector('#universal-time-notice');

    api.fetchUserProfile(user.id).then(data => {
        if(data.success) {
            if (data.profile) {
                modal.querySelector('#profileName').value = data.profile.name || '';
                modal.querySelector('#profileTelegram').value = data.profile.telegram_user || '';
                modal.querySelector('#profileDiscord').value = data.profile.discord_user_id || '';
                modal.querySelector('#profilePhone').value = data.profile.phone_number || '';
                modal.querySelector('#profileOverseerrAccess').checked = data.profile.overseerr_access || false;
                if (data.profile.expiration_date) {
                    const expDate = new Date(data.profile.expiration_date);
                    const year = expDate.getFullYear();
                    const month = (expDate.getMonth() + 1).toString().padStart(2, '0');
                    const day = expDate.getDate().toString().padStart(2, '0');
                    const hours = expDate.getHours().toString().padStart(2, '0');
                    const minutes = expDate.getMinutes().toString().padStart(2, '0');
                    modal.querySelector('#profileExpiration').value = `${year}-${month}-${day}`;
                    expirationTimeInput.value = `${hours}:${minutes}`;
                    sendNotificationButton.disabled = false;
                }
            }
            if (data.notification_settings) {
                const telegramField = modal.querySelector('#telegram-field-container');
                const discordField = modal.querySelector('#discord-field-container');
                if (telegramField && !data.notification_settings.telegram_enabled) telegramField.classList.add('hidden');
                if (discordField && !data.notification_settings.discord_enabled) discordField.classList.add('hidden');
            }
            if (data.universal_expiration_settings && data.universal_expiration_settings.enabled) {
                expirationTimeInput.disabled = true;
                universalTimeNotice.textContent = `Horário universal ativo: ${data.universal_expiration_settings.time}`;
                universalTimeNotice.classList.remove('hidden');
            }
        }
    }).catch(error => showToast(error.message, 'error'));

    modal.querySelector('#modalCancel').onclick = () => modal.classList.add('hidden');
    modal.querySelector('#saveProfileButton').onclick = async () => {
        const dateValue = modal.querySelector('#profileExpiration').value;
        const timeValue = expirationTimeInput.value || '00:00';
        const localDateTimeString = dateValue ? `${dateValue}T${timeValue}` : null;
        const profileData = {
            name: modal.querySelector('#profileName').value,
            telegram_user: modal.querySelector('#profileTelegram').value,
            discord_user_id: modal.querySelector('#profileDiscord').value,
            phone_number: modal.querySelector('#profilePhone').value,
            expiration_datetime_local: localDateTimeString 
        };
        try {
            const result = await api.updateUserProfile(user.id, profileData);
            showToast(result.message, result.success ? 'success' : 'error');
            if (result.success) { modal.classList.add('hidden'); document.dispatchEvent(new CustomEvent('data-refresh-requested')); }
        } catch (error) {
            showToast(error.message, 'error');
        }
    };
    
    modal.querySelector('#profileOverseerrAccess').onchange = async (e) => {
        try {
            const result = await api.toggleOverseerr(user.id, e.target.checked);
            showToast(result.message, result.success ? 'success' : 'error');
        } catch (error) {
            showToast(error.message, 'error');
        }
    };
    
    sendNotificationButton.onclick = async () => {
        sendNotificationButton.disabled = true;
        try {
            const result = await api.notifyUser(user.id);
            showToast(result.message, result.success ? 'success' : 'error');
        } catch (error) {
            showToast(error.message, 'error');
        } finally {
            sendNotificationButton.disabled = false;
        }
    };

    const handleRenewal = async (button, payload) => {
        button.disabled = true;
        const expirationInput = modal.querySelector('#profileExpiration');
        if (expirationInput && expirationInput.value) payload.base_date = expirationInput.value;
        const timeInput = expirationTimeInput;
        if (timeInput && timeInput.value && !timeInput.disabled) payload.expiration_time = timeInput.value;

        try {
            const result = await api.renewSubscription(user.id, payload);
            showToast(result.message, result.success ? 'success' : 'error');
            if (result.success) { modal.classList.add('hidden'); document.dispatchEvent(new CustomEvent('data-refresh-requested')); }
        } catch (error) {
            showToast(error.message, 'error');
        } finally {
            button.disabled = false;
        }
    };

    modal.querySelector('#confirm-renew').onclick = (e) => {
        const months = parseInt(modal.querySelector('#renew-months').value);
        // CORREÇÃO: Alterado de 'add_days' para 'today' para corresponder ao schema do backend.
        handleRenewal(e.target, { months, base: 'today' });
    };
    modal.querySelector('#renew-same-day').onclick = (e) => {
        handleRenewal(e.target, { months: 1, base: 'expiry_date' });
    }
}

export async function showPaymentHistoryModal(user) {
    const loadingHtml = `<div class="text-center py-8"><svg class="animate-spin h-8 w-8 text-yellow-500 mx-auto" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg><p class="mt-4 text-gray-500 dark:text-gray-400">${i18n.loadingHistory}</p></div>`;
    const body = `<div id="paymentHistoryContainer" class="space-y-2 max-h-96 overflow-y-auto pr-2">${loadingHtml}</div>`;
    const footer = `<button id="modalClose" class="btn bg-gray-200 text-gray-800 dark:bg-gray-600 dark:text-gray-200 w-full">${i18n.close}</button>`;
    const modal = createModal('paymentHistoryModal', `${i18n.paymentHistory} - ${user.username}`, body, footer);
    modal.querySelector('#modalClose').onclick = () => modal.classList.add('hidden');

    const container = modal.querySelector('#paymentHistoryContainer');
    try {
        const result = await api.fetchPaymentHistory(user.id);
        if (result.success && result.payments.length > 0) {
            container.innerHTML = `
                <table class="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
                    <thead class="bg-gray-50 dark:bg-gray-700/50">
                        <tr>
                            <th class="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase">${i18n.date}</th>
                            <th class="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase">${i18n.description}</th>
                            <th class="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase">${i18n.value}</th>
                            <th class="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-300 uppercase">${i18n.status}</th>
                        </tr>
                    </thead>
                    <tbody class="bg-white dark:bg-gray-800 divide-y divide-gray-200 dark:divide-gray-700">
                        ${result.payments.map(p => `
                            <tr>
                                <td class="px-4 py-2 whitespace-nowrap text-sm">${new Date(p.created_at).toLocaleString('pt-BR')}</td>
                                <td class="px-4 py-2 text-sm">${p.description || `${p.provider} - ${p.screens > 0 ? `${p.screens} Telas` : 'Padrão'}`}</td>
                                <td class="px-4 py-2 text-sm font-mono">${p.value.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })}</td>
                                <td class="px-4 py-2 text-sm"><span class="px-2 inline-flex text-xs leading-5 font-semibold rounded-full ${p.status === 'CONCLUIDA' ? 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-300' : 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-300'}">${p.status}</span></td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            `;
        } else {
            container.innerHTML = `<p class="text-gray-500 dark:text-gray-400 text-center py-4">${i18n.noPaymentsFound}</p>`;
        }
    } catch (error) {
        container.innerHTML = `<p class="text-red-500">${error.message}</p>`;
    }
}

export async function showReactivationModal(user) {
    const title = `${i18n.reactivateUserTitle} - ${user.username}`;
    const body = `
        <p class="mb-4">${i18n.selectLibsForReactivation.replace('{username}', `<strong>${user.username}</strong>`)}</p>
        <div class="flex justify-end mb-2">
            <button type="button" id="modalSelectAllLibs" class="text-xs bg-gray-200 dark:bg-gray-600 hover:bg-gray-300 dark:hover:bg-gray-500 px-2 py-1 rounded-md">${i18n.selectAll}</button>
        </div>
        <div id="modalLibsContainer" class="max-h-40 overflow-y-auto bg-gray-100 dark:bg-gray-900/50 p-2 rounded-lg border border-gray-300 dark:border-gray-600 space-y-1 modal-body">
            ${state.allLibraries.map(lib => `
                <label class="flex items-center space-x-2 p-1 hover:bg-gray-200 dark:hover:bg-gray-600 rounded">
                    <input type="checkbox" class="form-checkbox bg-gray-100 dark:bg-gray-900 border-gray-300 dark:border-gray-500 rounded text-yellow-500" value="${lib.title}">
                    <span>${lib.title}</span>
                </label>`).join('')}
        </div>`;
    const footer = `
        <button id="modalConfirm" class="btn bg-green-600 hover:bg-green-500 text-white w-full sm:w-auto">${i18n.confirmReactivateButton}</button>
        <button id="modalCancel" class="btn bg-gray-200 text-gray-800 dark:bg-gray-600 dark:text-gray-200 w-full sm:w-auto">${i18n.cancel}</button>`;

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
        confirmButton.textContent = i18n.reactivating || 'Reativando...';

        try {
            const result = await api.reactivateUser(user.id, selectedLibraries);
            showToast(result.message, result.success ? 'success' : 'error');
            if (result.success) {
                document.dispatchEvent(new CustomEvent('data-refresh-requested'));
            }
        } catch (error) {
            showToast(error.message, 'error');
        } finally {
            modal.classList.add('hidden');
        }
    };
}
