// app/static/js/users_modules/ui.js

import * as dom from './dom.js';
import * as state from './state.js';
import * as api from './api.js';
import { i18n, urls } from './config.js';
import { showToast, escapeHTML } from '../utils.js';
import { handleInviteAction, handleUserAction } from './handlers.js';
import * as modals from './modals.js';

/**
 * Módulo de UI (Interface do Utilizador)
 * Responsável pela renderização do conteúdo principal (grelha de utilizadores, convites, contadores).
 */

// Re-exporta as funções dos modais para acesso global
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
    showInviteDetailsModal
} = modals;

// ==========================================
// HELPERS DE UTILIDADE
// ==========================================

/**
 * Sanitiza entradas de texto para prevenir ataques XSS ao injetar no HTML.
 *
 * 🛡️ NOTA: usa `escapeHTML`, que escapa TAMBÉM as aspas. A versão anterior
 * baseava-se em `textContent`/`innerHTML`, que deixa `"` e `'` intactos — o que
 * é seguro no corpo do HTML, mas não dentro de um atributo. Como estes valores
 * acabam em `value="..."`, `title="..."` e `data-code="..."`, o título de uma
 * biblioteca com aspas truncava silenciosamente o `value` da checkbox e o
 * administrador partilhava bibliotecas diferentes das que escolheu.
 */
const sanitizeHTML = (str) => escapeHTML(str);

/**
 * Idioma ativo da página (definido em `<html lang="...">`), para que datas e
 * números sigam a tradução escolhida em vez de estarem presos a pt-BR.
 */
const pageLocale = document.documentElement.lang || undefined;

const formatDate = (date) => date.toLocaleDateString(pageLocale);
const formatDateTime = (date) => date.toLocaleString(pageLocale);

/**
 * Normaliza texto para pesquisa: minúsculas e sem acentos, para que "jose"
 * encontre "José" e vice-versa.
 */
const normalizeForSearch = (str) => String(str ?? '')
    .toLowerCase()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '');

/**
 * Alterna as classes do Tailwind para o estado de Abas (Tabs) ativas/inativas.
 */
const toggleTabStyles = (element, isActive) => {
    if (!element) return;
    const activeClasses = ['bg-white', 'dark:bg-gray-600', 'shadow', 'text-gray-900', 'dark:text-white'];
    const inactiveClasses = ['text-gray-500', 'dark:text-gray-400', 'hover:text-gray-700', 'dark:hover:text-gray-200'];
    
    if (isActive) {
        element.classList.add(...activeClasses);
        element.classList.remove(...inactiveClasses);
    } else {
        element.classList.add(...inactiveClasses);
        element.classList.remove(...activeClasses);
    }
};

// ==========================================
// RENDERIZAÇÃO DE CONVITES
// ==========================================

/**
 * Carrega todos os convites, atualiza o estado e processa a renderização.
 */
export async function loadInvites(isPeriodicCheck = false) {
    try {
        const invitesDict = await api.listInvites();
        const allInvites = Object.values(invitesDict).sort((a, b) => new Date(b.created_at) - new Date(a.created_at));

        state.setAllInvitesCache(allInvites);

        const activeInvitesCount = allInvites.filter(inv => {
            const isExpired = inv.expires_at && new Date(inv.expires_at) < new Date();
            const isFull = inv.use_count >= inv.max_uses;
            return !isExpired && !isFull;
        }).length;

        if (isPeriodicCheck && state.activeInviteCount > 0 && activeInvitesCount < state.activeInviteCount) {
            showToast(i18n.inviteUsedUpdating || 'Um convite foi utilizado! A atualizar...', 'info');
            await loadStatus(true);
        }

        state.setActiveInviteCount(activeInvitesCount);
        renderInvites(); 

    } catch (e) {
        if(dom.inviteListDiv) dom.inviteListDiv.innerHTML = `<p class="text-red-500">${i18n.error}: ${e.message}</p>`;
    }
}

/**
 * Filtra e desenha a lista de convites no ecrã consoante a aba ativa.
 */
