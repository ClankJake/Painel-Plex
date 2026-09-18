/**
 * invite.js
 * Lógica para a página de resgate de convites.
 */

import { setButtonLoading, restoreButton, escapeHTML, buildPinCheckUrl, lerConfiguracaoDoScript,
         soDigitos, paisesComOPadrao, juntarTelefone } from './utils.js';

// --- INICIALIZAÇÃO ---
const scriptTag = document.getElementById('invite-script');
const inviteCode = scriptTag.dataset.inviteCode;

// Idioma ativo da aplicação. Antes as datas estavam fixas em 'pt-BR', o que fazia
// um utilizador com o painel em inglês ver datas em formato brasileiro.
const LOCALE = scriptTag.dataset.locale || navigator.language || 'pt-BR';

// Tempo máximo à espera da autenticação no Plex antes de desistir (5 minutos).
// Sem isto, o polling continuava indefinidamente enquanto a janela estivesse aberta.
const AUTH_TIMEOUT_MS = 5 * 60 * 1000;

/**
 * 🔒 Sanitiza um URL antes de o usar num atributo href.
 * Impede esquemas executáveis como 'javascript:' — relevante porque o URL do
 * Overseerr é configurado pelo administrador e chega aqui vindo da API.
 */
function safeUrl(url) {
    if (!url) return null;
    try {
        const parsed = new URL(url, window.location.origin);
        return ['http:', 'https:'].includes(parsed.protocol) ? parsed.href : null;
    } catch {
        return null;
    }
}

function formatDate(date) {
    return date.toLocaleDateString(LOCALE, { day: '2-digit', month: '2-digit', year: 'numeric' });
}

function formatTime(date) {
    return date.toLocaleTimeString(LOCALE, { hour: '2-digit', minute: '2-digit' });
}

// Mapeia os data-attributes para objetos para facilitar o acesso
// 🐛 A conversão das chaves TEM de comer os traços que o browser deixou: ele só
// os remove quando vêm seguidos de uma letra minúscula, e `data-i18n-step-local-1`
// chega ao dataset como `i18nStepLocal-1`. Sem isto, a chave ficava
// `stepLocal-1`, o consumidor pedia `stepLocal1` e a página escrevia
// "undefined" no "Como começar". Quem trata disso é o `chaveEmCamelCase`, dentro
// do `lerConfiguracaoDoScript`.
const { urls, i18n, config } = lerConfiguracaoDoScript('invite-script');


/**
 * Um texto do dicionário, nunca `undefined`.
 *
 * Uma chave em falta é um erro de programação — mas escrevê-lo na cara de quem
 * está a resgatar um convite é pior do que mostrar a frase incompleta.
 */
function texto(chave, alternativa = '') {
    return i18n[chave] || alternativa;
}

let pinCheckInterval = null;
let authWindow = null;
const mainContainer = document.getElementById('main-container');

// --- FUNÇÕES DE UI ---

function showMessage(title, message, isError = false) {
    const titleColor = isError ? 'text-red-500' : 'text-green-500';
    const icon = isError 
        ? `<svg class="w-16 h-16 text-red-400 mx-auto" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" /></svg>`
        : `<svg class="w-16 h-16 text-yellow-400 mx-auto" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor"><path d="M12.0001 1.5C11.3001 1.5 10.7301 2.01 10.6501 2.71L9.50006 12.35L4.08006 15.2C3.36006 15.65 3.11006 16.59 3.56006 17.31C3.88006 17.84 4.48006 18.15 5.12006 18.15H6.28006L8.47006 22.29C8.91006 23.12 9.87006 23.57 10.7601 23.36C11.6501 23.15 12.3001 22.35 12.3001 21.42V14.88L17.5301 17.9C18.1501 18.25 18.8901 18.06 19.3501 17.48L21.8201 13.94C22.2801 13.36 22.1801 12.55 21.6501 12.01L15.2701 5.68C14.7301 5.14 13.8801 5.21 13.4301 5.76L12.3001 7.15V2.85C12.3001 2.1 11.7001 1.5 12.0001 1.5Z"/></svg>`;

    // 🔒 'title' e 'message' vêm de respostas da API (podem conter nomes de
    // utilizador ou dados externos), por isso são escapados antes de entrar no HTML.
    mainContainer.innerHTML = `
        ${icon}
        <h1 class="text-3xl font-bold ${titleColor} mt-4">${escapeHTML(title)}</h1>
        <p class="mt-2 text-lg text-gray-600 dark:text-gray-300">${escapeHTML(message)}</p>
    `;
}

function createAppCard(title, href, svgPath) {
    return `
        <a href="${escapeHTML(href)}" target="_blank" rel="noopener noreferrer" class="block bg-gray-100 dark:bg-gray-700/50 p-4 rounded-lg hover:bg-yellow-100 dark:hover:bg-yellow-500/20 hover:scale-105 transition-all duration-200">
            <svg class="w-10 h-10 mx-auto text-gray-700 dark:text-gray-300" fill="currentColor" viewBox="0 0 24 24">${svgPath}</svg>
            <p class="mt-2 text-sm font-semibold text-gray-800 dark:text-gray-200">${escapeHTML(title)}</p>
        </a>
    `;
}

