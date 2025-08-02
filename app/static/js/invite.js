/**
 * invite.js
 * Lógica para a página de resgate de convites.
 */

// --- INICIALIZAÇÃO ---
const scriptTag = document.getElementById('invite-script');
const inviteCode = scriptTag.dataset.inviteCode;

// Mapeia os data-attributes para objetos para facilitar o acesso
const urls = {};
const i18n = {};
for (const key in scriptTag.dataset) {
    if (key.startsWith('url')) {
        const urlKey = key.charAt(3).toLowerCase() + key.slice(4);
        urls[urlKey] = scriptTag.dataset[key];
    } else if (key.startsWith('i18n')) {
        const i18nKey = key.charAt(4).toLowerCase() + key.slice(5);
        i18n[i18nKey] = scriptTag.dataset[key];
    }
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

    mainContainer.innerHTML = `
        ${icon}
        <h1 class="text-3xl font-bold ${titleColor} mt-4">${title}</h1>
        <p class="mt-2 text-lg text-gray-600 dark:text-gray-300">${message}</p>
    `;
}

function createAppCard(title, href, svgPath) {
    return `
        <a href="${href}" target="_blank" rel="noopener noreferrer" class="block bg-gray-100 dark:bg-gray-700/50 p-4 rounded-lg hover:bg-yellow-100 dark:hover:bg-yellow-500/20 hover:scale-105 transition-all duration-200">
            <svg class="w-10 h-10 mx-auto text-gray-700 dark:text-gray-300" fill="currentColor" viewBox="0 0 24 24">${svgPath}</svg>
            <p class="mt-2 text-sm font-semibold text-gray-800 dark:text-gray-200">${title}</p>
        </a>
    `;
}

