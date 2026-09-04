/**
 * Módulo DOM
 * Centraliza a obtenção de referências para os elementos do DOM da página de configurações.
 */

export const form = document.getElementById('settingsForm');
export const saveButton = document.getElementById('saveButton');
export const saveBulkTemplatesButton = document.getElementById('saveBulkTemplatesButton');
export const logLevelSelector = document.getElementById('log_level_selector');
export const testTautulliButton = document.getElementById('testTautulliButton');
export const testOverseerrButton = document.getElementById('testOverseerrButton');
export const reauthPlexButton = document.getElementById('reauth-plex-button');
export const serverSelectionContainer = document.getElementById('server-selection-container');
export const logDisplay = document.getElementById('log-display');
export const toggleLogsButton = document.getElementById('toggle-logs');
export const clearLogsButton = document.getElementById('clear-logs');
export const languageSelector = document.getElementById('languageSelector');
export const helpModal = document.getElementById('placeholderHelpModal');
export const closeHelpModalButton = document.getElementById('closeHelpModal');
export const efiUseMtlsCheckbox = document.getElementById('EFI_USE_MTLS');
export const efiHmacSection = document.getElementById('efi-hmac-section');
export const generateHmacButton = document.getElementById('generateHmacSecret');
export const hmacInput = document.getElementById('EFI_WEBHOOK_HMAC_SECRET');
export const mainTabs = document.getElementById('main-tabs'); // Navegação horizontal
export const mainTabsSelect = document.getElementById('main-tabs-select'); // NOVO: Dropdown
export const mainTabContent = document.getElementById('main-tab-content');
export const plexTabs = document.getElementById('plex-tabs');
export const plexTabContent = document.getElementById('plex-tab-content');
export const paymentTabs = document.getElementById('payment-provider-tabs');
export const paymentTabContent = document.getElementById('payment-provider-tab-content');
export const notificationTabs = document.getElementById('notification-tabs');
export const notificationTabContent = document.getElementById('notification-tab-content');
export const comunicacoesTabs = document.getElementById('comunicacoes-tabs');
export const comunicacoesTabContent = document.getElementById('comunicacoes-tab-content');
