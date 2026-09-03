/**
 * Módulo de Handlers (Manipuladores de Eventos)
 * Contém a lógica que é executada em resposta às interações do utilizador na página de configurações.
 */

import * as dom from './dom.js';
import * as api from './api.js';
import * as ui from './ui.js';
import { i18n, fieldMap, urls } from './config.js';
import { initGamificationSubtabs, addLevelRow, collectLevelsFromEditor, collectResetMonths, loadSeasonStatus, handleManualSeasonReset } from './gamification.js';
import { showToast, fetchAPI, setButtonLoading, restoreButton as restoreButtonState, escapeHTML, copyToClipboard } from '../utils.js';

let pinCheckInterval = null;
let authWindow = null;
export let settingsData = { plex_url: null, plex_token: null };

// --- HELPERS (Auxiliares Visuais) ---
const getSpinner = (classes = "w-4 h-4 mr-2") => `<svg class="animate-spin inline ${classes}" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>`;




async function handleTestGates2b() {
    const btn = document.getElementById('testGates2b');
    const resultado = document.getElementById('gates2b-test-result');
    const campo = document.getElementById('GATES2B_AUTH_TOKEN');

    setButtonLoading(btn, i18n.testing || 'Testando...');
    if (resultado) resultado.textContent = '';

    try {
        // Envia o valor ATUAL do campo, para o administrador poder validar a chave
        // antes de gravar. Se não tiver sido alterado (vem mascarado), o servidor
        // usa a chave já guardada.
        const result = await api.testGates2b({ auth_token: campo ? campo.value : '' });
        showToast(result.message, result.success ? 'success' : 'error');
        if (resultado) {
            resultado.textContent = result.message;
            resultado.className = `text-xs ${result.success ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'}`;
        }
    } catch (error) {
        const msg = error.message || i18n.unknownError;
        showToast(msg, 'error');
        if (resultado) {
            resultado.textContent = msg;
            resultado.className = 'text-xs text-red-600 dark:text-red-400';
        }
    } finally {
        restoreButtonState(btn);
    }
}


async function handleSyncProfiles() {
    const btn = document.getElementById('syncProfilesButton');
    const resultado = document.getElementById('sync-profiles-result');
    setButtonLoading(btn, i18n.importing || 'A importar...');
    if (resultado) resultado.textContent = '';
    try {
        const r = await api.syncProfiles({});
        showToast(r.message, r.success ? 'success' : 'error');
        if (resultado) {
            resultado.textContent = r.message;
            resultado.className = `text-xs ${r.success ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'}`;
        }
    } catch (error) {
        const msg = error.message || i18n.unknownError;
        showToast(msg, 'error');
        if (resultado) { resultado.textContent = msg; resultado.className = 'text-xs text-red-600 dark:text-red-400'; }
    } finally {
        restoreButtonState(btn);
    }
}


/**
 * Sub-abas internas das mensagens (Assinatura / Pedidos).
 *
 * Usa delegação num único listener no documento em vez de um listener por
 * botão: os painéis das notificações são construídos pelo servidor, mas assim
 * a lógica continua a funcionar se algum bloco vier a ser gerado por JS.
 */
function initMessageSubtabs() {
    document.addEventListener('click', (e) => {
        const btn = e.target.closest('.msg-subtab-button');
        if (!btn) return;

        const nav = btn.closest('[data-msg-group]');
        const grupo = nav?.dataset.msgGroup;
        const alvo = btn.dataset.msgTab;
        if (!grupo || !alvo) return;

        // Só mexe nos botões e painéis DESTE canal — cada canal tem o seu par.
        nav.querySelectorAll('.msg-subtab-button').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');

        document.querySelectorAll(`.msg-subtab-panel[data-msg-group="${grupo}"]`).forEach(panel => {
            panel.classList.toggle('hidden', panel.dataset.msgPanel !== alvo);
        });
    });
}

// --- WHATSAPP ---