export function renderInvites() {
    const currentTab = state.activeInviteTab; // 'active' ou 'history'
    const allInvites = state.allInvitesCache;

    // Atualiza visualmente os botões das abas
    toggleTabStyles(dom.inviteTabActive, currentTab === 'active');
    toggleTabStyles(dom.inviteTabHistory, currentTab === 'history');
    if (dom.inviteTabActive) dom.inviteTabActive.setAttribute('aria-selected', String(currentTab === 'active'));
    if (dom.inviteTabHistory) dom.inviteTabHistory.setAttribute('aria-selected', String(currentTab === 'history'));

    // Filtra convites
    const filteredInvites = allInvites.filter(inv => {
        const isExpired = inv.expires_at && new Date(inv.expires_at) < new Date();
        const isFull = inv.use_count >= inv.max_uses;
        const isActive = !isExpired && !isFull;
        return currentTab === 'active' ? isActive : !isActive;
    });

    if (!dom.inviteListDiv) return;

    dom.inviteListDiv.innerHTML = filteredInvites.length > 0
        ? filteredInvites.map(renderInviteCard).join('')
        : `<p class="text-gray-500 dark:text-gray-400 text-sm text-center py-4">${i18n.noPendingInvites || 'Sem convites para mostrar.'}</p>`;

    // Delegação direta de eventos nos botões gerados
    dom.inviteListDiv.querySelectorAll('button').forEach(button => {
        button.onclick = () => {
            const code = button.dataset.code;
            if (button.dataset.action === 'details') {
                const inviteDetails = allInvites.find(inv => inv.code === code);
                handleInviteAction('details', code, inviteDetails);
            } else {
                handleInviteAction(button.dataset.action, code);
            }
        };
    });
}

export function handleInviteTabChange(tab) {
    state.setActiveInviteTab(tab);
    renderInvites();
}

/**
 * Retorna o HTML de um cartão de convite único.
 */
function renderInviteCard(details) {
    const { code, expires_at, use_count, max_uses, trial_duration_minutes } = details;
    const isExpired = expires_at && new Date(expires_at) < new Date();
    const isFull = use_count >= max_uses;
    const isActive = !isExpired && !isFull;
    const safeCode = sanitizeHTML(code);

    let statusHtml = '';
    if (isActive) {
         statusHtml = `<span class="px-2 py-1 text-xs font-medium rounded-full text-green-800 bg-green-100 dark:bg-green-900/30 dark:text-green-300">${i18n.active}</span>`;
    } else if (isFull) {
         statusHtml = `<span class="px-2 py-1 text-xs font-medium rounded-full text-gray-800 bg-gray-200 dark:bg-gray-700 dark:text-gray-300">${i18n.exhausted || 'Esgotado'}</span>`;
    } else {
         statusHtml = `<span class="px-2 py-1 text-xs font-medium rounded-full text-red-800 bg-red-100 dark:bg-red-900/30 dark:text-red-300">${i18n.expired}</span>`;
    }

    const usageHtml = `<span class="text-xs text-gray-500 dark:text-gray-400">${use_count}/${max_uses} ${i18n.uses}</span>`;
    const trialHtml = trial_duration_minutes > 0 ? `<span class="px-2 py-1 text-xs font-medium text-purple-800 bg-purple-100 rounded-full dark:bg-purple-900/30 dark:text-purple-300">${i18n.trial}</span>` : '';
    let expirationHtml = '';

    if (expires_at) {
        const expDate = new Date(expires_at);
        if (!isExpired) {
            const diffMinutes = Math.floor((expDate - new Date()) / 60000);
            const diffHours = Math.floor(diffMinutes / 60);
            const diffDays = Math.floor(diffHours / 24);
            
            const expiresInText = diffDays > 0 ? i18n.expiresInDays.replace('{days}', diffDays) : 
                                 (diffHours > 0 ? i18n.expiresInHours.replace('{hours}', diffHours) : 
                                 i18n.expiresInMinutes.replace('{minutes}', diffMinutes));
            
            expirationHtml = `<span class="text-xs text-gray-500 dark:text-gray-400 flex items-center gap-1"><svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>${expiresInText}</span>`;
        } else {
            expirationHtml = `<span class="text-xs text-gray-500 dark:text-gray-400 flex items-center gap-1" title="${sanitizeHTML(`${i18n.expiredOn || 'Vencido em:'} ${formatDateTime(expDate)}`)}"><svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" /></svg> ${formatDate(expDate)}</span>`;
        }
    }

    return `
    <div class="flex items-center justify-between p-2 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-700/50 border border-gray-100 dark:border-gray-700/30">
        <div class="flex items-center gap-3 flex-wrap">
            <span class="font-mono font-bold text-sm text-gray-900 dark:text-white">${safeCode}</span>
            ${statusHtml} ${trialHtml} ${usageHtml} ${expirationHtml}
        </div>
        <div class="flex items-center gap-2">
            <button data-action="copy-invite" data-code="${safeCode}" title="${i18n.copyLink}" class="p-2 rounded-full text-gray-500 hover:bg-blue-100 dark:hover:bg-blue-500/20 transition-colors" ${!isActive ? 'disabled style="opacity: 0.5; cursor: default;"' : ''}>
                <svg class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path d="M8 3a1 1 0 011-1h2a1 1 0 110 2H9a1 1 0 01-1-1z" /><path d="M6 3a2 2 0 00-2 2v11a2 2 0 002 2h8a2 2 0 002-2V5a2 2 0 00-2-2 3 3 0 01-3 3H9a3 3 0 01-3-3z" /></svg>
            </button>
            <button data-action="details" data-code="${safeCode}" title="${i18n.inviteDetails || 'Detalhes'}" class="p-2 rounded-full text-gray-500 hover:bg-yellow-100 dark:hover:bg-yellow-500/20 transition-colors">
                <svg class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clip-rule="evenodd" /></svg>
            </button>
            <button data-action="delete-invite" data-code="${safeCode}" title="${i18n.deleteInvite}" class="p-2 rounded-full text-gray-500 hover:bg-red-100 dark:hover:bg-red-500/20 transition-colors">
                <svg class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z" clip-rule="evenodd" /></svg>
            </button>
        </div>
    </div>`;
}

