import { fetchAPI, showToast, createModal } from './utils.js';

document.addEventListener('DOMContentLoaded', () => {
    // --- ELEMENTOS E DADOS GLOBAIS ---
    const scriptTag = document.getElementById('users-script');
    
    const urls = {
        login: scriptTag.dataset.urlLogin,
        api_status: scriptTag.dataset.urlApiStatus,
        api_invites_create: scriptTag.dataset.urlApiInvitesCreate,
        api_invites_list: scriptTag.dataset.urlApiInvitesList,
        api_invites_delete: scriptTag.dataset.urlApiInvitesDelete,
        api_users_update_limit: scriptTag.dataset.urlApiUsersUpdateLimit,
        api_users_renew_base: scriptTag.dataset.urlApiUsersRenewBase,
        api_users_profile_base: scriptTag.dataset.urlApiUsersProfileBase,
        api_users_profile_set_base: scriptTag.dataset.urlApiUsersProfileSetBase,
        api_users_notify_base: scriptTag.dataset.urlApiUsersNotifyBase,
        api_users_libraries_base: scriptTag.dataset.urlApiUsersLibrariesBase,
        api_users_update_libraries: scriptTag.dataset.urlApiUsersUpdateLibraries,
        api_users_remove: scriptTag.dataset.urlApiUsersRemove,
        api_users_block: scriptTag.dataset.urlApiUsersBlock,
        api_users_unblock: scriptTag.dataset.urlApiUsersUnblock,
        api_users_toggle_overseerr: scriptTag.dataset.urlApiUsersToggleOverseerr,
        api_users_update_all_limits: scriptTag.dataset.urlApiUsersUpdateAllLimits,
        api_users_payments_base: scriptTag.dataset.urlApiUsersPaymentsBase,
        base_invite_page: scriptTag.dataset.urlBaseInvitePage,
        api_users_update_all_libraries: '/api/users/update-all-libraries' 
    };

    const i18n = {};
    for (const key in scriptTag.dataset) {
        if (key.startsWith('i18n')) {
            const i18nKey = key.charAt(4).toLowerCase() + key.slice(5);
            i18n[i18nKey] = scriptTag.dataset[key];
        }
    }

    // --- ELEMENTOS DO DOM ---
    const userGrid = document.getElementById('userGrid');
    const inviteListDiv = document.getElementById('inviteList');
    const createInviteButton = document.getElementById('createInviteButton');
    const refreshButton = document.getElementById('refreshButton');
    const searchInput = document.getElementById('searchInput');
    const sortSelect = document.getElementById('sortSelect');
    const userTabs = document.getElementById('userTabs');
    const bulkActionsButton = document.getElementById('bulkActionsButton');
    
    // --- ESTADO DA APLICAÇÃO ---
    let allLibraries = [];
    let allUsersCache = [];
    const savedViewState = JSON.parse(localStorage.getItem('userListViewState'));
    let viewState = savedViewState || {
        filter: 'all',
        searchTerm: '',
        sortBy: 'name_asc'
    };
    let activeInviteCount = 0;
    let inviteCheckInterval = null;

    // --- FUNÇÕES AUXILIARES ---

    function showConfirmationModal({ title, message, confirmText, confirmClass, onConfirm }) {
        const modal = createModal('confirmationModal', title, `<p>${message}</p>`,
            `<button id="modalConfirm" class="btn ${confirmClass} w-full sm:w-auto">${confirmText}</button>
             <button id="modalCancel" class="btn bg-gray-200 text-gray-800 hover:bg-gray-300 dark:bg-gray-600 dark:text-gray-200 dark:hover:bg-gray-500 w-full sm:w-auto">${i18n.cancel}</button>`
        );
        modal.querySelector('#modalConfirm').onclick = () => { onConfirm(); modal.classList.add('hidden'); };
        modal.querySelector('#modalCancel').onclick = () => modal.classList.add('hidden');
    }
    
    function toggleSelectAll(container, button) {
        const checkboxes = container.querySelectorAll('input[type="checkbox"]');
        const areAllSelected = Array.from(checkboxes).every(cb => cb.checked);
        checkboxes.forEach(cb => cb.checked = !areAllSelected);
        button.textContent = areAllSelected ? i18n.selectAll : i18n.unselectAll;
    }

    // --- LÓGICA DE GESTÃO DE CONVITES ---

    function showCreateInviteModal() {
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
                                ${allLibraries.map(lib => `<label class="flex items-center space-x-2 p-1 hover:bg-gray-200 dark:hover:bg-gray-600 rounded"><input type="checkbox" class="form-checkbox bg-gray-100 dark:bg-gray-900 border-gray-300 dark:border-gray-500 rounded text-yellow-500" value="${lib.title}"><span>${lib.title}</span></label>`).join('')}
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
                const result = await fetchAPI(urls.api_invites_create, 'POST', { 
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
                    loadInvites();
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

    function showInviteLinkModal(inviteUrl) {
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

    async function loadInvites(isPeriodicCheck = false) {
        try {
            const invites = await fetchAPI(urls.api_invites_list);
            const pendingInvites = Object.values(invites);
            
            if (isPeriodicCheck && activeInviteCount > 0 && pendingInvites.length < activeInviteCount) {
                showToast(i18n.inviteUsedUpdating, 'info');
                await loadStatus(true);
                return;
            }
            
            activeInviteCount = pendingInvites.length;
            inviteListDiv.innerHTML = pendingInvites.length > 0
                ? pendingInvites.sort((a, b) => new Date(b.created_at) - new Date(a.created_at)).map(details => {
                    const { code, expires_at, use_count, max_uses, trial_duration_minutes } = details;
                    const isExpired = expires_at && new Date(expires_at) < new Date();
                    const isFull = use_count >= max_uses;
                    const isActive = !isExpired && !isFull;

                    let statusHtml = `<span class="px-2 py-1 text-xs font-medium rounded-full ${isActive ? 'text-green-800 bg-green-100 dark:bg-green-900/30 dark:text-green-300' : 'text-red-800 bg-red-100 dark:bg-red-900/30 dark:text-red-300'}">${isActive ? i18n.active : i18n.expired}</span>`;
                    
                    let usageHtml = `<span class="text-xs text-gray-500 dark:text-gray-400">${use_count}/${max_uses} ${i18n.uses}</span>`;

                    let trialHtml = trial_duration_minutes > 0 ? `<span class="px-2 py-1 text-xs font-medium text-purple-800 bg-purple-100 rounded-full dark:bg-purple-900/30 dark:text-purple-300">${i18n.trial}</span>` : '';

                    let expirationHtml = '';
                    if (details.expires_at && !isExpired) {
                        const expirationDate = new Date(details.expires_at);
                        const now = new Date();
                        const diffMs = expirationDate - now;
                        
                        if (diffMs > 0) {
                            const diffMinutes = Math.floor(diffMs / 60000);
                            const diffHours = Math.floor(diffMinutes / 60);
                            const diffDays = Math.floor(diffHours / 24);

                            let expiresInText = '';
                            if (diffDays > 0) {
                                expiresInText = i18n.expiresInDays.replace('{days}', diffDays);
                            } else if (diffHours > 0) {
                                expiresInText = i18n.expiresInHours.replace('{hours}', diffHours);
                            } else {
                                expiresInText = i18n.expiresInMinutes.replace('{minutes}', diffMinutes);
                            }
                            
                            expirationHtml = `<span class="text-xs text-gray-500 dark:text-gray-400 flex items-center gap-1">
                                                <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
                                                ${expiresInText}
                                              </span>`;
                        }
                    }

                    return `
                    <div class="flex items-center justify-between p-2 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-700/50">
                        <div class="flex items-center gap-3 flex-wrap">
                            <span class="font-mono text-sm">${code}</span>
                            ${statusHtml}
                            ${trialHtml}
                            ${usageHtml}
                            ${expirationHtml}
                        </div>
                        <div class="flex items-center gap-2">
                             <button data-action="copy-invite" data-code="${code}" title="${i18n.copyLink}" class="p-2 rounded-full text-gray-500 hover:bg-blue-100 dark:hover:bg-blue-500/20" ${!isActive ? 'disabled' : ''}><svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path d="M8 3a1 1 0 011-1h2a1 1 0 110 2H9a1 1 0 01-1-1z" /><path d="M6 3a2 2 0 00-2 2v11a2 2 0 002 2h8a2 2 0 002-2V5a2 2 0 00-2-2 3 3 0 01-3 3H9a3 3 0 01-3-3z" /></svg></button>
                            <button data-action="delete-invite" data-code="${code}" title="${i18n.deleteInvite}" class="p-2 rounded-full text-gray-500 hover:bg-red-100 dark:hover:bg-red-500/20"><svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z" clip-rule="evenodd" /></svg></button>
                        </div>
                    </div>`;
                }).join('')
                : `<p class="text-gray-500 dark:text-gray-400">${i18n.noPendingInvites}</p>`;
            inviteListDiv.querySelectorAll('button').forEach(button => button.onclick = () => handleInviteAction(button.dataset.action, button.dataset.code));
        } catch (e) {
             inviteListDiv.innerHTML = `<p class="text-red-500">${i18n.error}: ${e.message}</p>`;
        }
    }

    function handleInviteAction(action, code) {
        if (action === 'copy-invite') {
            const inviteUrl = `${window.location.origin}${urls.base_invite_page}${code}`;
            showInviteLinkModal(inviteUrl);
        } else if (action === 'delete-invite') {
            const message = `${i18n.confirmDeleteInvite} <strong>${code}</strong>? ${i18n.actionCannotBeUndone}`;
            showConfirmationModal({
                title: i18n.deleteInvite, message: message, confirmText: i18n.confirmDeleteButton, confirmClass: 'bg-red-600 text-white',
                onConfirm: async () => {
                    try {
                        const result = await fetchAPI(urls.api_invites_delete, 'POST', { code });
                        showToast(result.message, result.success ? 'success' : 'error');
                        if (result.success) loadInvites();
                    } catch (error) {
                        showToast(error.message, 'error');
                    }
                }
            });
        }
    }
    
    // --- LÓGICA DE GESTÃO DE UTILIZADORES ---

    function renderUserGrid() {
        let usersToRender = [...allUsersCache];

        if (viewState.filter === 'active') {
            usersToRender = usersToRender.filter(u => !u.is_blocked);
        } else if (viewState.filter === 'blocked') {
            usersToRender = usersToRender.filter(u => u.is_blocked);
        }

        if (viewState.searchTerm) {
            const term = viewState.searchTerm.toLowerCase();
            usersToRender = usersToRender.filter(u =>
                u.username.toLowerCase().includes(term) ||
                u.email.toLowerCase().includes(term)
            );
        }

        usersToRender.sort((a, b) => {
            if (viewState.sortBy === 'name_asc') return a.username.localeCompare(b.username);
            if (viewState.sortBy === 'name_desc') return b.username.localeCompare(a.username);
            if (viewState.sortBy === 'exp_asc' || viewState.sortBy === 'exp_desc') {
                const dateA = a.expiration_date ? new Date(a.expiration_date) : null;
                const dateB = b.expiration_date ? new Date(b.expiration_date) : null;
                if (dateA && dateB) return viewState.sortBy === 'exp_asc' ? dateA - dateB : dateB - dateA;
                if (!dateA) return 1;
                if (!dateB) return -1;
            }
            return 0;
        });

        userGrid.innerHTML = '';
        if (usersToRender.length > 0) {
            usersToRender.forEach(user => userGrid.appendChild(renderUserCard(user)));
        } else {
            userGrid.innerHTML = `<p class="text-gray-500 dark:text-gray-400 text-center col-span-full py-10">${i18n.noUsersFound}</p>`;
        }
    }

    function updateTabCounts() {
        const total = allUsersCache.length;
        const active = allUsersCache.filter(u => !u.is_blocked).length;
        const blocked = total - active;

        document.getElementById('count-all').textContent = total;
        document.getElementById('count-active').textContent = active;
        document.getElementById('count-blocked').textContent = blocked;
    }

    async function loadStatus(force = false) {
        refreshButton.disabled = true;
        refreshButton.querySelector('svg').classList.add('animate-spin');
        try {
            const data = await fetchAPI(`${urls.api_status}?force=${force}`);
            allUsersCache = data.users || [];
            allLibraries = data.libraries || [];
            
            updateTabCounts();
            renderUserGrid();
            await loadInvites();
        } catch (e) {
            userGrid.innerHTML = `<p class="text-red-500 text-center col-span-full">${i18n.loadingUsersFailed} ${e.message}</p>`;
        } finally {
            refreshButton.disabled = false;
            refreshButton.querySelector('svg').classList.remove('animate-spin');
        }
    }
    
    function renderUserCard(user) {
        const card = document.createElement('div');
        card.className = 'flex flex-col bg-white dark:bg-gray-800 p-4 rounded-lg hover:shadow-xl transition-shadow duration-300 border border-gray-200 dark:border-gray-700';
        
        let trialHtml = '';
        if (user.trial_end_date) {
            const trialEndDate = new Date(user.trial_end_date);
            if (trialEndDate > new Date()) {
                const diffMs = trialEndDate - new Date();
                const diffHours = Math.floor(diffMs / 3600000);
                const diffMinutes = Math.round((diffMs % 3600000) / 60000);
                const remainingTime = diffHours > 0 ? `${diffHours}h ${diffMinutes}m` : `${diffMinutes}m`;
                trialHtml = `<div class="mt-2 text-xs font-bold bg-purple-100 text-purple-800 dark:bg-purple-900/50 dark:text-purple-300 px-2 py-1 rounded-full inline-flex items-center gap-1"><span>${i18n.inTestWithTime.replace('{remainingTime}', remainingTime)}</span></div>`;
            } else {
                trialHtml = `<div class="mt-2 text-xs font-bold bg-gray-200 text-gray-800 dark:bg-gray-600 dark:text-gray-200 px-2 py-1 rounded-full inline-flex items-center gap-1"><span>${i18n.testFinished}</span></div>`;
            }
        }

        let expirationHtml = '';
        if (!trialHtml && user.expiration_date) {
            const expDate = new Date(user.expiration_date);
            const today = new Date();
            today.setHours(0, 0, 0, 0);

            const daysLeft = Math.ceil((expDate.getTime() - today.getTime()) / (1000 * 3600 * 24));
            
            const dateColor = daysLeft < 0 ? 'text-red-500 font-semibold' : (daysLeft <= 7 ? 'text-yellow-500 font-semibold' : 'text-gray-400 dark:text-gray-500');
            
            const formattedDate = expDate.toLocaleDateString();
            
            expirationHtml = `<div class="mt-1 text-xs flex items-center ${dateColor}"><span>${i18n.expiresOn} ${formattedDate}</span></div>`;
        }

        card.innerHTML = `
            <div class="flex items-start flex-1">
                <img src="${user.thumb || 'https://placehold.co/80x80/1F2937/E5E7EB?text=?'}" alt="Avatar" class="w-16 h-16 rounded-full mr-4">
                <div class="flex-1">
                    <div class="flex items-center gap-2">
                        <div class="w-3 h-3 rounded-full ${user.is_blocked ? 'bg-red-500' : 'bg-green-500'}" title="${user.is_blocked ? i18n.blockedTitle : i18n.activeTitle}"></div>
                        <p class="font-semibold text-gray-900 dark:text-white text-lg">${user.username}</p>
                    </div>
                    <p class="text-sm text-gray-500 dark:text-gray-400 truncate">${user.email}</p>
                    ${trialHtml || expirationHtml}
                    ${user.screen_limit > 0 ? `<div class="mt-2 text-xs font-bold bg-blue-100 text-blue-800 dark:bg-blue-500/50 dark:text-blue-200 border border-blue-200 dark:border-blue-400/30 px-2 py-1 rounded-full inline-block">${user.screen_limit} ${user.screen_limit > 1 ? i18n.screenPlural : i18n.screenSingular}</div>` : ''}
                </div>
            </div>
            <div class="flex items-center justify-end gap-1 mt-4 pt-4 border-t border-gray-200 dark:border-gray-700">
                <button data-action="renew-month" title="${i18n.addOneMonth}" class="btn text-xs bg-green-600 hover:bg-green-700 text-white px-2 py-1 mr-auto">${i18n.addOneMonth}</button>
                <button data-action="manage-profile" title="${i18n.manageProfileAndExpiration}" class="p-2 rounded-full text-gray-500 hover:bg-green-100 dark:hover:bg-green-500/20 dark:text-green-400"><svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path d="M10 2a5 5 0 00-5 5v2a2 2 0 00-2 2v5a2 2 0 002 2h10a2 2 0 002-2v-5a2 2 0 00-2-2V7a5 5 0 00-5-5zm0 10a3 3 0 100-6 3 3 0 000 6z" /></svg></button>
                <button data-action="payment-history" title="${i18n.paymentHistory}" class="p-2 rounded-full text-gray-500 hover:bg-yellow-100 dark:hover:bg-yellow-500/20 dark:text-yellow-400"><svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path d="M10.707 2.293a1 1 0 00-1.414 0l-7 7a1 1 0 001.414 1.414L4 10.414V17a1 1 0 001 1h2a1 1 0 001-1v-2a1 1 0 011-1h2a1 1 0 011 1v2a1 1 0 001 1h2a1 1 0 001-1v-6.586l.293.293a1 1 0 001.414-1.414l-7-7z" /></svg></button>
                <button data-action="manage-libraries" title="${i18n.manageLibraries}" class="p-2 rounded-full text-gray-500 hover:bg-purple-100 dark:hover:bg-purple-500/20 dark:text-purple-400"><svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path d="M7 3a1 1 0 000 2h6a1 1 0 100-2H7zM4 7a1 1 0 011-1h10a1 1 0 110 2H5a1 1 0 01-1-1zM2 11a2 2 0 012-2h12a2 2 0 012 2v4a2 2 0 01-2-2H4a2 2 0 01-2-2v-4z" /></svg></button>
                <button data-action="manage-limit" title="${i18n.manageScreenLimit}" class="p-2 rounded-full text-gray-500 hover:bg-blue-100 dark:hover:bg-blue-500/20 dark:text-blue-400"><svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M3 5a2 2 0 012-2h10a2 2 0 012 2v10a2 2 0 01-2 2H5a2 2 0 01-2-2V5zm4 0h6v10H7V5z" clip-rule="evenodd" /></svg></button>
                ${user.is_blocked ? `<button data-action="unblock" title="${i18n.unblock}" class="p-2 rounded-full text-gray-500 hover:bg-yellow-100 dark:hover:bg-yellow-500/20 dark:text-yellow-400"><svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd" /></svg></button>`: `<button data-action="block" title="${i18n.block}" class="p-2 rounded-full text-gray-500 hover:bg-red-100 dark:hover:bg-red-500/20 dark:text-red-400"><svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clip-rule="evenodd" /></svg></button>`}
                <button data-action="remove" title="${i18n.removeUserButton}" class="p-2 rounded-full text-gray-500 hover:bg-gray-200 dark:hover:bg-gray-600 dark:text-gray-400"><svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z" clip-rule="evenodd" /></svg></button>
            </div>`;
        card.querySelectorAll('button').forEach(button => button.onclick = () => handleUserAction(button.dataset.action, user));
        return card;
    }

    async function handleQuickRenewal(user) {
        const message = `${i18n.confirmAddOneMonth} <strong>${user.username}</strong>?`;
        showConfirmationModal({
            title: i18n.addOneMonth,
            message: message,
            confirmText: i18n.confirm,
            confirmClass: 'bg-green-600 text-white',
            onConfirm: async () => {
                try {
                    const result = await fetchAPI(urls.api_users_renew_base.replace('__USERNAME__', user.username), 'POST', {
                        months: 1,
                        base: 'expiry_date'
                    });
                    showToast(result.message, result.success ? 'success' : 'error');
                    if (result.success) loadStatus(true);
                } catch (error) {
                    showToast(error.message, 'error');
                }
            }
        });
    }

    async function handleUserAction(action, user) {
        if (action === 'manage-profile') return showUserProfileModal(user);
        if (action === 'manage-limit') return showScreenLimitModal(user);
        if (action === 'manage-libraries') return showLibraryManagementModal(user);
        if (action === 'renew-month') return handleQuickRenewal(user);
        if (action === 'payment-history') return showPaymentHistoryModal(user);
        
        const actionConfig = {
            'remove': { title: i18n.removeUserTitle, message: `${i18n.confirmRemoveUser} <strong>${user.username}</strong>?`, confirmText: i18n.confirmRemoveButton, confirmClass: 'bg-red-600 text-white', endpoint: urls.api_users_remove },
            'block': { title: i18n.blockUserTitle, message: `${i18n.confirmBlockUser} <strong>${user.username}</strong>?`, confirmText: i18n.confirmBlockButton, confirmClass: 'bg-red-600 text-white', endpoint: urls.api_users_block },
            'unblock': { title: i18n.unblockUserTitle, message: `${i18n.confirmUnblockUser} <strong>${user.username}</strong>?`, confirmText: i18n.confirmUnblockButton, confirmClass: 'bg-yellow-500 text-black', endpoint: urls.api_users_unblock }
        };
        const config = actionConfig[action];
        if (config) {
            showConfirmationModal({ ...config, onConfirm: async () => { 
                try {
                    const result = await fetchAPI(config.endpoint, 'POST', { email: user.email }); 
                    showToast(result.message, result.success ? 'success' : 'error'); 
                    if (result.success) loadStatus(true); 
                } catch (error) {
                    showToast(error.message, 'error');
                }
            }});
        }
    }

    // --- MODAIS (implementações completas) ---
    
    async function showPaymentHistoryModal(user) {
        const loadingHtml = `
            <div class="text-center py-8">
                <svg class="animate-spin h-8 w-8 text-yellow-500 mx-auto" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                    <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                    <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                <p class="mt-4 text-gray-500 dark:text-gray-400">${i18n.loadingHistory}</p>
            </div>`;
        const body = `<div id="paymentHistoryContainer" class="space-y-2 max-h-96 overflow-y-auto pr-2">${loadingHtml}</div>`;
        const footer = `<button id="modalClose" class="btn bg-gray-200 text-gray-800 dark:bg-gray-600 dark:text-gray-200 w-full">${i18n.close}</button>`;
        const modal = createModal('paymentHistoryModal', `${i18n.paymentHistory} - ${user.username}`, body, footer);
        modal.querySelector('#modalClose').onclick = () => modal.classList.add('hidden');

        const container = modal.querySelector('#paymentHistoryContainer');
        try {
            const endpoint = urls.api_users_payments_base.replace('__USERNAME__', user.username);
            const result = await fetchAPI(endpoint);
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

    function showBulkActionsModal() {
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
            showLibraryManagementModal(null); // Pass null for bulk update
        };
        modal.querySelector('#bulkUpdateLimits').onclick = () => {
            modal.classList.add('hidden');
            showBulkScreenLimitModal();
        };
    }

    function showBulkScreenLimitModal() {
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
                    const result = await fetchAPI(urls.api_users_update_all_limits, 'POST', { screens });
                    showToast(result.message, result.success ? 'success' : 'error');
                    if (result.success) loadStatus(true);
                    modal.classList.add('hidden');
                } catch (error) {
                    showToast(error.message, 'error');
                }
            };
        });
        modal.querySelector('#limitCancel').onclick = () => modal.classList.add('hidden');
    }

    function showScreenLimitModal(user) {
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
                    const result = await fetchAPI(urls.api_users_update_limit, 'POST', { email: user.email, screens });
                    showToast(result.message, result.success ? 'success' : 'error');
                    if (result.success) loadStatus(true);
                    modal.classList.add('hidden');
                } catch (error) {
                    showToast(error.message, 'error');
                }
            };
        });
        modal.querySelector('#limitCancel').onclick = () => modal.classList.add('hidden');
    }
    
    async function showLibraryManagementModal(user = null) {
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
                const endpoint = urls.api_users_libraries_base.replace('__EMAIL__', user.email);
                const userLibsResponse = await fetchAPI(endpoint);
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
        
        modalLibsContainer.innerHTML = allLibraries.map(lib => `<label class="flex items-center space-x-2 p-1 hover:bg-gray-200 dark:hover:bg-gray-600 rounded"><input type="checkbox" class="form-checkbox bg-gray-100 dark:bg-gray-900 border-gray-300 dark:border-gray-500 rounded text-yellow-500" value="${lib.title}" ${userLibraries.includes(lib.title) ? 'checked' : ''}><span>${lib.title}</span></label>`).join('');
        saveButton.disabled = false;
        selectAllButton.onclick = () => toggleSelectAll(modalLibsContainer, selectAllButton);

        saveButton.onclick = async () => {
            const selectedLibraries = Array.from(modalLibsContainer.querySelectorAll('input:checked')).map(input => input.value);
            const endpoint = isBulkUpdate ? urls.api_users_update_all_libraries : urls.api_users_update_libraries;
            const payload = isBulkUpdate ? { libraries: selectedLibraries } : { email: user.email, libraries: selectedLibraries };
            try {
                const result = await fetchAPI(endpoint, 'POST', payload);
                showToast(result.message, result.success ? 'success' : 'error');
                if (result.success) loadStatus(true);
                modal.classList.add('hidden');
            } catch (error) {
                showToast(error.message, 'error');
            }
        };
    }

    async function showUserProfileModal(user) {
        const body = `
            <div class="space-y-4">
                <div><label for="profileName" class="block mb-2 text-sm font-medium text-gray-500 dark:text-gray-400">${i18n.fullName}</label><input type="text" id="profileName" class="w-full p-2.5 text-sm rounded-lg border bg-gray-50 border-gray-300 text-gray-900 dark:bg-gray-700 dark:border-gray-600 dark:text-white"></div>
                <div><label for="profileTelegram" class="block mb-2 text-sm font-medium text-gray-500 dark:text-gray-400">${i18n.telegramUser}</label><input type="text" id="profileTelegram" class="w-full p-2.5 text-sm rounded-lg border bg-gray-50 border-gray-300 text-gray-900 dark:bg-gray-700 dark:border-gray-600 dark:text-white"></div>
                <div><label for="profilePhone" class="block mb-2 text-sm font-medium text-gray-500 dark:text-gray-400">${i18n.phoneNumber}</label><input type="tel" id="profilePhone" class="w-full p-2.5 text-sm rounded-lg border bg-gray-50 border-gray-300 text-gray-900 dark:bg-gray-700 dark:border-gray-600 dark:text-white"></div>
                <div class="grid grid-cols-2 gap-4">
                    <div>
                        <label for="profileExpiration" class="block mb-2 text-sm font-medium text-gray-500 dark:text-gray-400">${i18n.expirationDate}</label>
                        <input type="date" id="profileExpiration" class="w-full p-2.5 text-sm rounded-lg border bg-gray-50 border-gray-300 text-gray-900 dark:bg-gray-700 dark:border-gray-600 dark:text-white">
                    </div>
                    <div>
                        <label for="profileExpirationTime" class="block mb-2 text-sm font-medium text-gray-500 dark:text-gray-400">${i18n.blockTime}</label>
                        <input type="time" id="profileExpirationTime" class="w-full p-2.5 text-sm rounded-lg border bg-gray-50 border-gray-300 text-gray-900 dark:bg-gray-700 dark:border-gray-600 dark:text-white">
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

        fetchAPI(urls.api_users_profile_base.replace('__USERNAME__', user.username)).then(data => {
            if(data.success && data.profile) {
                modal.querySelector('#profileName').value = data.profile.name || '';
                modal.querySelector('#profileTelegram').value = data.profile.telegram_user || '';
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
                    modal.querySelector('#profileExpirationTime').value = `${hours}:${minutes}`;
                    sendNotificationButton.disabled = false;
                }
            }
        }).catch(error => showToast(error.message, 'error'));

        modal.querySelector('#modalCancel').onclick = () => modal.classList.add('hidden');
        modal.querySelector('#saveProfileButton').onclick = async () => {
            const dateValue = modal.querySelector('#profileExpiration').value;
            const timeValue = modal.querySelector('#profileExpirationTime').value || '00:00';
            const localDateTimeString = dateValue ? `${dateValue}T${timeValue}` : null;

            const profileData = {
                name: modal.querySelector('#profileName').value,
                telegram_user: modal.querySelector('#profileTelegram').value,
                phone_number: modal.querySelector('#profilePhone').value,
                expiration_datetime_local: localDateTimeString 
            };
            try {
                const result = await fetchAPI(urls.api_users_profile_set_base.replace('__USERNAME__', user.username), 'POST', profileData);
                showToast(result.message, result.success ? 'success' : 'error');
                if (result.success) { modal.classList.add('hidden'); loadStatus(true); }
            } catch (error) {
                showToast(error.message, 'error');
            }
        };
        
        modal.querySelector('#profileOverseerrAccess').onchange = async (e) => {
            try {
                const result = await fetchAPI(urls.api_users_toggle_overseerr, 'POST', { email: user.email, access: e.target.checked });
                showToast(result.message, result.success ? 'success' : 'error');
            } catch (error) {
                showToast(error.message, 'error');
            }
        };
        
        sendNotificationButton.onclick = async () => {
            sendNotificationButton.disabled = true;
            try {
                const result = await fetchAPI(urls.api_users_notify_base.replace('__USERNAME__', user.username), 'POST');
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
            if (expirationInput && expirationInput.value) {
                payload.base_date = expirationInput.value;
            }
            
            const timeInput = modal.querySelector('#profileExpirationTime');
            if (timeInput && timeInput.value) {
                payload.expiration_time = timeInput.value;
            }

            try {
                const result = await fetchAPI(urls.api_users_renew_base.replace('__USERNAME__', user.username), 'POST', payload);
                showToast(result.message, result.success ? 'success' : 'error');
                if (result.success) { modal.classList.add('hidden'); loadStatus(true); }
            } catch (error) {
                showToast(error.message, 'error');
            } finally {
                button.disabled = false;
            }
        };

        modal.querySelector('#confirm-renew').onclick = (e) => {
            const months = parseInt(modal.querySelector('#renew-months').value);
            handleRenewal(e.target, { months, base: 'add_days' });
        };
        modal.querySelector('#renew-same-day').onclick = (e) => {
            handleRenewal(e.target, { months: 1, base: 'expiry_date' });
        }
    }

    // --- INICIALIZAÇÃO E EVENT LISTENERS ---
    
    function initializeView() {
        if (sortSelect) {
            sortSelect.value = viewState.sortBy;
        }
        if (userTabs) {
            userTabs.querySelectorAll('.user-tab').forEach(tab => {
                tab.classList.toggle('active', tab.dataset.filter === viewState.filter);
            });
        }

        createInviteButton.addEventListener('click', showCreateInviteModal);
        refreshButton.addEventListener('click', () => loadStatus(true));
        
        if (bulkActionsButton) {
            bulkActionsButton.addEventListener('click', showBulkActionsModal);
        }
        
        searchInput.addEventListener('input', (e) => {
            viewState.searchTerm = e.target.value;
            renderUserGrid();
        });

        sortSelect.addEventListener('change', (e) => {
            viewState.sortBy = e.target.value;
            localStorage.setItem('userListViewState', JSON.stringify(viewState));
            renderUserGrid();
        });

        userTabs.addEventListener('click', (e) => {
            const button = e.target.closest('button');
            if (button && button.dataset.filter) {
                userTabs.querySelectorAll('.user-tab').forEach(tab => tab.classList.remove('active'));
                button.classList.add('active');
                viewState.filter = button.dataset.filter;
                localStorage.setItem('userListViewState', JSON.stringify(viewState));
                renderUserGrid();
            }
        });

        loadStatus();
        inviteCheckInterval = setInterval(() => loadInvites(true), 10000);
    }

    initializeView();
});
