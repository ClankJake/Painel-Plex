/**
 * utils.js
 * Este ficheiro contém funções auxiliares partilhadas por várias páginas da aplicação.
 */

/**
 * Sanitiza entradas de texto para prevenir ataques XSS (Cross-Site Scripting).
 * Centralizado aqui para evitar repetição (DRY).
 *
 * 🛡️ CORREÇÃO: a implementação anterior usava `textContent` + `innerHTML`, que
 * escapa `&`, `<` e `>` mas NÃO as aspas. Isso é suficiente no corpo do HTML,
 * mas não dentro de um atributo — e o resultado desta função é interpolado em
 * `value="..."`, `title="..."` e `data-*="..."` por toda a aplicação. Um valor
 * com aspas fechava o atributo mais cedo: no melhor caso truncava o conteúdo
 * (uma biblioteca com aspas no nome deixava de ser partilhada corretamente), no
 * pior permitia injetar novos atributos. Delega agora em `escapeHTML`, que
 * também escapa `"` e `'`. Em contexto de texto o resultado é idêntico — o
 * navegador volta a mostrar as aspas normalmente.
 *
 * @param {string} str Texto a ser sanitizado.
 * @returns {string} Texto sanitizado, seguro em corpo E em atributos.
 */
export const sanitizeHTML = (str) => escapeHTML(str);

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

    // 🐛 CORREÇÃO: o listener do fundo era adicionado a CADA abertura do modal.
    // Como o elemento é reutilizado (só o conteúdo é substituído), abrir o mesmo
    // modal dez vezes deixava dez listeners ligados ao mesmo nó. Marcar o
    // elemento garante que a ligação é feita uma única vez.
    if (!modal.dataset.closeHandlersBound) {
        modal.dataset.closeHandlersBound = 'true';

        // Fecha ao clicar no fundo (fora do conteúdo).
        modal.addEventListener('click', (e) => {
            if (e.target === modal) modal.classList.add('hidden');
        });

        // Fecha com Escape — comportamento esperado em qualquer diálogo.
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && !modal.classList.contains('hidden')) {
                modal.classList.add('hidden');
            }
        });
    }

    return modal;
}

/**
 * =====================================================================
 * BOTÕES COM ESTADO DE CARREGAMENTO (SEM REINTERPRETAR HTML)
 * =====================================================================
 *
 * 🔒 PORQUÊ ESTES HELPERS EXISTEM
 *
 * O padrão comum de "guardar e restaurar" o conteúdo de um botão era:
 *
 *     const original = botao.innerHTML;   // lê HTML do DOM
 *     botao.innerHTML = 'A carregar...';
 *     // ...
 *     botao.innerHTML = original;         // volta a interpretar como HTML
 *
 * Ler texto do DOM e voltar a escrevê-lo como HTML anula qualquer escape que
 * tenha sido feito antes (o texto é re-analisado como marcação). Se algum
 * conteúdo dentro do botão tiver origem, direta ou indireta, em dados
 * controlados por um utilizador, isto torna-se um vetor de Cross-Site
 * Scripting (XSS) — é exatamente este o padrão que as análises de segurança
 * assinalam.
 *
 * A solução aqui NÃO usa strings de HTML: guarda os NÓS reais do DOM (clonados)
 * e volta a colocá-los tal como estavam. Como nunca há uma etapa de "texto ->
 * HTML", não existe reinterpretação possível, independentemente do conteúdo.
 */

const _buttonStateCache = new WeakMap();

/**
 * Coloca um botão em estado de carregamento, preservando com segurança o seu
 * conteúdo original para restauro posterior.
 *
 * @param {HTMLElement} button - O botão a alterar.
 * @param {string} loadingText - Texto a mostrar (inserido como TEXTO, nunca HTML).
 * @param {boolean} withSpinner - Se deve mostrar um indicador de progresso.
 */