/**
 * O campo de payload JSON personalizado só faz sentido no provedor 'custom':
 * nos restantes o formato do corpo é fixo e definido pela própria API.
 */
function syncWhatsappProviderFields() {
    const provider = document.getElementById('WHATSAPP_PROVIDER')?.value;
    const wrapper = document.getElementById('whatsapp-custom-payload-wrapper');
    if (wrapper) wrapper.classList.toggle('hidden', provider !== 'custom');
}

async function handleTestWhatsapp() {
    const btn = document.getElementById('testWhatsapp');
    const phone = document.getElementById('whatsapp-test-phone')?.value.trim();

    setButtonLoading(btn, i18n.testing || 'Testando...');
    try {
        // Envia os valores ATUAIS do formulário, para o administrador poder testar
        // antes de gravar — evita o ciclo "gravar, testar, corrigir, gravar".
        const result = await api.testWhatsapp({
            phone: phone || null,
            provider: document.getElementById('WHATSAPP_PROVIDER')?.value,
        });
        showToast(result.message, result.success ? 'success' : 'error');
    } catch (error) {
        showToast(error.message || i18n.unknownError, 'error');
    } finally {
        restoreButtonState(btn);
    }
}

// --- CHAVE DE API (INTEGRAÇÕES / BOTS) ---

// Guardada apenas em memória enquanto a página está aberta. A chave NÃO vem no
// payload geral de configurações (é removida no servidor por segurança), por isso
// é pedida numa rota própria só quando o administrador precisa dela.
let cachedApiKey = null;
const API_KEY_MASK = '••••••••••••••••••••••••';

async function fetchApiKey() {
    if (cachedApiKey) return cachedApiKey;
    const result = await fetchAPI(urls.apiKey);
    cachedApiKey = result.api_key;
    return cachedApiKey;
}

async function handleToggleApiKey() {
    const field = document.getElementById('api-key-field');
    if (!field) return;

    // Alterna entre a máscara e o valor real, buscando-o só na primeira vez.
    if (field.type === 'password') {
        try {
            field.value = await fetchApiKey();
            field.type = 'text';
        } catch (error) {
            showToast(`${i18n.errorGeneric || 'Erro'}: ${error.message}`, 'error');
        }
    } else {
        field.type = 'password';
        field.value = API_KEY_MASK;
    }
}

async function handleCopyApiKey() {
    try {
        const key = await fetchApiKey();
        const ok = await copyToClipboard(key);
        showToast(ok ? (i18n.apiKeyCopied || 'Chave copiada!') : (i18n.errorGeneric || 'Erro'), ok ? 'success' : 'error');
    } catch (error) {
        showToast(`${i18n.errorGeneric || 'Erro'}: ${error.message}`, 'error');
    }
}

async function handleRegenerateApiKey() {
    // ⚠️ Ação destrutiva para integrações: pede confirmação explícita.
    if (!confirm(i18n.confirmRegenerateKey || 'Gerar uma nova chave invalida a atual. Continuar?')) return;

    const btn = document.getElementById('regenerate-api-key');
    setButtonLoading(btn, i18n.generating || 'A gerar...');

    try {
        const result = await fetchAPI(urls.apiKeyRegenerate, 'POST');
        cachedApiKey = result.api_key;

        // Mostra logo a chave nova: o administrador precisa dela imediatamente
        // para atualizar os bots que acabaram de deixar de funcionar.
        const field = document.getElementById('api-key-field');
        if (field) {
            field.value = result.api_key;
            field.type = 'text';
        }
        showToast(result.message || 'OK', 'success');
    } catch (error) {
        showToast(`${i18n.errorGeneric || 'Erro'}: ${error.message}`, 'error');
    } finally {
        restoreButtonState(btn);
    }
}

// --- HANDLERS DE BACKUP E RESTAURO ---

function formatBytes(bytes) {
    if (!bytes) return '0 KB';
    const kb = bytes / 1024;
    if (kb < 1024) return `${kb.toFixed(1)} KB`;
    return `${(kb / 1024).toFixed(2)} MB`;
}