// ==========================================
// RENDERIZAÇÃO DE UTILIZADORES
// ==========================================

/**
 * Desenha a grelha de utilizadores combinando filtros de Aba, Busca e Ordenação.
 */
export function renderUserGrid() {
    let usersToRender = [...state.allUsersCache];

    // 1. Filtragem por Aba
    if (state.viewState.filter === 'active') {
        usersToRender = usersToRender.filter(u => !u.is_blocked && u.status === 'active');
    } else if (state.viewState.filter === 'blocked') {
        usersToRender = usersToRender.filter(u => u.is_blocked);
    } else if (state.viewState.filter === 'trial') {
        usersToRender = usersToRender.filter(u => u.is_on_trial && u.trial_end_date && new Date(u.trial_end_date) > new Date());
    } else if (state.viewState.filter === 'inactive') {
        usersToRender = usersToRender.filter(u => u.status === 'inactive');
    }

    // 2. Pesquisa de Texto (insensível a maiúsculas E a acentos)
    const rawTerm = (state.viewState.searchTerm || '').trim();
    if (rawTerm) {
        const term = normalizeForSearch(rawTerm);
        usersToRender = usersToRender.filter(u =>
            normalizeForSearch(u.username).includes(term) ||
            normalizeForSearch(u.email).includes(term) ||
            normalizeForSearch(u.name).includes(term)
        );
    }

    // 3. Ordenação Otimizada
    usersToRender.sort((a, b) => {
        const order = state.viewState.sortBy;
        if (order === 'name_asc') return (a.username || '').localeCompare(b.username || '', pageLocale, { sensitivity: 'base' });
        if (order === 'name_desc') return (b.username || '').localeCompare(a.username || '', pageLocale, { sensitivity: 'base' });
        
        if (order.startsWith('exp_')) {
            const timeA = a.expiration_date ? new Date(a.expiration_date).getTime() : 0;
            const timeB = b.expiration_date ? new Date(b.expiration_date).getTime() : 0;
            
            // Lida com datas inexistentes enviando-as para o fim/início
            if (!timeA && !timeB) return 0;
            if (!timeA) return order === 'exp_asc' ? 1 : -1;
            if (!timeB) return order === 'exp_asc' ? -1 : 1;
            
            return order === 'exp_asc' ? (timeA - timeB) : (timeB - timeA);
        }
        return 0;
    });

    // 4. Construção do DOM
    if (!dom.userGrid) return;
    dom.userGrid.innerHTML = '';
    
    if (usersToRender.length > 0) {
        const fragment = document.createDocumentFragment();
        usersToRender.forEach(user => fragment.appendChild(renderUserCard(user)));
        dom.userGrid.appendChild(fragment);
    } else {
        dom.userGrid.innerHTML = `<p class="text-gray-500 dark:text-gray-400 text-center col-span-full py-10">${i18n.noUsersFound}</p>`;
    }
}

/**
 * Cria o Node HTML para um único cartão de utilizador.
 */