export function setButtonLoading(button, loadingText, withSpinner = true) {
    if (!button) return;

    // Guarda os nós originais (clonados) — não o HTML em forma de texto.
    if (!_buttonStateCache.has(button)) {
        _buttonStateCache.set(button, Array.from(button.childNodes).map(node => node.cloneNode(true)));
    }

    button.disabled = true;
    button.replaceChildren();

    if (withSpinner) {
        // O spinner é construído com createElementNS/createElement: nunca há
        // análise de uma string como HTML.
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('class', 'animate-spin h-4 w-4 mr-2 inline');
        svg.setAttribute('viewBox', '0 0 24 24');
        svg.setAttribute('fill', 'none');

        const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        circle.setAttribute('class', 'opacity-25');
        circle.setAttribute('cx', '12');
        circle.setAttribute('cy', '12');
        circle.setAttribute('r', '10');
        circle.setAttribute('stroke', 'currentColor');
        circle.setAttribute('stroke-width', '4');

        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        path.setAttribute('class', 'opacity-75');
        path.setAttribute('fill', 'currentColor');
        path.setAttribute('d', 'M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z');

        svg.appendChild(circle);
        svg.appendChild(path);
        button.appendChild(svg);
    }

    if (loadingText) {
        // textContent -> o texto nunca é interpretado como marcação.
        button.appendChild(document.createTextNode(loadingText));
    }
}

/**
 * Restaura um botão ao estado anterior a setButtonLoading, recolocando os nós
 * originais do DOM (sem qualquer reinterpretação de HTML).
 *
 * @param {HTMLElement} button - O botão a restaurar.
 */
export function restoreButton(button) {
    if (!button) return;

    button.disabled = false;

    const originalNodes = _buttonStateCache.get(button);
    if (originalNodes) {
        button.replaceChildren(...originalNodes.map(node => node.cloneNode(true)));
        _buttonStateCache.delete(button);
    }
}

/**
 * Escapa texto para inclusão segura dentro de uma string de HTML.
 * Usar sempre que um valor de origem externa (API, utilizador, nome de ficheiro,
 * mensagem de erro) tiver de ser interpolado num template literal com HTML.
 *
 * É uma declaração de função (e não uma const) de propósito: o hoisting permite
 * que `sanitizeHTML`, definida no topo deste ficheiro, a utilize.
 */
export function escapeHTML(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[char]));
}


/**
 * Constrói o URL de verificação do PIN do Plex a partir do modelo que o
 * `url_for` deixa no template (…/check-pin/__CLIENT_ID__/999999).
 *
 * 🐛 CORREÇÃO: os cinco locais que faziam isto à mão usavam
 * `.replace('__CLIENT_ID__', id).replace('999999', pin)`. A segunda
 * substituição corre DEPOIS de o client_id já estar dentro do URL, por isso um
 * client_id que contivesse "999999" era mutilado e o sentinela ficava por
 * substituir:
 *
 *     '…/__CLIENT_ID__/999999' + client_id 'abc999999def' + pin 4242
 *       ->  '…/abc4242def/999999'   (client_id inexistente, PIN errado)
 *
 * O pedido seguia então para um client_id que não pertence à sessão, o backend
 * recusava-o e a autenticação nunca concluía — sem qualquer erro visível.
 * Substituir primeiro o PIN, enquanto o URL ainda só contém texto nosso,
 * elimina a dependência da ordem.
 *
 * O `encodeURIComponent` protege o caminho de valores com '/' ou '?' e, de
 * caminho, escapa o '$' — que de outra forma o `String.prototype.replace`
 * leria como padrão de substituição ('$&' reinseria o próprio marcador).
 *
 * O sentinela é um número literal (e não um marcador como o `__CLIENT_ID__`)
 * porque a rota declara `<int:pin_id>`: o `url_for` recusa um valor não
 * numérico ao gerar o modelo.
 */
export function buildPinCheckUrl(urlTemplate, clientId, pinId) {
    return String(urlTemplate)
        .replace('999999', encodeURIComponent(String(pinId)))
        .replace('__CLIENT_ID__', encodeURIComponent(String(clientId)));
}