function formatBackupDate(isoString) {
    try {
        return new Date(isoString).toLocaleString();
    } catch {
        return isoString;
    }
}

async function loadBackupList() {
    const container = document.getElementById('backupList');
    if (!container) return;

    try {
        const result = await api.backupList();
        const backups = result.backups || [];

        if (backups.length === 0) {
            container.innerHTML = `<p class="text-xs text-gray-400 dark:text-gray-500">${i18n.noBackupsYet || 'Nenhum backup automático ainda. Ative o backup automático acima, ou use "Baixar Backup Agora" para gerar um agora mesmo.'}</p>`;
            return;
        }

        container.innerHTML = backups.map(b => `
            <div class="flex items-center justify-between gap-3 bg-white dark:bg-gray-900/50 border border-gray-200 dark:border-gray-700/50 rounded-lg px-3 py-2">
                <div class="min-w-0">
                    <p class="text-sm font-medium text-gray-800 dark:text-gray-200 truncate">${escapeHTML(b.filename)}</p>
                    <p class="text-xs text-gray-400 dark:text-gray-500">${formatBackupDate(b.created_at)} · ${formatBytes(b.size_bytes)}</p>
                </div>
                <div class="flex items-center gap-1 flex-shrink-0">
                    <a href="${urls.backupDownloadStored.replace('__FILENAME__', encodeURIComponent(b.filename))}" title="${i18n.download || 'Baixar'}" class="p-2 rounded-full text-gray-500 hover:bg-emerald-100 dark:hover:bg-emerald-500/20 dark:text-emerald-400 transition-colors">
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"></path></svg>
                    </a>
                    <button type="button" data-filename="${escapeHTML(b.filename)}" class="backup-delete-btn p-2 rounded-full text-gray-500 hover:bg-red-100 dark:hover:bg-red-500/20 dark:text-red-400 transition-colors" title="${i18n.delete || 'Apagar'}">
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"></path></svg>
                    </button>
                </div>
            </div>
        `).join('');

        container.querySelectorAll('.backup-delete-btn').forEach(btn => {
            btn.addEventListener('click', () => handleDeleteBackup(btn.dataset.filename));
        });
    } catch (error) {
        // A mensagem de erro pode vir do servidor: escapamos antes de a inserir como HTML.
        container.innerHTML = `<p class="text-xs text-red-500">${i18n.errorLoadingBackups || 'Falha ao carregar a lista de backups.'}: ${escapeHTML(error.message)}</p>`;
    }
}

async function handleDeleteBackup(filename) {
    if (!confirm(`${i18n.confirmDeleteBackup || 'Apagar este backup permanentemente?'}\n\n${filename}`)) return;
    try {
        await api.backupDelete(filename);
        showToast(i18n.backupDeleted || 'Backup removido.', 'success');
        loadBackupList();
    } catch (error) {
        showToast(`${i18n.errorGeneric || 'Erro'}: ${error.message}`, 'error');
    }
}

function handleBackupDownloadNow(button) {
    if (!urls.backupDownloadNow) return;
    setButtonLoading(button, i18n.generatingBackup || 'A gerar backup...');

    // Navega diretamente para a rota GET: o navegador trata o download nativamente
    // (o servidor responde com Content-Disposition: attachment). Não usamos fetchAPI
    // aqui porque a resposta é um ficheiro binário (ZIP), não JSON.
    window.location.href = urls.backupDownloadNow;

    // Não há callback de conclusão de download via navegação direta, então
    // apenas devolvemos o botão ao normal após um curto período.
    setTimeout(() => {
        restoreButtonState(button);
        loadBackupList();
    }, 2500);
}