function renderUserCard(user) {
    const card = document.createElement('div');
    card.className = 'flex flex-col bg-white dark:bg-gray-800 p-4 rounded-lg hover:shadow-xl transition-shadow duration-300 border border-gray-200 dark:border-gray-700';

    const isInactive = user.status === 'inactive';
    const sUsername = sanitizeHTML(user.username);
    const sName = sanitizeHTML(user.name);
    const sEmail = sanitizeHTML(user.email);
    
    // Gerar Avatar Seguro.
    // A inicial é retirada do nome ORIGINAL: `sUsername` já está escapado, e um
    // nome começado por "&" daria "&" (o primeiro caráter de "&amp;").
    const initial = encodeURIComponent((user.username || 'U').trim().charAt(0).toUpperCase() || 'U');
    const fallbackAvatar = `https://placehold.co/80x80/1F2937/E5E7EB?text=${initial}`;
    const safeAvatar = sanitizeHTML(user.thumb || fallbackAvatar);

    // Avaliação de Status (Expiração vs Teste)
    let statusHtml = ''; 
    if (user.expiration_date) {
        try {
            const expDate = new Date(user.expiration_date);
            const now = new Date();
            
            // 🛡️ CORREÇÃO TIMEZONE FRONTEND: Remove as horas de ambas as datas para avaliar puramente os dias
            const expDateOnly = new Date(expDate.getFullYear(), expDate.getMonth(), expDate.getDate());
            const todayOnly = new Date(now.getFullYear(), now.getMonth(), now.getDate());
            
            const daysLeft = Math.round((expDateOnly - todayOnly) / (1000 * 3600 * 24));
            const isExpired = expDate < now; // Usa a hora exata real para definir se já expirou completamente
            
            // Cores: Vermelho se expirado (isExpired = true ou daysLeft < 0), Amarelo se faltarem <= 7 dias.
            const dateColor = isExpired ? 'text-red-600 dark:text-red-500 font-bold' : (daysLeft <= 7 ? 'text-yellow-600 dark:text-yellow-500 font-bold' : 'text-gray-500 dark:text-gray-400');
            const labelText = isExpired ? (i18n.expiredOn || 'Vencido em:') : i18n.expiresOn;

            statusHtml = `<div class="mt-1 text-xs flex items-center ${dateColor}"><span>${sanitizeHTML(labelText)} ${formatDate(expDate)}</span></div>`;
        } catch(e) { }
    } else if (user.trial_end_date) {
        try {
            const diffMs = new Date(user.trial_end_date) - new Date();
            if (diffMs > 0) {
                const diffHours = Math.floor(diffMs / 3600000);
                const diffMinutes = Math.round((diffMs % 3600000) / 60000);
                const remainingTime = diffHours > 0 ? `${diffHours}h ${diffMinutes}m` : `${diffMinutes}m`;
                statusHtml = `<div class="mt-2 text-xs font-bold bg-purple-100 text-purple-800 dark:bg-purple-900/50 dark:text-purple-300 px-2 py-1 rounded-full inline-flex items-center gap-1"><span>${i18n.inTestWithTime.replace('{remainingTime}', remainingTime)}</span></div>`;
            } else {
                 statusHtml = `<div class="mt-2 text-xs font-bold bg-gray-200 text-gray-800 dark:bg-gray-600 dark:text-gray-200 px-2 py-1 rounded-full inline-flex items-center gap-1"><span>${i18n.testFinished}</span></div>`;
            }
        } catch(e) { }
    }

    const extendTrialButton = (user.trial_end_date && !user.expiration_date) ? `
        <button data-action="extend-trial" title="${i18n.extendTrial}" class="btn text-xs bg-orange-600 hover:bg-orange-700 text-white px-2 py-1">${i18n.extendTrial}</button>
    ` : '';

    const inactiveButtons = `
        <button data-action="reactivate" title="${i18n.reactivate}" class="btn text-xs bg-green-600 hover:bg-green-700 text-white px-2 py-1 shadow-sm">${i18n.reactivate}</button>
        <button data-action="copy-payment-link" title="${i18n.copyPaymentLink}" class="p-2 rounded-full text-gray-500 hover:bg-teal-100 dark:hover:bg-teal-500/20 dark:text-teal-400 transition-colors"><svg class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10.293 3.293a1 1 0 011.414 0l6 6a1 1 0 010 1.414l-6 6a1 1 0 01-1.414-1.414L14.586 11H3a1 1 0 110-2h11.586l-4.293-4.293a1 1 0 010-1.414z" clip-rule="evenodd" /></svg></button>
        <button data-action="delete-permanently" title="${i18n.deletePermanently}" class="btn text-xs bg-red-800 hover:bg-red-700 text-white px-2 py-1 shadow-sm">${i18n.deletePermanently}</button>
    `;

    const activeButtons = `
        <button data-action="renew-month" title="${i18n.addOneMonth}" class="btn text-xs bg-green-600 hover:bg-green-700 text-white px-2 py-1 shadow-sm">${i18n.addOneMonth}</button>
        ${extendTrialButton}
        <div class="flex items-center justify-end flex-wrap gap-1">
            <button data-action="copy-payment-link" title="${i18n.copyPaymentLink}" class="p-2 rounded-full text-gray-500 hover:bg-teal-100 dark:hover:bg-teal-500/20 dark:text-teal-400 transition-colors"><svg class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10.293 3.293a1 1 0 011.414 0l6 6a1 1 0 010 1.414l-6 6a1 1 0 01-1.414-1.414L14.586 11H3a1 1 0 110-2h11.586l-4.293-4.293a1 1 0 010-1.414z" clip-rule="evenodd" /></svg></button>
            <button data-action="manage-profile" title="${i18n.manageProfileAndExpiration}" class="p-2 rounded-full text-gray-500 hover:bg-green-100 dark:hover:bg-green-500/20 dark:text-green-400 transition-colors"><svg class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path d="M10 2a5 5 0 00-5 5v2a2 2 0 00-2 2v5a2 2 0 002 2h10a2 2 0 002-2v-5a2 2 0 00-2-2V7a5 5 0 00-5-5zm0 10a3 3 0 100-6 3 3 0 000 6z" /></svg></button>
            <button data-action="payment-history" title="${i18n.paymentHistory}" class="p-2 rounded-full text-gray-500 hover:bg-yellow-100 dark:hover:bg-yellow-500/20 dark:text-yellow-400 transition-colors"><svg class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path d="M10.707 2.293a1 1 0 00-1.414 0l-7 7a1 1 0 001.414 1.414L4 10.414V17a1 1 0 001 1h2a1 1 0 001-1v-2a1 1 0 011-1h2a1 1 0 011 1v2a1 1 0 001 1h2a1 1 0 001-1v-6.586l.293.293a1 1 0 001.414-1.414l-7-7z" /></svg></button>
            <button data-action="manage-libraries" title="${i18n.manageLibraries}" class="p-2 rounded-full text-gray-500 hover:bg-purple-100 dark:hover:bg-purple-500/20 dark:text-purple-400 transition-colors"><svg class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path d="M7 3a1 1 0 000 2h6a1 1 0 100-2H7zM4 7a1 1 0 011-1h10a1 1 0 110 2H5a1 1 0 01-1-1zM2 11a2 2 0 012-2h12a2 2 0 012 2v4a2 2 0 01-2-2H4a2 2 0 01-2-2v-4z" /></svg></button>
            <button data-action="manage-limit" title="${i18n.manageScreenLimit}" class="p-2 rounded-full text-gray-500 hover:bg-blue-100 dark:hover:bg-blue-500/20 dark:text-blue-400 transition-colors"><svg class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M3 5a2 2 0 012-2h10a2 2 0 012 2v10a2 2 0 01-2-2H5a2 2 0 01-2-2V5zm4 0h6v10H7V5z" clip-rule="evenodd" /></svg></button>
            ${user.is_blocked ? 
                `<button data-action="unblock" title="${i18n.unblock}" class="p-2 rounded-full text-gray-500 hover:bg-yellow-100 dark:hover:bg-yellow-500/20 dark:text-yellow-400 transition-colors"><svg class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd" /></svg></button>`: 
                `<button data-action="block" title="${i18n.block}" class="p-2 rounded-full text-gray-500 hover:bg-red-100 dark:hover:bg-red-500/20 dark:text-red-400 transition-colors"><svg class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clip-rule="evenodd" /></svg></button>`}
            <button data-action="remove" title="${i18n.removeUserButton}" class="p-2 rounded-full text-gray-500 hover:bg-gray-200 dark:hover:bg-gray-600 dark:text-gray-400 transition-colors"><svg class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z" clip-rule="evenodd" /></svg></button>
        </div>
    `;

    const statusIndicatorColor = isInactive ? 'bg-gray-500' : (user.is_blocked ? 'bg-red-500' : 'bg-green-500');
    const statusIndicatorTitle = isInactive ? i18n.inactiveTitle : (user.is_blocked ? i18n.blockedTitle : i18n.activeTitle);

    card.innerHTML = `
        <div class="flex items-start flex-1">
            <img src="${safeAvatar}" alt="" loading="lazy" class="user-avatar w-16 h-16 rounded-full mr-4 border border-gray-200 dark:border-gray-700 object-cover bg-gray-100 dark:bg-gray-900">
            <div class="flex-1 min-w-0">
                <div class="flex items-center gap-2">
                    <div class="w-3 h-3 rounded-full ${statusIndicatorColor} shadow-sm flex-shrink-0" title="${statusIndicatorTitle}"></div>
                    <p class="font-semibold text-gray-900 dark:text-white text-lg truncate">${sUsername}</p>
                </div>
                ${sName ? `<p class="text-sm text-gray-600 dark:text-gray-300 font-medium truncate">${sName}</p>` : ''}
                <p class="text-sm text-gray-500 dark:text-gray-400 truncate">${sEmail}</p>
                ${statusHtml}
                ${user.screen_limit > 0 ? `<div class="mt-2 text-xs font-bold bg-blue-100 text-blue-800 dark:bg-blue-500/30 dark:text-blue-200 border border-blue-200 dark:border-blue-400/30 px-2.5 py-1 rounded-full inline-block">${user.screen_limit} ${user.screen_limit > 1 ? i18n.screenPlural : i18n.screenSingular}</div>` : ''}
            </div>
        </div>
        <div class="${isInactive ? 'flex flex-wrap items-center justify-end gap-1' : 'flex flex-wrap items-center justify-between gap-1'} mt-4 pt-4 border-t border-gray-200 dark:border-gray-700">
            ${isInactive ? inactiveButtons : activeButtons}
        </div>
    `;

    // 🐛 CORREÇÃO: o fallback do avatar estava num atributo `onerror` inline que
    // voltava a apontar para o mesmo endereço externo. Se esse endereço também
    // falhasse (rede fechada, servidor em baixo), o `onerror` disparava sem fim.
    // Agora é ligado por JS e desliga-se após a primeira tentativa.
    const avatarImg = card.querySelector('.user-avatar');
    if (avatarImg) {
        avatarImg.onerror = () => {
            avatarImg.onerror = null;
            if (avatarImg.src !== fallbackAvatar) avatarImg.src = fallbackAvatar;
        };
    }

    // Vincula a ação de clique nos botões recém-criados
    card.querySelectorAll('button').forEach(button => {
        button.onclick = () => handleUserAction(button.dataset.action, user);
    });

    return card;
}

// ==========================================
// CICLO DE VIDA (REFRESH E STATUS)
// ==========================================

/**
 * Atualiza todos os dados do painel, incluindo utilizadores e convites.
 */
export async function loadStatus(force = false) {
    if (dom.refreshButton) {
        dom.refreshButton.disabled = true;
        const icon = dom.refreshButton.querySelector('svg');
        if (icon) icon.classList.add('animate-spin');
    }
    if (dom.userGrid) dom.userGrid.setAttribute('aria-busy', 'true');

    try {
        const data = await api.fetchStatus(force);
        state.setAllUsersCache(data.users || []);
        state.setAllLibraries(data.libraries || []);
        state.setTelegramEnabled(data.telegram_enabled);
        
        updateTabCounts();
        renderUserGrid();
        await loadInvites(); 
    } catch (e) {
        if (dom.userGrid) {
            dom.userGrid.innerHTML = `<p class="text-red-500 text-center font-semibold col-span-full py-10">${i18n.loadingUsersFailed} <br><span class="text-sm font-normal">${e.message}</span></p>`;
        }
    } finally {
        if (dom.refreshButton) {
            dom.refreshButton.disabled = false;
            const icon = dom.refreshButton.querySelector('svg');
            if (icon) icon.classList.remove('animate-spin');
        }
        if (dom.userGrid) dom.userGrid.setAttribute('aria-busy', 'false');
    }
}

/**
 * Atualiza os emblemas numéricos (badges) nos botões das abas superiores.
 */
export function updateTabCounts() {
    if (!dom.countAll) return;
    
    dom.countAll.textContent = state.allUsersCache.length;
    dom.countActive.textContent = state.allUsersCache.filter(u => !u.is_blocked && u.status === 'active').length;
    dom.countBlocked.textContent = state.allUsersCache.filter(u => u.is_blocked).length;
    dom.countTrial.textContent = state.allUsersCache.filter(u => u.is_on_trial && u.trial_end_date && new Date(u.trial_end_date) > new Date()).length;
    dom.countInactive.textContent = state.allUsersCache.filter(u => u.status === 'inactive').length;
}
