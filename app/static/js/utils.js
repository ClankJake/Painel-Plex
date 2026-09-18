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
 * O idioma em que a interface mostra datas e números.
 *
 * ⚠️ **Vem do `<html lang>` e não do navegador.** Quem usa o painel em
 * português com um navegador em inglês via `09/15/2026, 08:48 PM` — o mês
 * antes do dia, que num painel brasileiro se lê ao contrário do que diz. O
 * `navigator.language` fica como segunda escolha e o 'pt-BR' como terceira,
 * porque a página pode não declarar língua nenhuma.
 *
 * ⚠️ É lido A CADA CHAMADA, e não guardado num `const` de módulo: o seletor de
 * idioma recarrega a página, mas um módulo que leia o `lang` no momento do
 * import corre o risco de o fazer antes de o atributo existir.
 */
function idiomaDaInterface() {
    return document.documentElement.lang || navigator.language || 'pt-BR';
}

/**
 * Uma data como "dd/mm/aaaa hh:mm", no idioma da interface.
 *
 * 📌 **Esta é a porta única.** Havia três cópias disto — uma em
 * `dashboard_modules/formatters.js` e duas em `users_modules` — e elas não
 * concordavam: a do dashboard pedia dia/mês/ano e hora:minuto explícitos, as
 * dos usuários faziam `toLocaleString()` e saíam com vírgula e SEGUNDOS
 * (`15/09/2026, 20:48:33`). A mesma data aparecia de duas maneiras conforme a
 * página. Também não concordavam no que fazer com um valor em falta: uma
 * devolvia texto vazio, outra "Não disponível", outra rebentava.
 *
 * Absorve as três diferenças que eram reais:
 *   • aceita um `Date`, um texto ISO ou um número — as chamadas tinham as três;
 *   • `ausente` é o que sai quando não há data (vazio, nulo ou inválido), para
 *     quem precisa de escrever "Não disponível" em vez de um espaço em branco;
 *   • nunca levanta: uma data estragada não pode derrubar a linha da tabela
 *     onde ela aparece.
 */
export function formatarDataHora(valor, { ausente = '' } = {}) {
    if (valor === null || valor === undefined || valor === '') return ausente;

    const data = valor instanceof Date ? valor : new Date(valor);
    if (Number.isNaN(data.getTime())) return ausente;

    const idioma = idiomaDaInterface();
    const dia = data.toLocaleDateString(idioma, { day: '2-digit', month: '2-digit', year: 'numeric' });
    const hora = data.toLocaleTimeString(idioma, { hour: '2-digit', minute: '2-digit' });
    return `${dia} ${hora}`;
}

/**
 * Só o dia, como "dd/mm/aaaa". Segue as mesmas regras da `formatarDataHora`.
 */
export function formatarData(valor, { ausente = '' } = {}) {
    if (valor === null || valor === undefined || valor === '') return ausente;

    const data = valor instanceof Date ? valor : new Date(valor);
    if (Number.isNaN(data.getTime())) return ausente;

    return data.toLocaleDateString(idiomaDaInterface(),
        { day: '2-digit', month: '2-digit', year: 'numeric' });
}

/**
 * Escapa texto para inclusão segura dentro de uma string de HTML.
 * Usar sempre que um valor de origem externa (API, utilizador, nome de ficheiro,
 * mensagem de erro) tiver de ser interpolado num template literal com HTML.
 *
 * É uma declaração de função (e não uma const) de propósito: o hoisting permite
 * que `sanitizeHTML`, definida no topo deste ficheiro, a utilize.
 */
