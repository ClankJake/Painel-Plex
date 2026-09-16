/**
 * login.js
 * Lógica para a página de login, incluindo o fluxo de autenticação com Plex.
 */

import { buildPinCheckUrl, lerConfiguracaoDoScript } from './utils.js';

document.addEventListener('DOMContentLoaded', () => {
    // --- ELEMENTOS E DADOS GLOBAIS ---
    const loginButton = document.getElementById('login-button');
    const loginButtonText = document.getElementById('login-button-text');
    const { urls, i18n } = lerConfiguracaoDoScript('login-script');
    
    let pinCheckInterval = null;
    let authWindow = null;

    // --- LOGIN COM CREDENCIAIS (servidores de contas locais) ---
    const credentialsForm = document.getElementById('credentials-form');

    credentialsForm?.addEventListener('submit', async (evento) => {
        evento.preventDefault();

        const botao = document.getElementById('credentials-submit');
        const textoBotao = document.getElementById('credentials-submit-text');
        const spinner = document.getElementById('credentials-spinner');
        const erro = document.getElementById('credentials-error');
        const username = document.getElementById('login-username').value.trim();
        const password = document.getElementById('login-password').value;

        erro.textContent = '';

        if (!username || !password) {
            erro.textContent = i18n.credentialsRequired || '';
            return;
        }

        botao.disabled = true;
        spinner?.classList.remove('hidden');
        if (textoBotao) textoBotao.textContent = i18n.signingIn || '';

        try {
            const resposta = await fetch(urls.loginCredentials, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username, password }),
            });
            const dados = await resposta.json();

            if (dados.success && dados.redirect_url) {
                window.location.href = dados.redirect_url;
                return;
            }
            // Uma resposta sem corpo útil (429 do limitador, 500) deixava o
            // campo em branco e a pessoa sem saber o que tinha acontecido.
            erro.textContent = dados.message || dados.error || i18n.authCheckError || '';
        } catch (e) {
            erro.textContent = i18n.authCheckError || '';
        } finally {
            // A palavra-passe não fica no campo depois de uma tentativa falhada.
            const campoPalavraPasse = document.getElementById('login-password');
            campoPalavraPasse.value = '';
            campoPalavraPasse.focus();
            spinner?.classList.add('hidden');
            botao.disabled = false;
            if (textoBotao) textoBotao.textContent = i18n.signIn || '';
        }
    });

    // Ver o que se escreveu: numa palavra-passe de servidor local, escrita à mão
    // num telemóvel, é a diferença entre entrar e ficar a tentar.
    const alternarPalavraPasse = document.getElementById('toggle-password');
    alternarPalavraPasse?.addEventListener('click', () => {
        const campo = document.getElementById('login-password');
        const visivel = campo.type === 'text';

        campo.type = visivel ? 'password' : 'text';
        document.getElementById('icon-eye')?.classList.toggle('hidden', !visivel);
        document.getElementById('icon-eye-off')?.classList.toggle('hidden', visivel);
        alternarPalavraPasse.setAttribute('aria-pressed', String(!visivel));
        alternarPalavraPasse.setAttribute(
            'aria-label',
            (visivel ? i18n.showPassword : i18n.hidePassword) || ''
        );
        campo.focus();
    });

    // --- FUNÇÕES AUXILIARES ---

    function showFlashMessage(message, category = 'error') {
        const container = document.getElementById('flash-container');
        if (!container) return;

        const colors = {
            error: 'bg-red-500/80 text-white',
            success: 'bg-green-500/80 text-white',
            info: 'bg-blue-500/80 text-white'
        };
        const alertClass = colors[category] || colors.info;
        container.innerHTML = `<div class="p-3 my-2 text-sm rounded-lg text-center font-medium ${alertClass}" role="alert">${message}</div>`;
    }

    function startPolling(pin_id, client_id) {
        if (pinCheckInterval) clearInterval(pinCheckInterval);

        pinCheckInterval = setInterval(async () => {
            if (!authWindow || authWindow.closed) {
                clearInterval(pinCheckInterval);
                loginButton.disabled = false;
                loginButtonText.textContent = i18n.loginWithPlex;
                loginButton.classList.remove('animate-pulse');
                window.removeEventListener('message', handleAuthMessage);
                return;
            }
            
            try {
                const checkUrl = buildPinCheckUrl(urls.checkPlexPin, client_id, pin_id);
                const checkResponse = await fetch(checkUrl);
                const checkData = await checkResponse.json();

                if (checkData.success) {
                    clearInterval(pinCheckInterval);
                    authWindow.close();
                    // Redireciona com base na ação definida pelo backend
                    window.location.href = checkData.redirect_url || '/';
                } else if (checkData.message === 'auth_denied') {
                    clearInterval(pinCheckInterval);
                    if(!authWindow.closed) authWindow.close();
                    showFlashMessage(checkData.error || i18n.authDenied, 'error');
                    loginButton.disabled = false;
                    loginButtonText.textContent = i18n.loginWithPlex;
                    loginButton.classList.remove('animate-pulse');
                }
            } catch (e) {
                clearInterval(pinCheckInterval);
                showFlashMessage(i18n.authCheckError, 'error');
                loginButton.disabled = false;
                loginButtonText.textContent = i18n.loginWithPlex;
                loginButton.classList.remove('animate-pulse');
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

    function loginWithPlex() {
        loginButton.disabled = true;
        loginButtonText.textContent = i18n.waitingAuth;
        loginButton.classList.add('animate-pulse');
        
        window.addEventListener('message', handleAuthMessage, false);
        
        authWindow = window.open(urls.redirectToAuth, 'plexAuth', 'width=800,height=700,status=no,scrollbars=yes,resizable=yes');
    }

    // --- EVENT LISTENERS ---
    if (loginButton) {
        loginButton.addEventListener('click', loginWithPlex);
    }
});
