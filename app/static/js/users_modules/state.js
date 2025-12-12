// app/static/js/users_modules/state.js

export let allLibraries = [];
export let allUsersCache = [];
export let activeInviteCount = 0;
export let inviteCheckInterval = null;
// **NOVO**: Estado para controlar a aba ativa dos convites. Default 'active'
export let activeInviteTab = 'active'; 
// **NOVO**: Cache para todos os convites
export let allInvitesCache = [];
// **NOVO**: Estado para controlar se o Telegram está habilitado
export let telegramEnabled = false;

const savedViewState = JSON.parse(localStorage.getItem('userListViewState'));
export let viewState = savedViewState || {
    filter: 'all',
    searchTerm: '',
    sortBy: 'name_asc'
};

export function setAllLibraries(libs) { allLibraries = libs; }
export function setAllUsersCache(users) { allUsersCache = users; }
export function setActiveInviteCount(count) { activeInviteCount = count; }
export function setInviteCheckInterval(intervalId) {
    if (inviteCheckInterval) clearInterval(inviteCheckInterval);
    inviteCheckInterval = intervalId;
}
export function setActiveInviteTab(tab) { activeInviteTab = tab; }
export function setAllInvitesCache(invites) { allInvitesCache = invites; }
export function setTelegramEnabled(enabled) { telegramEnabled = enabled; }

export function setViewState(newState) {
    viewState = { ...viewState, ...newState };
    localStorage.setItem('userListViewState', JSON.stringify(viewState));
}

// --- Funções para Atualização Otimista da UI (ID-centric) ---

export function updateUserInCache(plexUserId, updates) {
    const userIndex = allUsersCache.findIndex(u => u.id === plexUserId);
    if (userIndex > -1) {
        const originalUser = { ...allUsersCache[userIndex] };
        allUsersCache[userIndex] = { ...allUsersCache[userIndex], ...updates };
        return originalUser;
    }
    return null;
}

export function replaceUserInCache(user) {
    const userIndex = allUsersCache.findIndex(u => u.id === user.id);
    if (userIndex > -1) {
        allUsersCache[userIndex] = user;
    }
}

export function removeUserFromCache(plexUserId) {
    const userIndex = allUsersCache.findIndex(u => u.id === plexUserId);
    if (userIndex > -1) {
        return allUsersCache.splice(userIndex, 1)[0];
    }
    return null;
}

export function addUserToCache(user) {
    allUsersCache.push(user);
}