/**
 * =====================================================================
 * O TELEFONE E O CÓDIGO DO PAÍS
 * =====================================================================
 *
 * 📌 Vive aqui porque são DOIS os sítios que o pedem — a "Minha Conta" e o fim
 * do resgate de um convite — e a regra é a mesma. Uma segunda cópia divergiria
 * da primeira no primeiro ajuste, que foi o que aconteceu às três cópias do
 * `formatDateTime` e às quatro do escapador.
 *
 * O painel guarda o telefone **só com dígitos** (`_normalizar_telefone`, em
 * `models.py`, e `validar_telefone`, nos schemas): o que está na base de dados
 * é `5521999999999`, sem o `+` e sem separadores.
 *
 * 🐛 REGRESSÃO REAL: a caixa da "Minha Conta" comparava o número guardado com o
 * código do país TAL COMO ele aparece na lista — `'5521999999999'.startsWith('+55')`
 * —, que é sempre falso porque o `+` nunca chega a ser gravado. Nenhum país
 * correspondia, o ramo de recurso punha o número INTEIRO no campo nacional, e
 * gravar outra vez escrevia `555521999999999` no perfil: 15 dígitos, dentro do
 * limite do `validar_telefone`, aceite sem uma queixa. Como o destinatário do
 * WhatsApp é `{phone_number}@s.whatsapp.net`, a pessoa deixava de receber
 * qualquer aviso e o painel continuava a dizer que tinha enviado.
 */
export const PAISES = [
    { name: 'Brasil', code: '55' }, { name: 'Portugal', code: '351' },
    { name: 'Angola', code: '244' }, { name: 'Moçambique', code: '258' },
    { name: 'Cabo Verde', code: '238' }, { name: 'EUA/Canadá', code: '1' },
    { name: 'Reino Unido', code: '44' }, { name: 'Espanha', code: '34' },
    { name: 'França', code: '33' }, { name: 'Alemanha', code: '49' }
];

export const soDigitos = (valor) => String(valor ?? '').replace(/\D/g, '');

/**
 * O número tem o formato NACIONAL esperado (logo, não traz código de país)?
 *
 * ⚠️ Nem todo o número guardado TEM DDI: o campo do administrador, na página de
 * utilizadores, é uma caixa de texto solta, e o `normalize_phone` do
 * `notifier_manager` só acrescenta o código no momento do ENVIO — não reescreve
 * o perfil. Por isso `11999999999` está lá tal e qual, e cortar-lhe os
 * primeiros dígitos por parecerem um código de país ("+1") daria um número
 * truncado com a bandeira errada.
 *
 * A heurística é a MESMA do backend, de propósito: para o Brasil (55) são 10
 * dígitos (fixo com DDD) ou 11 (celular, sempre com o 9 na terceira posição),
 * com o DDD entre 11 e 99.
 */
export function temFormatoNacional(digitos, ddiPadrao) {
    if (!ddiPadrao) return false;
    if (digitos.length !== 10 && digitos.length !== 11) return false;
    const ddd = Number(digitos.slice(0, 2));
    if (!(ddd >= 11 && ddd <= 99)) return false;
    if (ddiPadrao === '55' && digitos.length === 11 && digitos[2] !== '9') return false;
    return true;
}

/**
 * Separa o número guardado em (código do país, parte nacional), para os dois
 * campos que a pessoa vê. Devolve sempre os dois — a caixa nunca fica por
 * escolher.
 */
export function separarCodigoDoPais(numeroGuardado, codigosConhecidos, ddiPadrao) {
    const digitos = soDigitos(numeroGuardado);
    if (!digitos) return { codigo: ddiPadrao, numero: '' };

    if (temFormatoNacional(digitos, ddiPadrao)) {
        return { codigo: ddiPadrao, numero: digitos };
    }

    // ⚠️ Do prefixo MAIS LONGO para o mais curto: `351` tem de ser testado
    // antes de `1` e de `44`, ou o país errado ganha e o número fica truncado.
    const candidatos = codigosConhecidos.slice().sort((a, b) => b.length - a.length);
    for (const ddi of candidatos) {
        if (digitos.startsWith(ddi) && digitos.length > ddi.length) {
            return { codigo: ddi, numero: digitos.slice(ddi.length) };
        }
    }

    // Não reconhecido: fica inteiro no campo, com o DDI padrão à frente. É o
    // comportamento menos destrutivo — não se corta o que não se percebeu.
    return { codigo: ddiPadrao, numero: digitos };
}

