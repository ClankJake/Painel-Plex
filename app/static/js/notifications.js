import { fetchAPI, showToast, createModal } from './utils.js';

document.addEventListener('DOMContentLoaded', () => {
    const scriptTag = document.getElementById('notifications-script');
    if (!scriptTag) return;

    const urls = {
        getNotifications: scriptTag.dataset.urlGetNotifications,
        markAllRead: scriptTag.dataset.urlMarkAllRead,
        clearAll: scriptTag.dataset.urlClearAll,
    };
    const i18n = {
        noNotifications: scriptTag.dataset.i18nNoNotifications,
        confirmClearTitle: scriptTag.dataset.i18nConfirmClearTitle,
        confirmClearMessage: scriptTag.dataset.i18nConfirmClearMessage,
        confirmButton: scriptTag.dataset.i18nConfirmButton,
        cancelButton: scriptTag.dataset.i18nCancelButton,
    };

    const notificationButton = document.getElementById('notification-button');
    const notificationPanel = document.getElementById('notification-panel');
    const notificationBadge = document.getElementById('notification-badge');
    const notificationList = document.getElementById('notification-list');
    const clearAllButton = document.getElementById('clear-all-notifications');

    if (!notificationButton || !notificationPanel) {
        return;
    }

    let isPanelOpen = false;
    let hasUnread = false;

    function formatTimeAgo(dateString) {
        const date = new Date(dateString);
        const now = new Date();
        const seconds = Math.floor((now - date) / 1000);

        let interval = seconds / 31536000;
        if (interval > 1) return `${Math.floor(interval)}a atrás`;
        interval = seconds / 2592000;
        if (interval > 1) return `${Math.floor(interval)}m atrás`;
        interval = seconds / 86400;
        if (interval > 1) return `${Math.floor(interval)}d atrás`;
        interval = seconds / 3600;
        if (interval > 1) return `${Math.floor(interval)}h atrás`;
        interval = seconds / 60;
        if (interval > 1) return `${Math.floor(interval)}min atrás`;
        return `${Math.floor(seconds)}s atrás`;
    }

    function getIconForCategory(category) {
        const icons = {
            success: '<svg class="w-5 h-5 text-green-500" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd" /></svg>',
            warning: '<svg class="w-5 h-5 text-yellow-500" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M8.257 3.099c.636-1.1 2.242-1.1 2.878 0l5.482 9.5c.636 1.1-.214 2.5-1.44 2.5H4.214c-1.225 0-2.075-1.4-1.44-2.5l5.483-9.5zM10 13a1 1 0 11-2 0 1 1 0 012 0zm-1-3a1 1 0 00-1 1v2a1 1 0 102 0v-2a1 1 0 00-1-1z" clip-rule="evenodd" /></svg>',
            error: '<svg class="w-5 h-5 text-red-500" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clip-rule="evenodd" /></svg>',
            info: '<svg class="w-5 h-5 text-blue-500" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clip-rule="evenodd" /></svg>',
        };
        return icons[category] || icons['info'];
    }

    async function fetchNotifications() {
        try {
            const data = await fetchAPI(urls.getNotifications);
            if (data.success) {
                hasUnread = data.unread_count > 0;
                updateNotificationUI(data.notifications, data.unread_count);
            }
        } catch (error) {
            console.error("Erro ao buscar notificações:", error);
        }
    }

    function updateNotificationUI(notifications, unreadCount) {
        // Atualiza o emblema (ponto vermelho)
        if (unreadCount > 0) {
            notificationBadge.classList.remove('hidden');
        } else {
            notificationBadge.classList.add('hidden');
        }

        // Atualiza a lista
        if (notifications.length === 0) {
            notificationList.innerHTML = `<p class="px-4 py-3 text-sm text-gray-500 dark:text-gray-400 text-center">${i18n.noNotifications}</p>`;
            clearAllButton.disabled = true;
        } else {
            notificationList.innerHTML = notifications.map(n => `
                <a href="${n.link || '#'}" class="block px-4 py-3 text-sm text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700 ${!n.is_read ? 'font-bold' : ''}">
                    <div class="flex items-start gap-3">
                        <div class="flex-shrink-0 pt-1">${getIconForCategory(n.category)}</div>
                        <div>
                            <p class="break-words">${n.message}</p>
                            <p class="text-xs text-gray-500 dark:text-gray-400 mt-1">${formatTimeAgo(n.timestamp + 'Z')}</p>
                        </div>
                    </div>
                </a>
            `).join('');
            clearAllButton.disabled = false;
        }
    }

    async function markAllAsRead() {
        if (!hasUnread) return;
        try {
            await fetchAPI(urls.markAllRead, 'POST');
            hasUnread = false;
            notificationBadge.classList.add('hidden');
            // Remove o negrito das mensagens na UI
            notificationList.querySelectorAll('a').forEach(el => el.classList.remove('font-bold'));
        } catch (error) {
            console.error("Erro ao marcar notificações como lidas:", error);
        }
    }

    function togglePanel() {
        isPanelOpen = !isPanelOpen;
        if (isPanelOpen) {
            notificationPanel.classList.remove('hidden');
            setTimeout(() => {
                notificationPanel.classList.remove('opacity-0', 'scale-95');
                notificationPanel.classList.add('opacity-100', 'scale-100');
            }, 10);
            markAllAsRead(); // Marca como lido ao abrir
        } else {
            notificationPanel.classList.remove('opacity-100', 'scale-100');
            notificationPanel.classList.add('opacity-0', 'scale-95');
            setTimeout(() => notificationPanel.classList.add('hidden'), 200);
        }
    }

    notificationButton.addEventListener('click', (e) => {
        e.stopPropagation();
        togglePanel();
    });

    document.addEventListener('click', (e) => {
        if (isPanelOpen && !notificationPanel.contains(e.target)) {
            togglePanel();
        }
    });

    clearAllButton.addEventListener('click', () => {
        const modal = createModal('notificationConfirmationModal', i18n.confirmClearTitle, `<p>${i18n.confirmClearMessage}</p>`,
            `<button id="modalConfirm" class="btn bg-red-600 text-white w-full sm:w-auto">${i18n.confirmButton}</button>
             <button id="modalCancel" class="btn bg-gray-200 text-gray-800 dark:bg-gray-600 dark:text-gray-200 w-full sm:w-auto">${i18n.cancelButton}</button>`
        );
        modal.querySelector('#modalConfirm').onclick = async () => {
            try {
                const data = await fetchAPI(urls.clearAll, 'POST');
                if (data.success) {
                    showToast(data.message, 'success');
                    fetchNotifications(); // Recarrega para mostrar a lista vazia
                }
            } catch (error) {
                showToast(error.message, 'error');
            } finally {
                modal.classList.add('hidden');
            }
        };
        modal.querySelector('#modalCancel').onclick = () => modal.classList.add('hidden');
    });

    // Carga inicial e polling
    fetchNotifications();
    setInterval(fetchNotifications, 30000); // Verifica a cada 30 segundos
});