async function handleBackupRestore(file) {
    if (!file) return;

    // ⚠️ Ação destrutiva e irreversível: dupla confirmação antes de prosseguir.
    const firstConfirm = confirm(
        (i18n.confirmRestoreStep1 || 'ATENÇÃO: Isto vai SUBSTITUIR o config.json e as bases de dados atuais pelos dados do ficheiro de backup selecionado.\n\nTodos os dados criados depois desse backup (novos utilizadores, pagamentos, etc.) serão PERDIDOS.\n\nA aplicação será reiniciada automaticamente em seguida.\n\nDeseja continuar?')
    );
    if (!firstConfirm) return;

    const confirmationWord = (i18n.restoreConfirmationWord || 'RESTAURAR').toUpperCase();
    const typed = prompt(
        (i18n.confirmRestoreStep2 || `Para confirmar esta ação irreversível, digite "${confirmationWord}" (sem aspas):`)
    );
    if ((typed || '').trim().toUpperCase() !== confirmationWord) {
        showToast(i18n.restoreCancelled || 'Restauro cancelado.', 'info');
        return;
    }

    const restoreBtn = document.getElementById('backupRestoreButton');
    setButtonLoading(restoreBtn, i18n.restoring || 'A restaurar...');

    try {
        const formData = new FormData();
        formData.append('file', file);

        // Upload multipart: não pode passar por fetchAPI (que só envia JSON).
        const response = await fetch(urls.backupRestore, { method: 'POST', body: formData });
        const data = await response.json();

        if (!response.ok || !data.success) {
            throw new Error(data.message || `HTTP ${response.status}`);
        }

        showToast(data.message || (i18n.restoreSuccess || 'Backup restaurado! A aplicação vai reiniciar...'), 'success');

        // A aplicação reinicia sozinha no servidor (SIGTERM controlado). Damos um
        // tempo generoso e recarregamos a página para o admin ver o app já de volta.
        setTimeout(() => window.location.reload(), 8000);
    } catch (error) {
        showToast(`${i18n.restoreFailed || 'Falha ao restaurar'}: ${error.message}`, 'error');
        restoreButtonState(restoreBtn);
    }
}

async function handleSaveSettings(e) {
    e.preventDefault();
    if (!dom.saveButton) return;

    const originalText = dom.saveButton.textContent;
    dom.saveButton.disabled = true;
    dom.saveButton.innerHTML = `${getSpinner()} ${i18n.saving || 'A guardar...'}`;

    const newConfig = {};
    const screenPrices = {};

    for (const [id, field] of Object.entries(fieldMap)) {
        const el = document.getElementById(id);
        if (el) {
            if (field.type === 'price') {
                const priceValue = parseFloat(el.value.replace(',', '.'));
                if (!isNaN(priceValue) && priceValue > 0) {
                    screenPrices[field.key] = priceValue.toFixed(2);
                }
            } else if (!field.readonly) {
                const key = field.key || id;
                if (key.includes('_BULK_MESSAGE_TEMPLATE')) continue;

                if (field.type === 'checkbox') {
                    newConfig[key] = el.checked;
                } else if (field.type === 'number') {
                    newConfig[key] = parseInt(el.value, 10) || 0;
                } else if (field.type === 'decimal') {
                    // 🐛 CORREÇÃO: campos decimais (ex: XP por minuto = 0.1) não podem
                    // passar por parseInt, que trunca qualquer casa decimal para 0.
                    newConfig[key] = parseFloat(el.value) || 0;
                } else if (field.type === 'password') {
                    const originalLength = parseInt(el.dataset.originalLength || '0', 10);
                    // O backend envia '*' correspondente ao tamanho da senha. 
                    // Se o usuário não alterou, não enviamos de volta para evitar sobrescrever a verdadeira senha.
                    const isPlaceholder = el.value === '*'.repeat(originalLength);
                    if (!isPlaceholder) {
                        newConfig[key] = el.value;
                    }
                } else {
                    newConfig[key] = el.value;
                }
            }
        }
    }
    newConfig.SCREEN_PRICES = screenPrices;
    newConfig.XP_LEVEL_TABLE = collectLevelsFromEditor();
    newConfig.XP_RESET_MONTHS = collectResetMonths();
    if (dom.logLevelSelector) newConfig.LOG_LEVEL = dom.logLevelSelector.value;

    if (settingsData.plex_url && settingsData.plex_token) {
        newConfig.plex_url = settingsData.plex_url;
        newConfig.plex_token = settingsData.plex_token;
    }

    try {
        const result = await api.saveSettings(newConfig);
        showToast(result.message, result.success ? 'success' : 'error');
        if (result.success) ui.loadSettings();
    } catch (error) {
        showToast(error.message || i18n.unknownError || 'Erro desconhecido.', 'error');
    } finally {
        dom.saveButton.disabled = false;
        dom.saveButton.textContent = originalText;
    }
}