function showImprovedOnboarding(userData) {
    const desktopIcon = `<path d="M21 13H3a1 1 0 01-1-1V4a1 1 0 011-1h18a1 1 0 011 1v8a1 1 0 01-1 1zm-1-2V5H4v6h16z"></path><path d="M12 15H3.21a1 1 0 00-.97 1.24l1.39 4A1 1 0 004.59 21h14.82a1 1 0 00.97-.76l1.39-4A1 1 0 0020.79 15H12z"></path>`;
    const mobileIcon = `<path d="M17 2H7a3 3 0 00-3 3v14a3 3 0 003 3h10a3 3 0 003-3V5a3 3 0 00-3-3zm-1 16H8a1 1 0 010-2h8a1 1 0 010 2zm1-4H6V6a1 1 0 011-1h10a1 1 0 011 1v8z"></path>`;
    const tvIcon = `<path d="M21 16H3a1 1 0 010-2h18a1 1 0 010 2zM20 3H4a3 3 0 00-3 3v6a3 3 0 003 3h16a3 3 0 003-3V6a3 3 0 00-3-3zm1 9a1 1 0 01-1 1H4a1 1 0 01-1-1V6a1 1 0 011-1h16a1 1 0 011 1v6z"></path>`;
    
    let expirationHtml = '';
    const isTrial = userData.is_trial;

    if (userData.expiration_date) {
        const date = new Date(userData.expiration_date);
        const formattedDate = formatDate(date);
        const formattedTime = formatTime(date);

        if (isTrial) {
            expirationHtml = `
            <div class="mt-4 p-3 bg-yellow-100 dark:bg-yellow-500/20 border-l-4 border-yellow-500 text-yellow-700 dark:text-yellow-200 text-sm text-left rounded-r-lg">
                <p><strong>${i18n.attention}</strong> ${i18n.welcomeUserTrial.replace('{date}', `<strong>${formattedDate}</strong>`).replace('{time}', `<strong>${formattedTime}</strong>`)}</p>
            </div>`;
        } else {
            expirationHtml = `<p class="mt-1 text-sm text-gray-500 dark:text-gray-400">${i18n.accessValidUntil.replace('{date}', `<strong>${formattedDate}</strong>`)}</p>`;
        }
    }

    let overseerrStep = '';
    // 🔒 O URL do Overseerr é configurado pelo administrador: validamos o esquema
    // antes de o colocar num href, para nunca aceitar 'javascript:' e afins.
    const overseerrHref = userData.overseerr_access ? safeUrl(userData.overseerr_url) : null;
    if (overseerrHref) {
        const overseerrLink = `<a href="${escapeHTML(overseerrHref)}" target="_blank" rel="noopener noreferrer" class="text-yellow-500 hover:underline font-semibold">${i18n.step3LinkOverseerr}</a>`;
        overseerrStep = `<li>${i18n.step3Overseerr.replace('{link}', overseerrLink)}</li>`;
    }
    
    let paymentButtonHtml = '';
    if(isTrial && userData.payment_token) {
        // Usa o URL gerado pelo servidor (url_for) em vez de o construir à mão:
        // um caminho fixo '/pay/...' quebra se a aplicação correr sob um prefixo.
        const paymentUrl = (urls.payment || '/pay/__TOKEN__').replace('__TOKEN__', encodeURIComponent(userData.payment_token));
        paymentButtonHtml = `
            <div class="mt-8">
                 <a href="${escapeHTML(paymentUrl)}" class="inline-flex items-center justify-center px-4 py-3 rounded-lg font-bold transition-transform duration-200 ease-in-out border border-transparent bg-green-600 text-white hover:bg-green-500 hover:-translate-y-0.5">${i18n.renewNow}</a>
            </div>
        `;
    }

    // 🔒 O nome de utilizador vem da conta Plex (dado externo). Escapamos antes de
    // o interpolar — este era o ponto de XSS mais exposto de toda a página.
    const welcomeTitle = i18n.welcomeUser.replace('{username}', `<strong>${escapeHTML(userData.username)}</strong>`);

    // Num servidor de contas locais, o nome que a pessoa escolheu É uma
    // credencial — e é o que ela vai escrever na aplicação daqui a cinco
    // minutos. Repeti-lo aqui poupa-lhe a dúvida.
    const credenciaisHtml = criaContas() ? `
        <div class="mt-4 p-3 bg-purple-50 dark:bg-purple-900/20 border-l-4 border-purple-500 text-purple-800 dark:text-purple-200 text-sm text-left rounded-r-lg">
            <p>${i18n.yourUsernameIs.replace('{username}', `<strong>${escapeHTML(userData.username || '')}</strong>`)}</p>
        </div>` : '';

    mainContainer.innerHTML = `
        <svg class="w-16 h-16 text-green-500 mx-auto" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
        <h1 class="text-3xl font-bold text-green-500 mt-4">${i18n.success}</h1>
        <div class="mt-2 text-lg text-gray-600 dark:text-gray-300">${welcomeTitle}</div>
        ${credenciaisHtml}
        ${expirationHtml}
        ${paymentButtonHtml}

        <div class="mt-8 text-left border-t border-gray-200 dark:border-gray-700/50 pt-6">
            <h2 class="text-2xl font-bold text-gray-900 dark:text-white mb-4 text-center">${i18n.nextSteps}</h2>
             <ol class="list-decimal list-inside space-y-2 text-sm text-gray-600 dark:text-gray-400 step-list">
                <li>${criaContas() ? i18n.step1OnboardingLocal : i18n.step1Onboarding}</li>
                ${overseerrStep}
            </ol>
        </div>

        ${secaoDeContatos(userData)}

        <div class="mt-8 text-left border-t border-gray-200 dark:border-gray-700/50 pt-6">
            <h2 class="text-2xl font-bold text-gray-900 dark:text-white mb-4 text-center">${i18n.enjoyAnywhere}</h2>
            <div class="grid grid-cols-2 sm:grid-cols-3 gap-4 text-center">
                ${cartoesDeAplicacoes(desktopIcon, mobileIcon, tvIcon, userData.server_url)}
            </div>
            <div class="text-center mt-6">
                <a href="${criaContas() ? 'https://jellyfin.org/downloads/clients' : 'https://www.plex.tv/pt-br/apps-devices/'}" target="_blank" rel="noopener noreferrer" class="text-sm text-yellow-500 hover:underline">${i18n.allDevices} &rarr;</a>
            </div>
        </div>
    `;

    ligarSecaoDeContatos(userData);
}

