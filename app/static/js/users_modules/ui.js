// app/static/js/users_modules/ui.js

import * as dom from './dom.js';
import * as state from './state.js';
import * as api from './api.js';
import { i18n, urls } from './config.js';
import { showToast } from '../utils.js';
import { handleInviteAction, handleUserAction } from './handlers.js';
import * as modals from './modals.js';

/**
 * Módulo de UI (Interface do Utilizador)
 * * Responsável pela renderização do conteúdo principal da página, como a grelha de utilizadores e a lista de convites.
 * A lógica para os modais foi movida para o seu próprio módulo (modals.js).
 */

// Re-exporta as funções dos modais para que possam ser acedidas através deste módulo.
export const {
    showConfirmationModal,
    showCreateInviteModal,
    showInviteLinkModal,
    showBulkActionsModal,
    showBulkScreenLimitModal,
    showScreenLimitModal,
    showLibraryManagementModal,
    showUserProfileModal,
    showPaymentHistoryModal,
    showReactivationModal,
    showExtendTrialModal,
    showInviteDetailsModal // **NOVO**
} = modals;


/**
 * Renderiza a lista de convites pendentes.
 * @param {boolean} isPeriodicCheck - Indica se a chamada é de uma verificação periódica.
 */
export async function loadInvites(isPeriodicCheck = false) {
    try {
        const invites = await api.listInvites();
        const pendingInvites = Object.values(invites);

        if (isPeriodicCheck && state.activeInviteCount > 0 && pendingInvites.length < state.activeInviteCount) {
            showToast(i18n.inviteUsedUpdating, 'info');
            await loadStatus(true); // Força atualização da lista de utilizadores
            return; // Retorna aqui para evitar renderizar a lista de convites desatualizada
        }

        state.setActiveInviteCount(pendingInvites.length);
        dom.inviteListDiv.innerHTML = pendingInvites.length > 0
            ? pendingInvites.sort((a, b) => new Date(b.created_at) - new Date(a.created_at)).map(renderInviteCard).join('')
            : `<p class="text-gray-500 dark:text-gray-400">${i18n.noPendingInvites}</p>`;

        dom.inviteListDiv.querySelectorAll('button').forEach(button => {
            // Passa os detalhes completos do convite para o botão de detalhes
            if (button.dataset.action === 'details') {
                const code = button.dataset.code;
                const inviteDetails = pendingInvites.find(inv => inv.code === code);
                button.onclick = () => handleInviteAction('details', code, inviteDetails);
            } else {
                button.onclick = () => handleInviteAction(button.dataset.action, button.dataset.code);
            }
        });
    } catch (e) {
        dom.inviteListDiv.innerHTML = `<p class="text-red-500">${i18n.error}: ${e.message}</p>`;
    }
}

/**
 * Cria o HTML para um único cartão de convite.
 * @param {object} details - Detalhes do convite.
 * @returns {string} HTML do cartão de convite.
 */
function renderInviteCard(details) {
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
        const diffMs = expirationDate - new Date();
        if (diffMs > 0) {
            const diffMinutes = Math.floor(diffMs / 60000);
            const diffHours = Math.floor(diffMinutes / 60);
            const diffDays = Math.floor(diffHours / 24);
            let expiresInText = diffDays > 0 ? i18n.expiresInDays.replace('{days}', diffDays) : (diffHours > 0 ? i18n.expiresInHours.replace('{hours}', diffHours) : i18n.expiresInMinutes.replace('{minutes}', diffMinutes));
            expirationHtml = `<span class="text-xs text-gray-500 dark:text-gray-400 flex items-center gap-1"><svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>${expiresInText}</span>`;
        }
    }

    return `
    <div class="flex items-center justify-between p-2 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-700/50">
        <div class="flex items-center gap-3 flex-wrap">
            <span class="font-mono text-sm">${code}</span>
            ${statusHtml} ${trialHtml} ${usageHtml} ${expirationHtml}
        </div>
        <div class="flex items-center gap-2">
            <button data-action="copy-invite" data-code="${code}" title="${i18n.copyLink}" class="p-2 rounded-full text-gray-500 hover:bg-blue-100 dark:hover:bg-blue-500/20" ${!isActive ? 'disabled' : ''}><svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path d="M8 3a1 1 0 011-1h2a1 1 0 110 2H9a1 1 0 01-1-1z" /><path d="M6 3a2 2 0 00-2 2v11a2 2 0 002 2h8a2 2 0 002-2V5a2 2 0 00-2-2 3 3 0 01-3 3H9a3 3 0 01-3-3z" /></svg></button>
            <button data-action="details" data-code="${code}" title="${i18n.inviteDetails || 'Detalhes'}" class="p-2 rounded-full text-gray-500 hover:bg-yellow-100 dark:hover:bg-yellow-500/20"><svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clip-rule="evenodd" /></svg></button>
            <button data-action="delete-invite" data-code="${code}" title="${i18n.deleteInvite}" class="p-2 rounded-full text-gray-500 hover:bg-red-100 dark:hover:bg-red-500/20"><svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z" clip-rule="evenodd" /></svg></button>
        </div>
    </div>`;
}