/**
 * A lista de países para a caixa, com o DDI padrão do painel garantidamente lá.
 *
 * ⚠️ Sem o acrescentar, um `WHATSAPP_DEFAULT_COUNTRY_CODE` fora desta lista
 * curta fazia o `select.value = ...` não encontrar a opção, FALHAR EM SILÊNCIO
 * e a caixa ficar no primeiro país — o número gravado ganhava o DDI de outro.
 */
export function paisesComOPadrao(ddiPadrao) {
    const paises = PAISES.slice();
    if (ddiPadrao && !paises.some(p => p.code === ddiPadrao)) {
        paises.unshift({ name: `+${ddiPadrao}`, code: ddiPadrao });
    }
    return paises;
}

/**
 * Junta o que está nos dois campos no número que se grava.
 *
 * 🐛 Escrever o número já com o DDI não pode dar o DDI a dobrar — era isso que
 * acontecia a quem gravasse a "Minha Conta" com o campo como ela o mostrava
 * antes da correção, e o resultado passava no limite de 15 dígitos, portanto
 * sem erro nenhum a avisar. ⚠️ `temFormatoNacional` é o travão: um celular de
 * Santa Maria (`55999999999`) começa por "55" e ali o 55 é o DDD.
 */
export function juntarTelefone(codigoDoPais, textoDoCampo, ddiPadrao) {
    const ddi = soDigitos(codigoDoPais) || soDigitos(ddiPadrao);
    let nacional = soDigitos(textoDoCampo);
    if (ddi && nacional.startsWith(ddi) && !temFormatoNacional(nacional, ddi)) {
        nacional = nacional.slice(ddi.length);
    }
    return { ddi, nacional, completo: nacional ? `${ddi}${nacional}` : '' };
}

/**
 * Acrescenta o DESLOCAMENTO do navegador a uma data-hora local.
 * `'2026-09-05T23:59'` → `'2026-09-05T23:59:00-03:00'`.
 *
 * 🐛 **Sem ele, quem lia a data era o fuso do SERVIDOR.** O formulário de
 * vencimento manda a hora de parede que a pessoa escolheu, e o
 * `datetime.fromisoformat` do painel devolvia uma data INGÉNUA: o
 * `astimezone(utc)` que vinha a seguir assume o fuso do sistema. Num contentor
 * sem `TZ` definido — que é o padrão do Docker — isso é UTC, e um
 * administrador no Brasil que escolhesse 23:59 ficava com um vencimento às
 * 20:59 dele.
 *
 * E não parava aí: ao reabrir, o campo mostrava as 20:59 (o `new Date` lê o
 * `+00:00` e converte para o fuso de quem olha), por isso gravar outra vez sem
 * tocar em nada escrevia 17:59. **Três horas por gravação, sempre no mesmo
 * sentido.** Com o deslocamento à frente o instante é inequívoco, e deixa de
 * depender de o `TZ` do contentor coincidir com o de quem está a clicar.
 *
 * ⚠️ O deslocamento é o que estava em vigor NAQUELA data, não o de hoje — é
 * por isso que se pergunta ao `Date` construído com ela, e não ao `new Date()`
 * de agora: onde há horário de verão, os dois não são o mesmo.
 */
export function comDeslocamentoLocal(dataHoraLocal) {
    if (!dataHoraLocal) return dataHoraLocal;

    // Sem 'Z' e sem deslocamento, o navegador lê-a como hora LOCAL — que é
    // exatamente o que ela é: o que a pessoa escolheu no relógio dela.
    const momento = new Date(dataHoraLocal);
    if (Number.isNaN(momento.getTime())) return dataHoraLocal;

    // ⚠️ `getTimezoneOffset()` devolve os minutos a SOMAR para chegar a UTC:
    // no Brasil (UTC-3) são +180. O sinal que se escreve é o contrário.
    const minutos = -momento.getTimezoneOffset();
    const sinal = minutos < 0 ? '-' : '+';
    const absoluto = Math.abs(minutos);
    const horas = String(Math.floor(absoluto / 60)).padStart(2, '0');
    const resto = String(absoluto % 60).padStart(2, '0');

    // `YYYY-MM-DDTHH:MM` são 16 caracteres e não trazem segundos.
    const segundos = dataHoraLocal.length === 16 ? ':00' : '';
    return `${dataHoraLocal}${segundos}${sinal}${horas}:${resto}`;
}


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