// ==========================================
// OS CONTATOS, NO FIM DO RESGATE
// ==========================================
//
// 🔔 Quem entra por um link PÚBLICO fica sem contato nenhum: o painel só resolve
// os que um bot pré-atribuiu ao convite. Sem contato, `_prepare_and_send` não
// tem por onde tentar e TODAS as notificações morrem em silêncio — e não é só o
// fim do teste: é o lembrete de vencimento (diário), a renovação, a reativação,
// a redefinição de senha, as credenciais de uma conta recriada e o aviso em
// massa. O link de pagamento viaja dentro deles.
//
// ⚠️ Por isso aparece em TODO resgate, e não só nos de teste: `trial_duration_minutes`
// é 0 por padrão, e num convite normal não há teste nenhum — mas é justamente
// essa pessoa que vai ter vencimento e link de pagamento depois. O que muda com
// `is_trial` é só a frase.

const CAMPOS_POR_CANAL = {
    whatsapp: 'contact-whatsapp',
    telegram: 'contact-telegram',
    discord: 'contact-discord',
};

function secaoDeContatos(userData) {
    const canais = userData.contact_channels || [];
    if (!canais.length) return '';

    const ddiPadrao = soDigitos(config.defaultCountryCode) || '55';
    const paises = paisesComOPadrao(ddiPadrao);

    const explicacao = userData.is_trial
        ? (i18n.contactsWhyTrial || 'Para avisarmos você quando o período de teste terminar.')
        : (i18n.contactsWhy || 'Para avisarmos você do vencimento e enviarmos o link de pagamento.');

    const campo = 'w-full p-2.5 text-sm rounded-lg border bg-gray-50 border-gray-300 text-gray-900 dark:bg-gray-700 dark:border-gray-600 dark:text-white';
    const rotulo = 'block text-sm font-semibold text-gray-700 dark:text-gray-300 mb-1';

    const whatsappHtml = canais.includes('whatsapp') ? `
        <div>
            <label for="contact-whatsapp" class="${rotulo}">${escapeHTML(i18n.contactsWhatsapp || 'WhatsApp')}</label>
            <div class="flex">
                <select id="contact-country" class="p-2.5 text-sm rounded-l-lg border border-r-0 bg-gray-50 border-gray-300 text-gray-900 dark:bg-gray-700 dark:border-gray-600 dark:text-white">
                    ${paises.map(p => `<option value="${p.code}">${escapeHTML(p.name)} (+${p.code})</option>`).join('')}
                </select>
                <input type="tel" id="contact-whatsapp" inputmode="tel" maxlength="20"
                       placeholder="21999998888" class="${campo} rounded-l-none">
            </div>
        </div>` : '';

    // ⚠️ O Telegram NÃO tem campo de texto, e não é esquecimento: o painel manda
    // `chat_id = telegram_id or telegram_user` direto para a API, e um @username
    // não endereça uma conversa privada — só um canal. E mesmo com o id numérico
    // certo, um bot não pode INICIAR uma conversa: enquanto a pessoa não abrir o
    // bot e tocar em Começar, qualquer envio devolve 403. O botão resolve os
    // dois de uma vez: ela toca, o Telegram abre o bot, e o `/start <código>`
    // que ele recebe traz o chat.id que o painel precisa.
    const telegramHref = canais.includes('telegram') ? safeUrl(userData.telegram_link) : null;
    const telegramHtml = telegramHref ? `
        <div>
            <span class="${rotulo}">${escapeHTML(i18n.contactsTelegram || 'Telegram')}</span>
            <div class="flex flex-wrap gap-2">
                <a href="${telegramHref}" target="_blank" rel="noopener noreferrer" id="contact-telegram-open"
                   class="btn bg-sky-600 hover:bg-sky-500 text-white text-sm px-4 py-2 rounded-lg">
                    ${escapeHTML(i18n.contactsTelegramOpen || 'Abrir o bot no Telegram')}
                </a>
                <button type="button" id="contact-telegram-check"
                        class="btn bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-sm px-4 py-2 rounded-lg">
                    ${escapeHTML(i18n.contactsTelegramCheck || 'Já toquei em Começar')}
                </button>
            </div>
            <p id="contact-telegram-status" class="text-xs text-gray-500 dark:text-gray-400 mt-1"></p>
        </div>` : '';

    const discordHtml = canais.includes('discord') ? `
        <div>
            <label for="contact-discord" class="${rotulo}">${escapeHTML(i18n.contactsDiscord || 'ID de usuário do Discord')}</label>
            <input type="text" id="contact-discord" maxlength="32" inputmode="numeric"
                   placeholder="123456789012345678" class="${campo}">
        </div>` : '';

    return `
        <div class="mt-8 text-left border-t border-gray-200 dark:border-gray-700/50 pt-6" id="contact-section">
            <h2 class="text-2xl font-bold text-gray-900 dark:text-white mb-1 text-center">${escapeHTML(i18n.contactsTitle || 'Como falamos com você?')}</h2>
            <p class="text-sm text-gray-500 dark:text-gray-400 mb-4 text-center">${escapeHTML(explicacao)}</p>
            <div class="space-y-4">
                <div>
                    <label for="contact-name" class="${rotulo}">${escapeHTML(i18n.contactsName || 'Seu nome')}</label>
                    <input type="text" id="contact-name" maxlength="80" autocomplete="name" class="${campo}">
                </div>
                ${whatsappHtml}
                ${discordHtml}
                ${telegramHtml}
            </div>
            <button type="button" id="contact-save"
                    class="btn bg-green-600 hover:bg-green-500 text-white w-full mt-4 py-2.5 rounded-lg font-semibold">
                ${escapeHTML(i18n.contactsSave || 'Salvar')}
            </button>
            <p id="contact-status" class="text-sm text-center mt-2"></p>
            <p class="text-xs text-gray-400 dark:text-gray-500 text-center mt-2">${escapeHTML(i18n.contactsLater || 'Você pode preencher isso depois, em Minha Conta.')}</p>
        </div>`;
}