async function handleSaveBulkTemplates() {
    if (!dom.saveBulkTemplatesButton) return;
    
    const originalText = dom.saveBulkTemplatesButton.textContent;
    dom.saveBulkTemplatesButton.disabled = true;
    dom.saveBulkTemplatesButton.innerHTML = `${getSpinner()} ${i18n.savingTemplates || 'A gravar...'}`;

    const templateData = {
        'TELEGRAM_BULK_MESSAGE_TEMPLATE': document.getElementById('TELEGRAM_BULK_MESSAGE_TEMPLATE')?.value || '',
        'DISCORD_BULK_MESSAGE_TEMPLATE': document.getElementById('DISCORD_BULK_MESSAGE_TEMPLATE')?.value || '',
        'WEBHOOK_BULK_MESSAGE_TEMPLATE': document.getElementById('WEBHOOK_BULK_MESSAGE_TEMPLATE')?.value || '',
        // 🐛 O template de WhatsApp estava EM FALTA aqui. Como a gravação geral
        // também salta tudo o que contém '_BULK_MESSAGE_TEMPLATE', o campo era
        // apresentado, aceitava edição... e nunca era gravado por caminho nenhum.
        'WHATSAPP_BULK_MESSAGE_TEMPLATE': document.getElementById('WHATSAPP_BULK_MESSAGE_TEMPLATE')?.value || '',
    };

    try {
        const result = await api.saveSettings(templateData);
        showToast(result.message, result.success ? 'success' : 'error');
    } catch (error) {
        showToast(error.message || i18n.unknownError, 'error');
    } finally {
        dom.saveBulkTemplatesButton.disabled = false;
        dom.saveBulkTemplatesButton.textContent = originalText;
    }
}

async function handleTestConnection(button, endpoint, payloadBuilder) {
    if (!button) return;

    setButtonLoading(button, i18n.testing || 'Testando...');

    try {
        const apiFunction = api[endpoint];
        if (typeof apiFunction !== 'function') {
            throw new Error(`Função da API desconhecida: ${endpoint}`);
        }
        const result = await apiFunction(payloadBuilder());
        showToast(result.message, result.success ? 'success' : 'error');
    } catch (error) {
        showToast(error.message || i18n.unknownError, 'error');
    } finally {
        restoreButtonState(button);
    }
}