/**
 * A chave com que um `data-*` chega ao `dataset`, convertida para camelCase.
 *
 * 🐛 O browser só come o '-' quando ele é seguido de uma LETRA minúscula:
 * `data-i18n-step-local-1` chega ao dataset como `i18nStepLocal-1`, com o
 * traço intacto. Quem depois cortava o prefixo e mais nada ficava com a chave
 * `stepLocal-1` — e o consumidor, que pede `stepLocal1`, recebia `undefined`.
 * Era isso que a página de convite mostrava, escrito por extenso, no "Como
 * começar" de um servidor de contas locais.
 *
 * @param {string} chaveDoDataset a propriedade tal como o browser a criou
 * @param {number} prefixo quantas letras do prefixo saltar ('i18n' = 4)
 */
export function chaveEmCamelCase(chaveDoDataset, prefixo) {
    return chaveDoDataset.charAt(prefixo).toLowerCase()
        + chaveDoDataset.slice(prefixo + 1).replace(/-(\w)/g, (_, letra) => letra.toUpperCase());
}


/**
 * Lê a configuração que o backend injeta no `<script>` de uma página.
 *
 * ⚠️ Isto estava escrito à mão em DOZE ficheiros, e as cópias não concordavam:
 * cinco regras diferentes para decidir o nome da chave. `data-url-x` numa
 * página, `data-urls-x` noutra, `data-x-url` numa terceira, e a de estatísticas
 * a cortar o sufixo `Url` em vez de um prefixo. Nenhuma estava errada — mas
 * quem trabalhasse em duas páginas tinha de se lembrar de qual era qual, e uma
 * chave que não resolve não dá erro: dá um `fetch` para `undefined`.
 *
 * Aqui a regra é UMA, e aceita os prefixos que já existiam nos templates, para
 * nenhum `data-*` ter de ser renomeado:
 *
 *   data-i18n-foo-bar   → i18n.fooBar
 *   data-config-foo     → config.foo      (a página de convite)
 *   data-urls-foo       → urls.foo
 *   data-url-foo        → urls.foo
 *   data-foo-url        → urls.fooUrl     (qualquer outra chave, tal e qual)
 *
 * ⚠️ A ordem importa: `urls` é testado ANTES de `url`, senão `data-urls-foo`
 * entrava pelo ramo errado e ficava com um 's' a mais no nome.
 *
 * O `dataset` cru vai de volta porque há páginas que leem campos avulsos
 * (`currentUser`, `wrappedUrl`) que não são nem texto nem endereço.
 *
 * @param {string} idDoScript o id do `<script>` (ex.: 'users-script')
 * @returns {{i18n: Object, urls: Object, config: Object, dataset: Object}}
 */
export function lerConfiguracaoDoScript(idDoScript) {
    const i18n = {};
    const urls = {};
    const config = {};

    const tag = document.getElementById(idDoScript);
    // Uma página sem a tag não é um erro a levantar aqui: os dicionários ficam
    // vazios e cada consumidor já trata a chave em falta à sua maneira.
    if (!tag) return { i18n, urls, config, dataset: {} };

    for (const chave in tag.dataset) {
        const valor = tag.dataset[chave];
        if (chave.startsWith('i18n') && chave.length > 4) {
            i18n[chaveEmCamelCase(chave, 4)] = valor;
        } else if (chave.startsWith('config') && chave.length > 6) {
            config[chaveEmCamelCase(chave, 6)] = valor;
        } else if (chave.startsWith('urls') && chave.length > 4) {
            urls[chaveEmCamelCase(chave, 4)] = valor;
        } else if (chave.startsWith('url') && chave.length > 3) {
            urls[chaveEmCamelCase(chave, 3)] = valor;
        } else {
            urls[chave] = valor;
        }
    }

    return { i18n, urls, config, dataset: tag.dataset };
}