function aviso(elemento, texto, erro = false) {
    if (!elemento) return;
    elemento.textContent = texto;
    elemento.className = `text-sm text-center mt-2 ${erro ? 'text-red-500' : 'text-green-600 dark:text-green-400'}`;
}

function ligarSecaoDeContatos(userData) {
    const botao = document.getElementById('contact-save');
    if (!botao) return;

    const estado = document.getElementById('contact-status');
    const ddiPadrao = soDigitos(config.defaultCountryCode) || '55';

    botao.addEventListener('click', async () => {
        const original = botao.textContent;
        botao.disabled = true;
        botao.textContent = i18n.contactsSaving || 'Salvando...';

        const corpo = { name: document.getElementById('contact-name')?.value.trim() || '' };

        const pais = document.getElementById('contact-country');
        const telefone = document.getElementById(CAMPOS_POR_CANAL.whatsapp);
        if (pais && telefone) {
            // 📌 A junção é a do `utils.js`, a mesma da "Minha Conta": um número
            // digitado já com o DDI não pode ganhar outro à frente.
            corpo.whatsapp = juntarTelefone(pais.value, telefone.value, ddiPadrao).completo;
        }

        const discord = document.getElementById(CAMPOS_POR_CANAL.discord);
        if (discord) corpo.discord = discord.value.trim();

        try {
            const resposta = await fetch(urls.claimContacts, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(corpo),
            });
            const resultado = await resposta.json();
            aviso(estado, resultado.message || '', !resultado.success);
        } catch {
            aviso(estado, i18n.contactsFail || 'Não foi possível salvar agora.', true);
        } finally {
            botao.disabled = false;
            botao.textContent = original;
        }
    });

    const verificar = document.getElementById('contact-telegram-check');
    if (!verificar) return;

    const estadoTelegram = document.getElementById('contact-telegram-status');
    verificar.addEventListener('click', async () => {
        const original = verificar.textContent;
        verificar.disabled = true;
        verificar.textContent = i18n.contactsChecking || 'Verificando...';

        try {
            const resposta = await fetch(urls.claimTelegram, { method: 'POST' });
            const resultado = await resposta.json();
            // ⚠️ `vinculado: false` num 200 é "ainda não chegou", e não um erro:
            // a pessoa pode simplesmente não ter tocado em Começar ainda.
            aviso(estadoTelegram, resultado.message || '', !resultado.success);
            estadoTelegram.classList.add('text-xs');
            if (resultado.vinculado) verificar.disabled = true;
            else verificar.textContent = original;
        } catch {
            aviso(estadoTelegram, i18n.contactsFail || 'Não foi possível salvar agora.', true);
            verificar.textContent = original;
        } finally {
            if (!verificar.disabled) verificar.textContent = original;
            verificar.disabled = false;
        }
    });
}