async function handlePlexAuth() {
    const restoreButton = () => restoreButtonState(dom.reauthPlexButton);

    setButtonLoading(dom.reauthPlexButton, i18n.verifying || 'A aguardar autenticação...');

    if (pinCheckInterval) clearInterval(pinCheckInterval);

    try {
        const contextData = await api.getPlexAuthContext();
        const { product_name, client_id } = contextData;
        const plexHeaders = { 'X-Plex-Product': product_name, 'X-Plex-Client-Identifier': client_id, 'Accept': 'application/json' };
        
        const plexResponse = await fetch("https://plex.tv/api/v2/pins?strong=true", { method: 'POST', headers: plexHeaders });
        if (!plexResponse.ok) throw new Error('Falha ao criar PIN de autenticação com a Plex.tv.');
        
        const pinData = await plexResponse.json();
        const { id: pin_id, code: pin_code } = pinData;

        const authUrlParams = new URLSearchParams({ 
            'clientID': client_id, 
            'code': pin_code, 
            'context[device][product]': product_name, 
            'context[device][deviceName]': product_name, 
            'context[device][platform]': 'Web' 
        });
        const auth_url = `https://app.plex.tv/auth#?${authUrlParams.toString()}`;
        
        // Abre o popup centralizado
        const width = 800;
        const height = 700;
        const left = (window.innerWidth / 2) - (width / 2);
        const top = (window.innerHeight / 2) - (height / 2);
        authWindow = window.open(auth_url, 'plexAuth', `width=${width},height=${height},top=${top},left=${left}`);

        // Segurança: Limite de tempo de 5 minutos (100 verificações a cada 3s)
        let attempts = 0;
        const maxAttempts = 100;

        pinCheckInterval = setInterval(async () => {
            attempts++;
            
            // Cancela se o utilizador fechar a janela manualmente ou se o tempo expirar
            if (!authWindow || authWindow.closed || attempts > maxAttempts) {
                clearInterval(pinCheckInterval);
                if (attempts > maxAttempts) {
                    showToast('Tempo limite para autenticação excedido.', 'warning');
                    if (authWindow && !authWindow.closed) authWindow.close();
                }
                restoreButton();
                return;
            }
            
            try {
                const checkData = await api.checkPlexPin(client_id, pin_id);
                if (checkData.success) {
                    clearInterval(pinCheckInterval);
                    if (authWindow && !authWindow.closed) authWindow.close();
                    
                    showToast(i18n.authenticated || 'Autenticado com sucesso!', 'success');
                    await ui.fetchAndDisplayPlexServers();
                } else if (checkData.message === 'auth_denied') {
                    clearInterval(pinCheckInterval);
                    if (authWindow && !authWindow.closed) authWindow.close();
                    
                    showToast(checkData.error || 'Autenticação negada pelo utilizador.', 'error');
                    restoreButton();
                }
            } catch (e) {
                // Em caso de falha de rede temporária não matamos o interval, apenas logamos
                console.warn(`Verificação do Plex Pin falhou na tentativa ${attempts}: ${e.message}`);
            }
        }, 3000);
        
    } catch (error) {
        showToast(error.message, 'error');
        restoreButton();
    }
}

// --- INICIALIZAÇÃO DE LISTENERS ---

