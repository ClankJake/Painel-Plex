// app/static/js/users_modules/state.js

/**
 * Módulo de Estado (State)
 * Responsável por armazenar as variáveis globais na memória do navegador (RAM)
 * para evitar fazer pedidos repetidos à API e acelerar a navegação.
 */

// ==========================================
// VARIÁVEIS DE ESTADO (MEMÓRIA)
// ==========================================

export let allLibraries = [];
export let allUsersCache = [];
export let activeInviteCount = 0;
export let inviteCheckInterval = null;
export let activeInviteTab = 'active'; 
export let allInvitesCache = [];
export let telegramEnabled = false;

// ==========================================
// PREFERÊNCIAS DO UTILIZADOR (PERSISTENTES)
// ==========================================

// Recuperação segura do LocalStorage (Previne crashes da página se o JSON estiver corrompido)
let savedViewState = null;
try {
    const rawData = localStorage.getItem('userListViewState');
    if (rawData) {
        savedViewState = JSON.parse(rawData);
    }
} catch (error) {
    console.warn('Erro ao ler viewState do localStorage. A restaurar definições padrão.', error);
    localStorage.removeItem('userListViewState');
}

// Estado padrão caso seja o primeiro acesso ou ficheiro corrompido
export let viewState = savedViewState || {
    filter: 'all',
    searchTerm: '',
    sortBy: 'name_asc'
};

// ==========================================
// SETTERS (FUNÇÕES DE ATUALIZAÇÃO BÁSICA)
// ==========================================

export function setAllLibraries(libs) { allLibraries = libs; }
export function setAllUsersCache(users) { allUsersCache = users; }
export function setActiveInviteCount(count) { activeInviteCount = count; }
export function setActiveInviteTab(tab) { activeInviteTab = tab; }
export function setAllInvitesCache(invites) { allInvitesCache = invites; }
export function setTelegramEnabled(enabled) { telegramEnabled = enabled; }

export function setInviteCheckInterval(intervalId) {
    if (inviteCheckInterval) clearInterval(inviteCheckInterval);
    inviteCheckInterval = intervalId;
}

export function setViewState(newState) {
    viewState = { ...viewState, ...newState };
    try {
        localStorage.setItem('userListViewState', JSON.stringify(viewState));
    } catch (e) {
        console.warn('Falha ao guardar viewState no localStorage', e);
    }
}

// ==========================================
// MUTAÇÕES DE CACHE (ATUALIZAÇÃO OTIMISTA)
// Estas funções alteram a interface imediatamente antes de o servidor responder.
// ==========================================

/**
 * Atualiza propriedades específicas de um utilizador sem alterar o resto.
 * @param {string} plexUserId ID do utilizador no Plex
 * @param {Object} updates Novo objeto com os dados a atualizar
 * @returns {Object|null} O utilizador original antes da alteração (para possível rollback)
 */
export function updateUserInCache(plexUserId, updates) {
    const userIndex = allUsersCache.findIndex(u => u.id === plexUserId);
    if (userIndex > -1) {
        // Guarda uma cópia do original para rollback caso o servidor falhe
        const originalUser = { ...allUsersCache[userIndex] };
        // Mescla as novidades de forma segura
        allUsersCache[userIndex] = { ...originalUser, ...updates };
        return originalUser;
    }
    return null;
}

/**
 * Substitui o objeto inteiro de um utilizador no cache.
 * @param {Object} user O novo objeto completo do utilizador
 */
export function replaceUserInCache(user) {
    const userIndex = allUsersCache.findIndex(u => u.id === user.id);
    if (userIndex > -1) {
        allUsersCache[userIndex] = user;
    }
}

/**
 * Remove um utilizador do cache localmente.
 * @param {string} plexUserId ID do utilizador no Plex
 * @returns {Object|null} O utilizador apagado (ou nulo se não encontrado)
 */
export function removeUserFromCache(plexUserId) {
    const userIndex = allUsersCache.findIndex(u => u.id === plexUserId);
    if (userIndex > -1) {
        return allUsersCache.splice(userIndex, 1)[0];
    }
    return null;
}

/**
 * Adiciona um novo utilizador ao fundo do cache.
 * @param {Object} user O objeto do utilizador recém-criado
 */
export function addUserToCache(user) {
    // Evita duplicados acidentais na mesma sessão
    if (!allUsersCache.find(u => u.id === user.id)) {
        allUsersCache.push(user);
    }
}