// --- LÓGICA DE AUTENTICAÇÃO E CONVITE ---

async function claimInvite(plexToken) {
    showMessage(i18n.processing, i18n.waitClaim);
    try {
        const response = await fetch(urls.claimInvite, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code: inviteCode, plex_token: plexToken })
        });
        const result = await response.json();
        if (result.success) {
            showImprovedOnboarding(result.user_data);
        } else {
            showMessage(i18n.error, result.message, true);
        }
    } catch (e) {
        showMessage(i18n.networkError, i18n.claimFail, true);
    }
}

function startPolling(pin_id, client_id) {
    const loginButton = document.getElementById('login-button');
    if (pinCheckInterval) clearInterval(pinCheckInterval);

    const startedAt = Date.now();

    pinCheckInterval = setInterval(async () => {
        if (!authWindow || authWindow.closed) {
            stopPolling();
            // Restaura o botão recolocando os nós originais, sem reinterpretar HTML.
            restoreButton(loginButton);
            return;
        }

        // ⏱️ Desistência por tempo: sem isto, o polling corria de 3 em 3 segundos
        // indefinidamente enquanto a janela do Plex ficasse aberta e esquecida.
        if (Date.now() - startedAt > AUTH_TIMEOUT_MS) {
            stopPolling();
            try { if (!authWindow.closed) authWindow.close(); } catch { /* janela externa */ }
            restoreButton(loginButton);
            showMessage(i18n.error, i18n.authTimeout || 'Tempo de autenticação esgotado.', true);
            return;
        }
        
        try {
            const checkUrl = buildPinCheckUrl(urls.checkPinForToken, client_id, pin_id);
            const checkResponse = await fetch(checkUrl);
            const checkData = await checkResponse.json();

            if (checkData.success && checkData.token) {
                stopPolling();
                authWindow.close();
                await claimInvite(checkData.token);
            } else if (checkData.message === 'auth_denied') {
                stopPolling();
                if(!authWindow.closed) authWindow.close();
                showMessage(i18n.error, checkData.error || i18n.authDenied, true);
            }
        } catch (e) {
            stopPolling();
            showMessage(i18n.error, i18n.authCheckError, true);
        }
    }, 3000);
}

/**
 * Para o polling e remove o listener de mensagens. Centralizado para garantir que
 * nunca fica um intervalo órfão a correr (o que acontecia se o utilizador saísse
 * da página a meio da autenticação).
 */
function stopPolling() {
    if (pinCheckInterval) {
        clearInterval(pinCheckInterval);
        pinCheckInterval = null;
    }
    window.removeEventListener('message', handleAuthMessage);
}

window.addEventListener('beforeunload', stopPolling);

function handleAuthMessage(event) {
    if (event.origin !== window.location.origin) {
        console.warn("Mensagem de origem desconhecida ignorada:", event.origin);
        return;
    }

    const { type, pin_id, client_id } = event.data;
    if (type === 'plexAuthPin' && pin_id && client_id) {
        startPolling(pin_id, client_id);
        window.removeEventListener('message', handleAuthMessage);
    }
}

function loginWithPlexToClaim() {
    const loginButton = document.getElementById('login-button');
    setButtonLoading(loginButton, i18n.waitingAuth);

    window.addEventListener('message', handleAuthMessage, false);
    authWindow = window.open(urls.redirectToAuth, 'plexAuth', 'width=800,height=700,status=no,scrollbars=yes,resizable=yes');

    // 🐛 Pop-up bloqueado: 'window.open' devolve null (frequente no Safari/iOS e
    // sempre que o navegador não associa a ação a um clique direto). Antes, o
    // botão ficava preso em "Aguardando autenticação..." PARA SEMPRE, sem
    // qualquer mensagem — o visitante concluía que a página estava avariada e
    // desistia do registo.
    if (!authWindow || authWindow.closed || typeof authWindow.closed === 'undefined') {
        window.removeEventListener('message', handleAuthMessage);
        restoreButton(loginButton);
        showMessage(i18n.error, i18n.popupBlocked || 'Não foi possível abrir a janela de login. Verifique se o seu navegador está bloqueando pop-ups.', true);
    }
}

// Num servidor de contas locais o resgate CRIA a conta: em vez de autenticar
// uma conta que já existe, pede-se as credenciais que a pessoa vai passar a
// usar. Ver `JellyfinAccountManager.claim_invitation`.
function criaContas() {
    return config.createsAccounts === 'true';
}