/**
 * Renderiza a grelha de utilizadores com base no estado atual.
 */
export function renderUserGrid() {
    let usersToRender = [...state.allUsersCache];

    // Filtragem
    if (state.viewState.filter === 'active') {
        usersToRender = usersToRender.filter(u => !u.is_blocked && u.status === 'active');
    } else if (state.viewState.filter === 'blocked') {
        usersToRender = usersToRender.filter(u => u.is_blocked);
    } else if (state.viewState.filter === 'trial') {
        // CORREÇÃO: Mostra apenas quem está *atualmente* em teste
        usersToRender = usersToRender.filter(u => u.is_on_trial && u.trial_end_date && new Date(u.trial_end_date) > new Date());
    } else if (state.viewState.filter === 'inactive') {
        usersToRender = usersToRender.filter(u => u.status === 'inactive');
    }

    // Pesquisa
    if (state.viewState.searchTerm) {
        const term = state.viewState.searchTerm.toLowerCase();
        usersToRender = usersToRender.filter(u =>
            u.username.toLowerCase().includes(term) ||
            (u.email && u.email.toLowerCase().includes(term))
        );
    }

    // Ordenação
    usersToRender.sort((a, b) => {
        if (state.viewState.sortBy === 'name_asc') return a.username.localeCompare(b.username);
        if (state.viewState.sortBy === 'name_desc') return b.username.localeCompare(a.username);
        if (state.viewState.sortBy === 'exp_asc' || state.viewState.sortBy === 'exp_desc') {
            const dateA = a.expiration_date ? new Date(a.expiration_date) : null;
            const dateB = b.expiration_date ? new Date(b.expiration_date) : null;
            // CORREÇÃO: Trata datas inválidas ou nulas corretamente na ordenação
            if (dateA && dateB) {
                return state.viewState.sortBy === 'exp_asc' ? dateA - dateB : dateB - dateA;
            } else if (!dateA && !dateB) {
                return 0; // Se ambos não têm data, mantém a ordem
            } else if (!dateA) {
                return state.viewState.sortBy === 'exp_asc' ? 1 : -1; // Sem data vem depois (asc) ou antes (desc)
            } else { // !dateB
                return state.viewState.sortBy === 'exp_asc' ? -1 : 1; // Sem data vem depois (asc) ou antes (desc)
            }
        }
        return 0;
    });

    dom.userGrid.innerHTML = '';
    if (usersToRender.length > 0) {
        usersToRender.forEach(user => dom.userGrid.appendChild(renderUserCard(user)));
    } else {
        dom.userGrid.innerHTML = `<p class="text-gray-500 dark:text-gray-400 text-center col-span-full py-10">${i18n.noUsersFound}</p>`;
    }
}

/**
 * Cria o HTML para um único cartão de utilizador.
 * @param {object} user - O objeto do utilizador.
 * @returns {HTMLElement} O elemento do cartão de utilizador.
 */