export function initializeEventListeners() {
    if (dom.form) dom.form.addEventListener('submit', handleSaveSettings);
    
    if (dom.saveBulkTemplatesButton) {
        dom.saveBulkTemplatesButton.addEventListener('click', handleSaveBulkTemplates);
    }
    
    if (dom.testTautulliButton) {
        dom.testTautulliButton.addEventListener('click', () => handleTestConnection(dom.testTautulliButton, 'testTautulli', () => ({ 
            url: document.getElementById('TAUTULLI_URL')?.value, 
            api_key: document.getElementById('TAUTULLI_API_KEY')?.value 
        })));
    }
    
    if (dom.testOverseerrButton) {
        dom.testOverseerrButton.addEventListener('click', () => handleTestConnection(dom.testOverseerrButton, 'testOverseerr', () => ({ 
            url: document.getElementById('OVERSEERR_URL')?.value, 
            api_key: document.getElementById('OVERSEERR_API_KEY')?.value 
        })));
    }
    
    if (dom.reauthPlexButton) dom.reauthPlexButton.onclick = handlePlexAuth;
    if (dom.languageSelector) dom.languageSelector.addEventListener('change', (e) => window.location.href = `/language/${e.target.value}`);
    if (dom.toggleLogsButton) dom.toggleLogsButton.addEventListener('click', () => ui.toggleLogUpdates());
    if (dom.clearLogsButton) dom.clearLogsButton.addEventListener('click', ui.clearLogs);
    if (dom.efiUseMtlsCheckbox) dom.efiUseMtlsCheckbox.addEventListener('change', ui.toggleHmacSection);
    
    if (dom.generateHmacButton) {
        dom.generateHmacButton.addEventListener('click', () => {
            // Gera um HASH hexadecimal seguro aleatório
            const randomString = Array.from(crypto.getRandomValues(new Uint8Array(20))).map(b => b.toString(16).padStart(2, '0')).join('');
            if(dom.hmacInput) {
                dom.hmacInput.value = randomString;
                // Animação de feedback para mostrar que gerou
                dom.hmacInput.classList.add('ring-2', 'ring-green-500', 'bg-green-50', 'dark:bg-green-900/30');
                setTimeout(() => dom.hmacInput.classList.remove('ring-2', 'ring-green-500', 'bg-green-50', 'dark:bg-green-900/30'), 500);
            }
        });
    }

    if (dom.mainTabsSelect) {
        dom.mainTabsSelect.addEventListener('change', ui.handleTabsSelectChange);
    }

    // --- Backup e Restauro ---
    const backupDownloadNowButton = document.getElementById('backupDownloadNowButton');
    if (backupDownloadNowButton) {
        backupDownloadNowButton.addEventListener('click', () => handleBackupDownloadNow(backupDownloadNowButton));
    }

    const backupRestoreButton = document.getElementById('backupRestoreButton');
    const backupRestoreFileInput = document.getElementById('backupRestoreFileInput');
    if (backupRestoreButton && backupRestoreFileInput) {
        backupRestoreButton.addEventListener('click', () => backupRestoreFileInput.click());
        backupRestoreFileInput.addEventListener('change', (e) => {
            const file = e.target.files && e.target.files[0];
            handleBackupRestore(file);
            e.target.value = ''; // permite selecionar o mesmo ficheiro de novo, se necessário
        });
    }

    if (document.getElementById('backupList')) {
        loadBackupList();
    }

    document.getElementById('testGates2b')?.addEventListener('click', handleTestGates2b);
    document.getElementById('syncProfilesButton')?.addEventListener('click', handleSyncProfiles);

    // Sub-abas Assinatura / Pedidos dentro de cada canal de notificação
    initMessageSubtabs();

    // --- WhatsApp ---
    document.getElementById('testWhatsapp')?.addEventListener('click', handleTestWhatsapp);
    const waProvider = document.getElementById('WHATSAPP_PROVIDER');
    if (waProvider) {
        waProvider.addEventListener('change', syncWhatsappProviderFields);
        syncWhatsappProviderFields();
    }

    // --- Chave de API ---
    document.getElementById('toggle-api-key')?.addEventListener('click', handleToggleApiKey);
    document.getElementById('copy-api-key')?.addEventListener('click', handleCopyApiKey);
    document.getElementById('regenerate-api-key')?.addEventListener('click', handleRegenerateApiKey);

    // --- Gamificação: sub-abas, editor de níveis e temporadas de XP ---
    initGamificationSubtabs();
    document.getElementById('xp-level-add-btn')?.addEventListener('click', addLevelRow);

    const seasonResetBtn = document.getElementById('xp-season-reset-btn');
    if (seasonResetBtn) {
        seasonResetBtn.addEventListener('click', () => handleManualSeasonReset(urls, fetchAPI, showToast));
        loadSeasonStatus(urls, fetchAPI);
    }

    document.querySelectorAll('.show-help-button').forEach(button => button.addEventListener('click', () => dom.helpModal?.classList.remove('hidden')));
    if (dom.closeHelpModalButton) dom.closeHelpModalButton.addEventListener('click', () => dom.helpModal?.classList.add('hidden'));

    // Configuração da navegação por abas delegada à UI
    ui.setupTabNavigation(dom.mainTabs, dom.mainTabContent, '.tab-content');
    ui.setupTabNavigation(dom.paymentTabs, dom.paymentTabContent, '.sub-tab-content');
    ui.setupTabNavigation(dom.notificationTabs, dom.notificationTabContent, '.sub-tab-content');
    ui.setupTabNavigation(dom.comunicacoesTabs, dom.comunicacoesTabContent, '.sub-tab-content');
}