/**
 * 🐛 Convite JÁ EXPIRADO: só desativava o botão do Plex. Num servidor de contas
 * locais não há botão nenhum — há um FORMULÁRIO — e ele ficava a funcionar: a
 * pessoa escolhia utilizador e palavra-passe, submetia, e só então descobria
 * que o convite tinha expirado. Exatamente o que o aviso existe para evitar.
 */
function desativarResgate() {
    const botao = document.getElementById('login-button');
    if (botao) {
        botao.disabled = true;
        botao.classList.add('opacity-50', 'cursor-not-allowed');
        botao.classList.remove('hover:scale-105', 'hover:bg-yellow-600');
    }

    const formulario = document.getElementById('register-form');
    if (formulario) {
        formulario.querySelectorAll('input, button').forEach((campo) => {
            campo.disabled = true;
        });
        formulario.classList.add('opacity-50');
    }
}

function cartoesDeAplicacoes(desktopIcon, mobileIcon, tvIcon, serverUrl) {
    // Enviar alguém para descarregar a aplicação ERRADA é pior do que não
    // sugerir nenhuma: os links seguem o servidor que o painel administra.
    if (criaContas()) {
        return `
            ${createAppCard('Desktop', 'https://jellyfin.org/downloads/clients', desktopIcon)}
            ${createAppCard('Android', 'https://play.google.com/store/apps/details?id=org.jellyfin.mobile', mobileIcon)}
            ${createAppCard('Apple (iOS)', 'https://apps.apple.com/app/jellyfin-mobile/id1480192618', mobileIcon)}
            ${createAppCard('Smart TVs', 'https://jellyfin.org/downloads/clients', tvIcon)}
            ${createAppCard(i18n.webBrowser, safeUrl(serverUrl) || 'https://jellyfin.org/downloads/clients', desktopIcon)}`;
    }

    return `
        ${createAppCard('Desktop', 'https://www.plex.tv/pt-br/media-server-downloads/#plex-app', desktopIcon)}
        ${createAppCard('Android', 'https://play.google.com/store/apps/details?id=com.plexapp.android', mobileIcon)}
        ${createAppCard('Apple (iOS)', 'https://apps.apple.com/us/app/plex-movies-tv-music-more/id383457673', mobileIcon)}
        ${createAppCard('Smart TVs', 'https://www.plex.tv/pt-br/apps-devices/#tv', tvIcon)}
        ${createAppCard('Consoles', 'https://www.plex.tv/pt-br/apps-devices/#console', tvIcon)}
        ${createAppCard(i18n.webBrowser, 'https://app.plex.tv/desktop/#!/', desktopIcon)}`;
}

function comoComecarPlex() {
    return `
        <div class="text-left mt-6 border-t border-gray-200 dark:border-gray-700/50 pt-6">
            <h2 class="text-xl font-semibold text-gray-900 dark:text-white mb-2">${i18n.whatIsPlex}</h2>
            <p class="text-sm text-gray-600 dark:text-gray-400 mb-4">${i18n.plexDesc}</p>

            <h3 class="text-lg font-semibold text-gray-900 dark:text-white mt-4 mb-2">${i18n.howToStart}</h3>
            <ol class="list-decimal list-inside space-y-2 text-sm text-gray-600 dark:text-gray-400 step-list">
                <li>${i18n.step1Text} <a href="https://www.plex.tv/pt-br/sign-up/" target="_blank" rel="noopener noreferrer" class="text-yellow-500 hover:underline font-semibold">${i18n.step1Link}</a>${i18n.step1End}</li>
                <li>${i18n.step2}</li>
            </ol>
        </div>`;
}

function comoComecarLocal() {
    // Num servidor de contas locais não há conta externa para criar primeiro: a
    // conta nasce aqui, e os passos são outros — incluindo o que não existe no
    // Plex: a palavra-passe é escolhida agora e não se recupera depois.
    const passos = [
        texto('stepLocalOne'),
        texto('stepLocalTwo'),
        texto('stepLocalThree'),
    ].filter(Boolean);

    return `
        <div class="text-left mt-6 border-t border-gray-200 dark:border-gray-700/50 pt-6">
            <h3 class="text-lg font-semibold text-gray-900 dark:text-white mb-2">${texto('howToStartLocal')}</h3>
            <p class="text-sm text-gray-600 dark:text-gray-400 mb-3">${texto('whatIsLocal')}</p>
            <ol class="list-decimal list-inside space-y-2 text-sm text-gray-600 dark:text-gray-400 step-list">
                ${passos.map((passo) => `<li>${passo}</li>`).join('')}
            </ol>
        </div>`;
}

