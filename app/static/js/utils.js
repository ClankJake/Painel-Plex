/**
 * utils.js
 * Este ficheiro contém funções auxiliares partilhadas por várias páginas da aplicação.
 */

/**
 * Sanitiza entradas de texto para prevenir ataques XSS (Cross-Site Scripting).
 * Centralizado aqui para evitar repetição (DRY).
 * @param {string} str Texto a ser sanitizado.
 * @returns {string} Texto sanitizado.
 */
export const sanitizeHTML = (str) => {
    if (!str) return '';
    const temp = document.createElement('div');
    temp.textContent = str;
    return temp.innerHTML;
};

/**
 * Copia um texto para a área de transferência usando a API moderna com fallback.
 * Substitui o antigo copyToClipboardFallback.
 * @param {string} text O texto a ser copiado.
 * @returns {Promise<boolean>} True se a cópia foi bem sucedida, false caso contrário.
 */
export const copyToClipboard = async (text) => {
    // Tenta usar a API moderna do navegador primeiro (requer HTTPS)
    if (navigator.clipboard && window.isSecureContext) {
        try {
            await navigator.clipboard.writeText(text);
            return true;
        } catch (err) {
            console.warn('Falha na API clipboard moderna, tentando fallback...', err);
        }
    }
    
    // Fallback para navegadores antigos ou conexões locais sem HTTPS
    const el = document.createElement('textarea');
    el.value = text;
    el.setAttribute('readonly', '');
    el.style.position = 'absolute';
    el.style.left = '-9999px';
    document.body.appendChild(el);
    el.select();
    try {
        document.execCommand('copy');
        document.body.removeChild(el);
        return true;
    } catch (err) {
        document.body.removeChild(el);
        console.error('Fallback de cópia falhou', err);
        return false;
    }
};

/**
 * Realiza uma chamada para um endpoint da API.
 * @param {string} endpoint O URL do endpoint da API.
 * @param {string} [method='GET'] O método HTTP a ser utilizado.
 * @param {object|null} [body=null] O corpo da requisição para métodos como POST.
 * @returns {Promise<any>} A resposta JSON da API.
 * @throws {Error} Lança um erro se a requisição falhar ou a resposta não for OK.
 */
export async function fetchAPI(endpoint, method = 'GET', body = null) {
    if (!endpoint) {
        const errorMsg = 'Erro de configuração: URL da API não encontrada.';
        console.error('Fetch API Error: Endpoint is undefined.');
        showToast(errorMsg, 'error');
        throw new Error(errorMsg);
    }

    const options = { method, headers: { 'Content-Type': 'application/json' } };
    if (body) {
        options.body = JSON.stringify(body);
    }

    try {
        const response = await fetch(endpoint, options);
        // Se o utilizador não estiver autenticado, o servidor pode redirecionar para o login.
        if (response.status === 401 || response.redirected) {
            // O ideal é ter o URL de login disponível globalmente se esta verificação for necessária aqui.
            window.location.href = '/auth/login'; 
            return;
        }
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.message || `HTTP error! status: ${response.status}`);
        }
        return data;
    } catch (error) {
        console.error('Fetch API Error:', error);
        // A notificação de erro é movida para o local da chamada para ser mais específica ao contexto.
        throw error;
    }
}

/**
 * Exibe uma notificação toast no ecrã.
 * CORREÇÃO MOBILE: A classe pointer-events-none foi adicionada diretamente para
 * garantir que o toast invisível não bloqueie toques na tela (bug de z-index transparente).
 * 
 * @param {string} message A mensagem a ser exibida.
 * @param {'success'|'error'|'info'} [type='success'] O tipo de toast (afeta a cor).
 */
export function showToast(message, type = 'success') {
    const toastEl = document.getElementById('toast');
    if (!toastEl) return;
    
    toastEl.textContent = message;
    
    // pointer-events-none adicionado dinamicamente caso não exista no CSS base do HTML
    toastEl.className = `toast show ${type} pointer-events-none`; 
    
    setTimeout(() => {
        toastEl.className = 'toast pointer-events-none';
    }, 4000);
}

/**
 * Cria e exibe um modal genérico.
 * @param {string} id O ID do elemento do modal no DOM.
 * @param {string} title O título do modal.
 * @param {string} body O conteúdo HTML do corpo do modal.
 * @param {string} footer O conteúdo HTML do rodapé do modal (geralmente botões).
 * @returns {HTMLElement|undefined} O elemento do modal criado.
 */
export function createModal(id, title, body, footer) {
    const modal = document.getElementById(id);
    if (!modal) {
        console.error(`Elemento do modal com id '${id}' não encontrado.`);
        return;
    }
    modal.innerHTML = `
        <div class="modal-content transform transition-all sm:my-8 sm:w-full sm:max-w-lg">
            <div class="bg-white dark:bg-gray-800 px-4 pt-5 pb-4 sm:p-6 sm:pb-4 rounded-lg shadow-xl">
                <h3 class="text-xl font-bold text-gray-900 dark:text-white mb-4">${title}</h3>
                <div class="modal-body text-gray-600 dark:text-gray-300">${body}</div>
                <div class="bg-gray-50 dark:bg-gray-800/50 px-4 py-3 sm:px-6 flex flex-wrap-reverse justify-end gap-3 mt-6 rounded-b-lg">
                    ${footer}
                </div>
            </div>
        </div>
    `;
    modal.classList.remove('hidden');
    // Adiciona evento para fechar o modal ao clicar no fundo
    modal.addEventListener('click', (e) => {
        if (e.target.id === id) {
            modal.classList.add('hidden');
        }
    });
    return modal;
}