function renderUserCard(user) {
    const card = document.createElement('div');
    card.className = 'flex flex-col bg-white dark:bg-gray-800 p-4 rounded-lg hover:shadow-xl transition-shadow duration-300 border border-gray-200 dark:border-gray-700';

    const isInactive = user.status === 'inactive';

    let statusHtml = ''; // Armazena o HTML do status (teste ou expiração)

    // --- CORREÇÃO: Lógica de prioridade ---
    // 1. Verifica se há uma data de expiração regular
    if (user.expiration_date) {
        try {
            const expDate = new Date(user.expiration_date);
            const today = new Date();
            today.setHours(0, 0, 0, 0); // Compara apenas a data
            const daysLeft = Math.ceil((expDate.getTime() - today.getTime()) / (1000 * 3600 * 24));
            const dateColor = daysLeft < 0 ? 'text-red-500 font-semibold' : (daysLeft <= 7 ? 'text-yellow-500 font-semibold' : 'text-gray-400 dark:text-gray-500');
            const formattedDate = expDate.toLocaleDateString();
            statusHtml = `<div class="mt-1 text-xs flex items-center ${dateColor}"><span>${i18n.expiresOn} ${formattedDate}</span></div>`;
        } catch(e) { /* ignora erros de parsing */ }
    }
    // 2. Se NÃO houver data de expiração regular, verifica a data de teste
    else if (user.trial_end_date) {
        try {
            const trialEndDate = new Date(user.trial_end_date);
            const diffMs = trialEndDate - new Date();
            if (diffMs > 0) {
                // Em teste
                const diffHours = Math.floor(diffMs / 3600000);
                const diffMinutes = Math.round((diffMs % 3600000) / 60000);
                const remainingTime = diffHours > 0 ? `${diffHours}h ${diffMinutes}m` : `${diffMinutes}m`;
                statusHtml = `<div class="mt-2 text-xs font-bold bg-purple-100 text-purple-800 dark:bg-purple-900/50 dark:text-purple-300 px-2 py-1 rounded-full inline-flex items-center gap-1"><span>${i18n.inTestWithTime.replace('{remainingTime}', remainingTime)}</span></div>`;
            } else {
                // Teste finalizado
                 statusHtml = `<div class="mt-2 text-xs font-bold bg-gray-200 text-gray-800 dark:bg-gray-600 dark:text-gray-200 px-2 py-1 rounded-full inline-flex items-center gap-1"><span>${i18n.testFinished}</span></div>`;
            }
        } catch(e) { /* ignora erros de parsing */ }
    }
    // --- FIM DA CORREÇÃO ---


    const inactiveButtons = `
        <button data-action="reactivate" title="${i18n.reactivate}" class="btn text-xs bg-green-600 hover:bg-green-700 text-white px-2 py-1">${i18n.reactivate}</button>
        <button data-action="delete-permanently" title="${i18n.deletePermanently}" class="btn text-xs bg-red-800 hover:bg-red-700 text-white px-2 py-1">${i18n.deletePermanently}</button>
    `;

    // --- LÓGICA ALTERADA: Mostra o botão se "trial_end_date" existir E não houver expiration_date
    const extendTrialButton = (user.trial_end_date && !user.expiration_date) ? `
        <button data-action="extend-trial" title="${i18n.extendTrial}" class="btn text-xs bg-orange-600 hover:bg-orange-700 text-white px-2 py-1">${i18n.extendTrial}</button>
    ` : '';
    // --- FIM DA ALTERAÇÃO ---

    const activeButtons = `
        <button data-action="renew-month" title="${i18n.addOneMonth}" class="btn text-xs bg-green-600 hover:bg-green-700 text-white px-2 py-1">${i18n.addOneMonth}</button>
        ${extendTrialButton}
        <div class="flex items-center justify-end flex-wrap gap-1">
            <button data-action="copy-payment-link" title="${i18n.copyPaymentLink}" class="p-2 rounded-full text-gray-500 hover:bg-teal-100 dark:hover:bg-teal-500/20 dark:text-teal-400"><svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10.293 3.293a1 1 0 011.414 0l6 6a1 1 0 010 1.414l-6 6a1 1 0 01-1.414-1.414L14.586 11H3a1 1 0 110-2h11.586l-4.293-4.293a1 1 0 010-1.414z" clip-rule="evenodd" /></svg></button>
            <button data-action="manage-profile" title="${i18n.manageProfileAndExpiration}" class="p-2 rounded-full text-gray-500 hover:bg-green-100 dark:hover:bg-green-500/20 dark:text-green-400"><svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path d="M10 2a5 5 0 00-5 5v2a2 2 0 00-2 2v5a2 2 0 002 2h10a2 2 0 002-2v-5a2 2 0 00-2-2V7a5 5 0 00-5-5zm0 10a3 3 0 100-6 3 3 0 000 6z" /></svg></button>
            <button data-action="payment-history" title="${i18n.paymentHistory}" class="p-2 rounded-full text-gray-500 hover:bg-yellow-100 dark:hover:bg-yellow-500/20 dark:text-yellow-400"><svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path d="M10.707 2.293a1 1 0 00-1.414 0l-7 7a1 1 0 001.414 1.414L4 10.414V17a1 1 0 001 1h2a1 1 0 001-1v-2a1 1 0 011-1h2a1 1 0 011 1v2a1 1 0 001 1h2a1 1 0 001-1v-6.586l.293.293a1 1 0 001.414-1.414l-7-7z" /></svg></button>
            <button data-action="manage-libraries" title="${i18n.manageLibraries}" class="p-2 rounded-full text-gray-500 hover:bg-purple-100 dark:hover:bg-purple-500/20 dark:text-purple-400"><svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path d="M7 3a1 1 0 000 2h6a1 1 0 100-2H7zM4 7a1 1 0 011-1h10a1 1 0 110 2H5a1 1 0 01-1-1zM2 11a2 2 0 012-2h12a2 2 0 012 2v4a2 2 0 01-2-2H4a2 2 0 01-2-2v-4z" /></svg></button>
            <button data-action="manage-limit" title="${i18n.manageScreenLimit}" class="p-2 rounded-full text-gray-500 hover:bg-blue-100 dark:hover:bg-blue-500/20 dark:text-blue-400"><svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M3 5a2 2 0 012-2h10a2 2 0 012 2v10a2 2 0 01-2-2H5a2 2 0 01-2-2V5zm4 0h6v10H7V5z" clip-rule="evenodd" /></svg></button>
            ${user.is_blocked ? `<button data-action="unblock" title="${i18n.unblock}" class="p-2 rounded-full text-gray-500 hover:bg-yellow-100 dark:hover:bg-yellow-500/20 dark:text-yellow-400"><svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd" /></svg></button>`: `<button data-action="block" title="${i18n.block}" class="p-2 rounded-full text-gray-500 hover:bg-red-100 dark:hover:bg-red-500/20 dark:text-red-400"><svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clip-rule="evenodd" /></svg></button>`}
            <button data-action="remove" title="${i18n.removeUserButton}" class="p-2 rounded-full text-gray-500 hover:bg-gray-200 dark:hover:bg-gray-600 dark:text-gray-400"><svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z" clip-rule="evenodd" /></svg></button>
        </div>
    `;

    const buttonContainerClass = isInactive
        ? 'flex flex-wrap items-center justify-end gap-1'
        : 'flex flex-wrap items-center justify-between gap-1';

    card.innerHTML = `
        <div class="flex items-start flex-1">
            <img src="${user.thumb || 'https://placehold.co/80x80/1F2937/E5E7EB?text=?'}" alt="Avatar" class="w-16 h-16 rounded-full mr-4">
            <div class="flex-1">
                <div class="flex items-center gap-2">
                    <div class="w-3 h-3 rounded-full ${isInactive ? 'bg-gray-500' : (user.is_blocked ? 'bg-red-500' : 'bg-green-500')}" title="${isInactive ? i18n.inactiveTitle : (user.is_blocked ? i18n.blockedTitle : i18n.activeTitle)}"></div>
                    <p class="font-semibold text-gray-900 dark:text-white text-lg">${user.username}</p>
                </div>
                <p class="text-sm text-gray-500 dark:text-gray-400 truncate">${user.email || ''}</p>
                ${statusHtml}
                ${user.screen_limit > 0 ? `<div class="mt-2 text-xs font-bold bg-blue-100 text-blue-800 dark:bg-blue-500/50 dark:text-blue-200 border border-blue-200 dark:border-blue-400/30 px-2 py-1 rounded-full inline-block">${user.screen_limit} ${user.screen_limit > 1 ? i18n.screenPlural : i18n.screenSingular}</div>` : ''}
            </div>
        </div>
        <div class="${buttonContainerClass} mt-4 pt-4 border-t border-gray-200 dark:border-gray-700">
            ${isInactive ? inactiveButtons : activeButtons}
        </div>
    `;
    card.querySelectorAll('button').forEach(button => button.onclick = () => handleUserAction(button.dataset.action, user));
    return card;
}

// --- Função Principal de Carregamento ---
/**
 * Carrega todos os dados da página (status, convites).
 * @param {boolean} force - Se deve forçar a atualização dos dados do Plex.
 */
export async function loadStatus(force = false) {
    dom.refreshButton.disabled = true;
    dom.refreshButton.querySelector('svg').classList.add('animate-spin');
    try {
        const data = await api.fetchStatus(force);
        state.setAllUsersCache(data.users || []);
        state.setAllLibraries(data.libraries || []);
        updateTabCounts();
        renderUserGrid();
        await loadInvites(); // Recarrega convites após atualizar utilizadores
    } catch (e) {
        dom.userGrid.innerHTML = `<p class="text-red-500 text-center col-span-full">${i18n.loadingUsersFailed} ${e.message}</p>`;
    } finally {
        dom.refreshButton.disabled = false;
        dom.refreshButton.querySelector('svg').classList.remove('animate-spin');
    }
}

export function updateTabCounts() {
    dom.countAll.textContent = state.allUsersCache.length;
    dom.countActive.textContent = state.allUsersCache.filter(u => !u.is_blocked && u.status === 'active').length; // CORREÇÃO: Não conta trial aqui
    dom.countBlocked.textContent = state.allUsersCache.filter(u => u.is_blocked).length;
    // CORREÇÃO: Conta apenas quem está *atualmente* em teste
    dom.countTrial.textContent = state.allUsersCache.filter(u => u.is_on_trial && u.trial_end_date && new Date(u.trial_end_date) > new Date()).length;
    dom.countInactive.textContent = state.allUsersCache.filter(u => u.status === 'inactive').length;
}