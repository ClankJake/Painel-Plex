// Armazena referências do DOM, estado e dados de configuração (URLs, i18n)

// Estado e Configuração
export const state = {
    urls: {},
    i18n: {},
    monthlyRevenueChart: null,
    userStatusChart: null,
    activeTimers: {}, // Relógios de reprodução das sessões ativas (ver stream_timers.js)
    timeAgoInterval: null,
    allUsersForSelection: [], // Cache para lista de usuários
    selectedUserIds: new Set(), // Estado para armazenar IDs selecionados
    safetyTimeout: null, // **NOVO**: Temporizador de segurança para o envio em massa
};

// Referências do DOM
export const dom = {
    loadingIndicator: null,
    dashboardContainer: null,
    errorContainer: null,
    errorMessage: null,
    realtimeStatus: null,
    sendBulkNotificationBtn: null,
    sendBulkBtnText: null,
    clearAllLogsBtn: null,
    targetOptionsDiv: null,
    openUserSelectionBtn: null,
    progressContainer: null,
    progressBar: null,
    progressText: null,
    progressPercent: null,
    auditLogContainer: null,
    summaryCardsContainer: null,
    revenueCanvas: null,
    userStatusCanvas: null,
    systemHealthContainer: null,
    activeStreamsSection: null,
    activeStreamsContainer: null,
    userSelectionModal: null, 
};

/**
 * Preenche os objetos de configuração `urls`, `i18n` e `dom`
 * lendo os dados do script tag e obtendo elementos do DOM.
 */
export function initConfig() {
    const scriptTag = document.getElementById('dashboard-script');
    if (scriptTag) {
        for (const key in scriptTag.dataset) {
            if (key.startsWith('i18n')) {
                const i18nKey = key.charAt(4).toLowerCase() + key.slice(5).replace(/-(\w)/g, (_, letter) => letter.toUpperCase());
                state.i18n[i18nKey] = scriptTag.dataset[key];
            } else {
                const urlKey = key.replace(/-(\w)/g, (match, letter) => letter.toUpperCase());
                state.urls[urlKey] = scriptTag.dataset[key];
            }
        }
    }

    // Preenche as referências do DOM
    dom.loadingIndicator = document.getElementById('loadingIndicator');
    dom.dashboardContainer = document.getElementById('dashboardContainer');
    dom.errorContainer = document.getElementById('errorContainer');
    dom.errorMessage = document.getElementById('errorMessage');
    dom.realtimeStatus = document.getElementById('realtime-status');
    dom.sendBulkNotificationBtn = document.getElementById('send-bulk-notification-btn');
    dom.sendBulkBtnText = document.getElementById('send-bulk-btn-text');
    dom.clearAllLogsBtn = document.getElementById('clear-all-logs-btn');
    dom.targetOptionsDiv = document.getElementById('target-options');
    dom.openUserSelectionBtn = document.getElementById('open-user-selection-modal-btn');
    dom.progressContainer = document.getElementById('bulk-progress-container');
    dom.progressBar = document.getElementById('bulk-progress-bar');
    dom.progressText = document.getElementById('bulk-progress-text');
    dom.progressPercent = document.getElementById('bulk-progress-percent');
    dom.auditLogContainer = document.getElementById('auditLogContainer');
    dom.summaryCardsContainer = document.getElementById('summaryCards');
    dom.revenueCanvas = document.getElementById('monthlyRevenueChart');
    dom.userStatusCanvas = document.getElementById('userStatusChart');
    dom.systemHealthContainer = document.getElementById('systemHealthContainer');
    dom.activeStreamsSection = document.getElementById('active-streams-section');
    dom.activeStreamsContainer = document.getElementById('activeStreamsContainer');
    dom.userSelectionModal = document.getElementById('userSelectionModal');
}

