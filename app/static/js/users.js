import { fetchAPI, showToast, createModal } from './utils.js';

document.addEventListener('alpine:init', () => {
    Alpine.data('usersPage', () => ({
        // --- STATE ---
        isLoading: true,
        isLoadingInvites: true,
        allUsers: [],
        allLibraries: [],
        invites: [],
        viewState: {
            filter: 'all',
            searchTerm: '',
            sortBy: 'name_asc'
        },
        modals: {
            createInvite: false,
            // ... outros estados de modais aqui
        },
        
        // --- GETTERS (Propriedades Computadas) ---
        get filteredUsers() {
            let users = this.allUsers;
            // ... (lógica de filtro e ordenação movida para cá)
            return users;
        },
        get counts() {
            return {
                all: this.allUsers.length,
                active: this.allUsers.filter(u => !u.is_blocked && !u.is_on_trial).length,
                blocked: this.allUsers.filter(u => u.is_blocked).length,
                trial: this.allUsers.filter(u => u.is_on_trial).length,
            };
        },

        // --- METHODS ---
        init() {
            console.log('Componente de utilizadores inicializado com Alpine.js');
            this.loadStatus();
            // ... (carregar convites, etc.)
        },
        async loadStatus(force = false) {
            this.isLoading = true;
            try {
                const data = await fetchAPI(`/api/users/status?force=${force}`);
                this.allUsers = data.users || [];
                this.allLibraries = data.libraries || [];
            } catch (e) {
                showToast(`Falha ao carregar dados: ${e.message}`, 'error');
            } finally {
                this.isLoading = false;
            }
        },
        // ... (outras funções de ação como handleUserAction, showCreateInviteModal, etc.)
    }));
});
