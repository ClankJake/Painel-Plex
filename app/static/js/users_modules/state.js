// app/static/js/users_modules/state.js

/**
 * Módulo de Estado
 * * Centraliza o estado da página de usuários, como o cache de usuários,
 * bibliotecas e o estado atual da visualização (filtros, ordenação).
 * Isto garante uma única fonte de verdade para os dados da página.
 */

export let allLibraries = [];
export let allUsersCache = [];
export let activeInviteCount = 0;
export let inviteCheckInterval = null;

// Carrega o estado da visualização do localStorage para persistir filtros entre visitas.
const savedViewState = JSON.parse(localStorage.getItem('userListViewState'));
export let viewState = savedViewState || {
    filter: 'all',
    searchTerm: '',
    sortBy: 'name_asc'
};

// Funções "setter" para modificar o estado de forma controlada.
export function setAllLibraries(libs) {
    allLibraries = libs;
}

export function setAllUsersCache(users) {
    allUsersCache = users;
}

export function setActiveInviteCount(count) {
    activeInviteCount = count;
}

export function setInviteCheckInterval(intervalId) {
    if (inviteCheckInterval) {
        clearInterval(inviteCheckInterval);
    }
    inviteCheckInterval = intervalId;
}

export function setViewState(newState) {
    viewState = { ...viewState, ...newState };
    localStorage.setItem('userListViewState', JSON.stringify(viewState));
}

// --- Funções para Atualização Otimista da UI ---

/**
 * Atualiza um único utilizador na cache e retorna o seu estado original.
 * @param {string} username - O nome de utilizador a ser atualizado.
 * @param {object} updates - Um objeto com os campos a serem atualizados.
 * @returns {object|null} O estado original do utilizador antes da atualização, ou null se não for encontrado.
 */
export function updateUserInCache(username, updates) {
    const userIndex = allUsersCache.findIndex(u => u.username === username);
    if (userIndex > -1) {
        const originalUser = { ...allUsersCache[userIndex] };
        allUsersCache[userIndex] = { ...allUsersCache[userIndex], ...updates };
        return originalUser;
    }
    return null;
}

/**
 * Substitui um utilizador na cache. Usado para reverter uma atualização otimista.
 * @param {object} user - O objeto completo do utilizador a ser colocado na cache.
 */
export function replaceUserInCache(user) {
    const userIndex = allUsersCache.findIndex(u => u.username === user.username);
    if (userIndex > -1) {
        allUsersCache[userIndex] = user;
    }
}

/**
 * Remove um utilizador da cache.
 * @param {string} username - O nome de utilizador a ser removido.
 * @returns {object|null} O utilizador removido, ou null se não for encontrado.
 */
export function removeUserFromCache(username) {
    const userIndex = allUsersCache.findIndex(u => u.username === username);
    if (userIndex > -1) {
        return allUsersCache.splice(userIndex, 1)[0];
    }
    return null;
}

/**
 * Adiciona um utilizador de volta à cache. Usado para reverter uma remoção otimista.
 * @param {object} user - O objeto do utilizador a ser adicionado.
 */
export function addUserToCache(user) {
    allUsersCache.push(user);
}
