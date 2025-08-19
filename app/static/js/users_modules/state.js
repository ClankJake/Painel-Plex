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