function botaoDeLoginPlex() {
    return `
        <div class="mt-8">
            <button id="login-button" type="button" class="group relative w-full flex justify-center items-center py-3 px-4 border border-transparent text-sm font-medium rounded-md text-gray-900 bg-yellow-500 hover:bg-yellow-600 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-yellow-500 transition-transform transform hover:scale-105">
                 <svg class="w-6 h-6 mr-3" xmlns="http://www.w3.org/2000/svg" fill="currentColor" viewBox="0 0 24 24"><path d="M11.64,12.02C11.64,12.02,11.64,12.02,11.64,12.02L9.36,7.66L9.35,7.63C9.35,7.63,9.35,7.63,9.35,7.63L11.64,12L9.35,16.38C9.35,16.38,9.35,16.38,9.35,16.38L9.36,16.35L11.64,12.02M12,2C6.48,2,2,6.48,2,12C2,17.52,6.48,22,12,22C17.52,22,22,17.52,22,12C22,6.48,17.52,2,12,2M14.65,16.37H12.44L12.44,12.03L14.65,7.64H17L13.8,12.01L17,16.37H14.65Z" /></svg>
                ${i18n.loginToRedeem}
            </button>
        </div>`;
}

function formularioDeRegisto() {
    const campo = 'block w-full px-3 py-2.5 text-sm rounded-xl border border-gray-300 bg-white text-gray-900 dark:bg-gray-800 dark:border-gray-600 dark:text-white shadow-sm';
    return `
        <form id="register-form" class="mt-8 text-left space-y-4">
            <h2 class="text-xl font-semibold text-gray-900 dark:text-white">${i18n.createAccount}</h2>
            <p class="text-sm text-gray-600 dark:text-gray-400">${i18n.createAccountDesc}</p>

            <div>
                <label for="register-username" class="block text-sm font-semibold text-gray-700 dark:text-gray-300 mb-1">${i18n.username}</label>
                <!-- Os \`maxlength\` acompanham o que a rota de resgate aceita
                     (MAX_UTILIZADOR / MAX_PALAVRA_PASSE / MAX_EMAIL em
                     \`api/invites.py\`): o travão a sério é do servidor. -->
                <input type="text" id="register-username" autocomplete="username" required
                       maxlength="128" autocapitalize="off" autocorrect="off" spellcheck="false"
                       class="${campo}">
            </div>
            <div>
                <label for="register-password" class="block text-sm font-semibold text-gray-700 dark:text-gray-300 mb-1">${i18n.password}</label>
                <div class="relative">
                    <input type="password" id="register-password" autocomplete="new-password"
                           minlength="6" maxlength="256" required spellcheck="false"
                           class="${campo} pr-11">
                    <button type="button" id="toggle-register-password" tabindex="-1"
                            aria-label="${i18n.showPassword || ''}" aria-pressed="false"
                            class="absolute inset-y-0 right-0 px-3 flex items-center text-gray-400 hover:text-gray-600 dark:hover:text-gray-200">
                        <svg id="register-icon-eye" class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"></path><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z"></path></svg>
                        <svg id="register-icon-eye-off" class="w-5 h-5 hidden" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.875 18.825A10.05 10.05 0 0112 19c-4.477 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29M3 3l3.59 3.59m0 0A9.953 9.953 0 0112 5c4.477 0 8.268 2.943 9.543 7a10.025 10.025 0 01-4.132 5.411m0 0L21 21"></path></svg>
                    </button>
                </div>
                <!-- ⚠️ Esta palavra-passe não se recupera: o painel não a guarda
                     nem tem como a repor. Ver o que se escreveu é a diferença
                     entre entrar e ficar de fora. -->
                <p class="text-xs text-gray-500 dark:text-gray-400 mt-1">${i18n.passwordHelp || ''}</p>
            </div>
            <div>
                <label for="register-email" class="block text-sm font-semibold text-gray-700 dark:text-gray-300 mb-1">${i18n.emailOptional}</label>
                <input type="email" id="register-email" autocomplete="email" maxlength="254"
                       autocapitalize="off" autocorrect="off" spellcheck="false" class="${campo}">
                <p class="text-xs text-gray-500 dark:text-gray-400 mt-1">${i18n.emailHelp}</p>
            </div>

            <button type="submit" id="register-submit" class="w-full flex justify-center items-center py-3 px-4 text-sm font-semibold rounded-md text-white bg-purple-600 hover:bg-purple-500 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-purple-500 transition-transform transform hover:scale-105 disabled:opacity-50 disabled:cursor-not-allowed disabled:transform-none">
                ${i18n.createAndRedeem}
            </button>
            <p id="register-error" class="text-sm text-red-500 text-center" role="alert"></p>
        </form>`;
}

function ligarBotaoDeVerPalavraPasse() {
    const botao = document.getElementById('toggle-register-password');
    botao?.addEventListener('click', () => {
        const campo = document.getElementById('register-password');
        const visivel = campo.type === 'text';

        campo.type = visivel ? 'password' : 'text';
        document.getElementById('register-icon-eye')?.classList.toggle('hidden', !visivel);
        document.getElementById('register-icon-eye-off')?.classList.toggle('hidden', visivel);
        botao.setAttribute('aria-pressed', String(!visivel));
        botao.setAttribute('aria-label', (visivel ? i18n.showPassword : i18n.hidePassword) || '');
        campo.focus();
    });
}