function showSuccessAndApps(welcomeMessage) {
    const desktopIcon = `<path d="M21 13H3a1 1 0 01-1-1V4a1 1 0 011-1h18a1 1 0 011 1v8a1 1 0 01-1 1zm-1-2V5H4v6h16z"></path><path d="M12 15H3.21a1 1 0 00-.97 1.24l1.39 4A1 1 0 004.59 21h14.82a1 1 0 00.97-.76l1.39-4A1 1 0 0020.79 15H12z"></path>`;
    const mobileIcon = `<path d="M17 2H7a3 3 0 00-3 3v14a3 3 0 003 3h10a3 3 0 003-3V5a3 3 0 00-3-3zm-1 16H8a1 1 0 010-2h8a1 1 0 010 2zm1-4H6V6a1 1 0 011-1h10a1 1 0 011 1v8z"></path>`;
    const tvIcon = `<path d="M21 16H3a1 1 0 010-2h18a1 1 0 010 2zM20 3H4a3 3 0 00-3 3v6a3 3 0 003 3h16a3 3 0 003-3V6a3 3 0 00-3-3zm1 9a1 1 0 01-1 1H4a1 1 0 01-1-1V6a1 1 0 011-1h16a1 1 0 011 1v6z"></path>`;
    
    mainContainer.innerHTML = `
        <svg class="w-16 h-16 text-green-500 mx-auto" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
        <h1 class="text-3xl font-bold text-green-500 mt-4">${i18n.success}</h1>
        <p class="mt-2 text-lg text-gray-600 dark:text-gray-300">${welcomeMessage}</p>

        <div class="mt-8 text-left border-t border-gray-200 dark:border-gray-700/50 pt-6">
            <h2 class="text-2xl font-bold text-gray-900 dark:text-white mb-2 text-center">${i18n.enjoyAnywhere}</h2>
            <p class="text-center text-gray-600 dark:text-gray-400 mb-6">${i18n.downloadPlex}</p>
            <div class="grid grid-cols-2 sm:grid-cols-3 gap-4 text-center">
                ${createAppCard('Desktop', 'https://www.plex.tv/pt-br/media-server-downloads/#plex-app', desktopIcon)}
                ${createAppCard('Android', 'https://play.google.com/store/apps/details?id=com.plexapp.android', mobileIcon)}
                ${createAppCard('Apple (iOS)', 'https://apps.apple.com/us/app/plex-movies-tv-music-more/id383457673', mobileIcon)}
                ${createAppCard('Smart TVs', 'https://www.plex.tv/pt-br/apps-devices/#tv', tvIcon)}
                ${createAppCard('Consoles', 'https://www.plex.tv/pt-br/apps-devices/#console', tvIcon)}
                ${createAppCard(i18n.webBrowser, 'https://app.plex.tv/desktop/#!/', desktopIcon)}
            </div>
            <div class="text-center mt-6">
                <a href="https://www.plex.tv/pt-br/apps-devices/" target="_blank" rel="noopener noreferrer" class="text-sm text-yellow-500 hover:underline">${i18n.allDevices} &rarr;</a>
            </div>
        </div>
    `;
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
            showSuccessAndApps(result.message);
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

    pinCheckInterval = setInterval(async () => {
        if (!authWindow || authWindow.closed) {
            clearInterval(pinCheckInterval);
            if (loginButton) {
                loginButton.disabled = false;
                loginButton.innerHTML = i18n.loginToRedeem;
            }
            window.removeEventListener('message', handleAuthMessage);
            return;
        }
        
        try {
            const checkUrl = urls.checkPinForToken.replace('__CLIENT_ID__', client_id).replace('999999', pin_id);
            const checkResponse = await fetch(checkUrl);
            const checkData = await checkResponse.json();

            if (checkData.success && checkData.token) {
                clearInterval(pinCheckInterval);
                authWindow.close();
                await claimInvite(checkData.token);
            } else if (checkData.message === 'auth_denied') {
                clearInterval(pinCheckInterval);
                if(!authWindow.closed) authWindow.close();
                showMessage(i18n.error, checkData.error || i18n.authDenied, true);
            }
        } catch (e) {
            clearInterval(pinCheckInterval);
            showMessage(i18n.error, i18n.authCheckError, true);
        }
    }, 3000);
}

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
    loginButton.disabled = true;
    loginButton.innerHTML = `<svg class="animate-spin h-5 w-5 mr-3" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>${i18n.waitingAuth}`;

    window.addEventListener('message', handleAuthMessage, false);
    authWindow = window.open(urls.redirectToAuth, 'plexAuth', 'width=800,height=700,status=no,scrollbars=yes,resizable=yes');
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
            
            <div class="text-left mt-6 border-t border-gray-200 dark:border-gray-700/50 pt-6">
                <h2 class="text-xl font-semibold text-gray-900 dark:text-white mb-2">${i18n.whatIsPlex}</h2>
                <p class="text-sm text-gray-600 dark:text-gray-400 mb-4">${i18n.plexDesc}</p>
                
                <h3 class="text-lg font-semibold text-gray-900 dark:text-white mt-4 mb-2">${i18n.howToStart}</h3>
                <ol class="list-decimal list-inside space-y-2 text-sm text-gray-600 dark:text-gray-400 step-list">
                    <li>${i18n.step1Text} <a href="https://www.plex.tv/pt-br/sign-up/" target="_blank" rel="noopener noreferrer" class="text-yellow-500 hover:underline font-semibold">${i18n.step1Link}</a>${i18n.step1End}</li>
                    <li>${i18n.step2}</li>
                    <li>${i18n.step3}</li>
                </ol>
            </div>
            <div id="expiration-container"></div>
            <div class="mt-8">
                <button id="login-button" type="button" class="group relative w-full flex justify-center items-center py-3 px-4 border border-transparent text-sm font-medium rounded-md text-gray-900 bg-yellow-500 hover:bg-yellow-600 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-yellow-500 transition-transform transform hover:scale-105">
                     <svg class="w-6 h-6 mr-3" xmlns="http://www.w3.org/2000/svg" fill="currentColor" viewBox="0 0 24 24"><path d="M11.64,12.02C11.64,12.02,11.64,12.02,11.64,12.02L9.36,7.66L9.35,7.63C9.35,7.63,9.35,7.63,9.35,7.63L11.64,12L9.35,16.38C9.35,16.38,9.35,16.38,9.35,16.38L9.36,16.35L11.64,12.02M12,2C6.48,2,2,6.48,2,12C2,17.52,6.48,22,12,22C17.52,22,22,17.52,22,12C22,6.48,17.52,2,12,2M14.65,16.37H12.44L12.44,12.03L14.65,7.64H17L13.8,12.01L17,16.37H14.65Z" /></svg>
                    ${i18n.loginToRedeem}
                </button>
            </div>
        `;
        
        document.getElementById('login-button').onclick = loginWithPlexToClaim;
        
        if (data.details && data.details.expires_at) {
            const expirationDate = new Date(data.details.expires_at);
            const now = new Date();
            const diffMs = expirationDate - now;
            
            let expiresIn = '';
            if (diffMs > 0) {
                const diffMins = Math.round(diffMs / 60000);
                if (diffMins < 2) {
                    expiresIn = i18n.inLessThanAMinute;
                } else if (diffMins < 60) {
                    expiresIn = i18n.inMinutes.replace('{minutes}', diffMins);
                } else {
                     expiresIn = i18n.atTime.replace('{time}', expirationDate.toLocaleTimeString('pt-BR')).replace('{date}', expirationDate.toLocaleDateString('pt-BR'));
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