async function registarEResgatar(evento) {
    evento.preventDefault();

    const botao = document.getElementById('register-submit');
    const erro = document.getElementById('register-error');
    const username = document.getElementById('register-username').value.trim();
    const password = document.getElementById('register-password').value;
    const email = document.getElementById('register-email').value.trim();

    erro.textContent = '';

    if (password.length < 6) {
        erro.textContent = i18n.passwordTooShort || '';
        return;
    }

    botao.disabled = true;
    const textoOriginal = botao.textContent;
    botao.textContent = i18n.creating || '';

    try {
        const resposta = await fetch(urls.claimInvite, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code: inviteCode, username, password, email }),
        });
        const dados = await resposta.json();

        if (dados.success) {
            showImprovedOnboarding(dados.user_data || {});
            return;
        }
        erro.textContent = dados.message || i18n.claimFail;
    } catch (e) {
        erro.textContent = i18n.networkError || i18n.claimFail;
    } finally {
        botao.disabled = false;
        botao.textContent = textoOriginal;
    }
}

async function validateInvite() {
    try {
        const response = await fetch(urls.validateInvite);
        const data = await response.json();
        if (!response.ok || !data.success) {
            throw new Error(data.message || i18n.validateFail);
        }
        
        mainContainer.innerHTML = `
            <svg class="w-16 h-16 text-yellow-400 mx-auto" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor"><path d="M12.0001 1.5C11.3001 1.5 10.7301 2.01 10.6501 2.71L9.50006 12.35L4.08006 15.2C3.36006 15.65 3.11006 16.59 3.56006 17.31C3.88006 17.84 4.48006 18.15 5.12006 18.15H6.28006L8.47006 22.29C8.91006 23.12 9.87006 23.57 10.7601 23.36C11.6501 23.15 12.3001 22.35 12.3001 21.42V14.88L17.5301 17.9C18.1501 18.25 18.8901 18.06 19.3501 17.48L21.8201 13.94C22.2801 13.36 22.1801 12.55 21.6501 12.01L15.2701 5.68C14.7301 5.14 13.8801 5.21 13.4301 5.76L12.3001 7.15V2.85C12.3001 2.1 11.7001 1.5 12.0001 1.5Z"/></svg>
            <h1 class="text-3xl font-bold text-gray-900 dark:text-white mt-4">${i18n.youAreInvited}</h1>
            
            ${criaContas() ? comoComecarLocal() : comoComecarPlex()}
            <div id="expiration-container"></div>
            ${criaContas() ? formularioDeRegisto() : botaoDeLoginPlex()}
        `;
        
        if (criaContas()) {
            document.getElementById('register-form').addEventListener('submit', registarEResgatar);
            ligarBotaoDeVerPalavraPasse();
        } else {
            document.getElementById('login-button').onclick = loginWithPlexToClaim;
        }
        
        if (data.details && data.details.expires_at) {
            const expirationDate = new Date(data.details.expires_at);
            const now = new Date();
            const diffMs = expirationDate - now;

            // 🐛 Convite JÁ EXPIRADO: antes este ramo não mostrava nada. O visitante
            // via a página normal, clicava em "Entrar com Plex", autenticava-se e só
            // então descobria que o convite tinha expirado — depois de todo o esforço.
            // Agora avisamos já, e desativamos o botão.
            if (diffMs <= 0) {
                desativarResgate();
                document.getElementById('expiration-container').innerHTML = `
                    <div class="mt-6 p-4 bg-red-100 dark:bg-red-500/20 border-l-4 border-red-500 text-red-700 dark:text-red-200 text-sm text-left rounded-r-lg">
                        <p class="font-bold">${i18n.inviteExpired || 'Este convite já expirou.'}</p>
                        <p class="mt-1 text-xs opacity-80">${i18n.inviteExpiredDesc || ''}</p>
                    </div>
                `;
                return;
            }

            let expiresIn = '';
            if (diffMs > 0) {
                const diffMins = Math.round(diffMs / 60000);
                if (diffMins < 2) {
                    expiresIn = i18n.inLessThanAMinute;
                } else if (diffMins < 60) {
                    expiresIn = i18n.inMinutes.replace('{minutes}', diffMins);
                } else {
                     expiresIn = i18n.atTime.replace('{time}', formatTime(expirationDate)).replace('{date}', formatDate(expirationDate));
                }
                const expirationHtml = `
                    <div class="mt-6 p-3 bg-yellow-100 dark:bg-yellow-500/20 border-l-4 border-yellow-500 text-yellow-700 dark:text-yellow-200 text-sm text-left rounded-r-lg">
                        <p><strong>${i18n.attention}</strong> ${i18n.inviteExpires} ${expiresIn}.</p>
                    </div>
                `;
                document.getElementById('expiration-container').innerHTML = expirationHtml;
            }
        }

    } catch(e) {
        showMessage(i18n.invalidInvite, e.message, true);
    }
}

// --- PONTO DE ENTRADA ---
document.addEventListener('DOMContentLoaded', validateInvite);

