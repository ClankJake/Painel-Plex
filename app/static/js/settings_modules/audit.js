/**
 * A aba de Auditoria: quem mudou o quê, quando e de onde.
 *
 * ⚠️ **É a irmã da aba de Logs, e a diferença é deliberada**: aqui não há
 * botão de limpar. O `app.log` roda sozinho e tem um botão que o trunca; uma
 * trilha que a pessoa auditada apaga com um clique não responde à única
 * pergunta para que existe. Esta aba só lê.
 */

import { escapeHTML, formatarDataHora, showToast } from '../utils.js';
import { i18n } from './config.js';
import * as api from './api.js';

// Quantos registros de cada vez. 25 é o que cabe num ecrã sem pedir rolagem
// infinita; o resto vem pelo "Carregar mais".
const POR_PAGINA = 25;

// Estado da aba. Fica aqui e não no DOM: o "Carregar mais" precisa de saber
// onde ficou, e o filtro precisa de reiniciar a contagem.
const estado = {
    desvio: 0,
    acao: '',
    carregando: false,
    filtroPreenchido: false,
};

/**
 * O rótulo de cada ação, na língua da interface.
 *
 * ⚠️ A CHAVE é um identificador interno e estável (é por ela que se filtra a
 * auditoria de meses atrás); o VALOR é o que a pessoa lê. Quando falta um
 * rótulo — uma ação de uma versão mais recente, ou de uma mais antiga que já
 * não existe — mostra-se a chave crua em vez de esconder a linha: uma
 * auditoria com buracos é pior do que uma auditoria feia.
 */
const ROTULOS = {
    'definicoes.gravar': 'acaoDefinicoesGravar',
    'log.limpar': 'acaoLogLimpar',
    'backup.restaurar': 'acaoBackupRestaurar',
    'chave_api.regenerar': 'acaoChaveApiRegenerar',
    'utilizador.bloquear': 'acaoUsuarioBloquear',
    'utilizador.desbloquear': 'acaoUsuarioDesbloquear',
    'utilizador.remover': 'acaoUsuarioRemover',
    'utilizador.apagar_permanentemente': 'acaoUsuarioApagar',
    'utilizador.editar_perfil': 'acaoUsuarioEditar',
    'utilizador.limite_de_telas': 'acaoUsuarioTelas',
    'utilizador.limite_de_telas_global': 'acaoUsuarioTelasGlobal',
    'utilizador.acesso_pedidos': 'acaoUsuarioPedidos',
    'pagamento.adicionar_manual': 'acaoPagamentoManual',
    'pagamento.confirmar': 'acaoPagamentoConfirmar',
    'pagamento.apagar': 'acaoPagamentoApagar',
    'pagamento.restaurar': 'acaoPagamentoRestaurar',
    'cupao.criar': 'acaoCupomCriar',
    'cupao.apagar': 'acaoCupomApagar',
    'cupao.alternar_estado': 'acaoCupomEstado',
    'convite.criar': 'acaoConviteCriar',
    'convite.apagar': 'acaoConviteApagar',
    'convite.reativar': 'acaoConviteReativar',
    'convite.resgatar': 'acaoConviteResgatar',
};

// A cor e o ícone vêm da FAMÍLIA (o que vem antes do ponto), para uma ação
// nova herdar o aspeto das irmãs sem ninguém lhe tocar aqui.
const FAMILIAS = {
    definicoes: { cor: 'amber', icone: 'M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z' },
    utilizador: { cor: 'indigo', icone: 'M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z' },
    pagamento: { cor: 'emerald', icone: 'M17 9V7a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2m2 4h10a2 2 0 002-2v-6a2 2 0 00-2-2H9a2 2 0 00-2 2v6a2 2 0 002 2zm7-5a2 2 0 11-4 0 2 2 0 014 0z' },
    cupao: { cor: 'pink', icone: 'M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A1.994 1.994 0 013 12V7a4 4 0 014-4z' },
    backup: { cor: 'sky', icone: 'M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4' },
    chave_api: { cor: 'violet', icone: 'M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z' },
    convite: { cor: 'teal', icone: 'M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z' },
    log: { cor: 'slate', icone: 'M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z' },
};

const FAMILIA_PADRAO = FAMILIAS.log;

// ⚠️ As classes do Tailwind são escritas POR EXTENSO e não montadas com
// `bg-${cor}-100`: o compilador lê os templates à procura de nomes de classes
// LITERAIS, e uma classe montada em tempo de execução nunca chega ao CSS final
// — o ícone ficava sem cor nenhuma e ninguém percebia porquê.
const CORES = {
    amber: 'bg-amber-100 text-amber-600 dark:bg-amber-900/40 dark:text-amber-400',
    indigo: 'bg-indigo-100 text-indigo-600 dark:bg-indigo-900/40 dark:text-indigo-400',
    emerald: 'bg-emerald-100 text-emerald-600 dark:bg-emerald-900/40 dark:text-emerald-400',
    pink: 'bg-pink-100 text-pink-600 dark:bg-pink-900/40 dark:text-pink-400',
    sky: 'bg-sky-100 text-sky-600 dark:bg-sky-900/40 dark:text-sky-400',
    violet: 'bg-violet-100 text-violet-600 dark:bg-violet-900/40 dark:text-violet-400',
    slate: 'bg-slate-100 text-slate-600 dark:bg-slate-700/60 dark:text-slate-300',
    teal: 'bg-teal-100 text-teal-600 dark:bg-teal-900/40 dark:text-teal-400',
};

// Ações que tiram alguma coisa a alguém. Ganham uma marca vermelha para
// saltarem à vista numa lista longa — é por elas que se costuma procurar.
const DESTRUTIVAS = new Set([
    'utilizador.bloquear',
    'utilizador.remover',
    'utilizador.apagar_permanentemente',
    'pagamento.apagar',
    'cupao.apagar',
    'convite.apagar',
    'log.limpar',
    'backup.restaurar',
    'chave_api.regenerar',
]);


function elemento(id) {
    return document.getElementById(id);
}

function rotuloDaAcao(acao) {
    const chave = ROTULOS[acao];
    return (chave && i18n[chave]) || acao;
}

function familiaDa(acao) {
    return FAMILIAS[String(acao).split('.')[0]] || FAMILIA_PADRAO;
}

/**
 * A data guardada é UTC "nua" (sem sufixo de fuso), como todas as colunas
 * `DateTime` desta aplicação.
 *
 * ⚠️ O 'Z' NÃO é decoração: sem ele o `new Date` lê a string como hora LOCAL, e
 * no Brasil cada registro aparecia três horas adiantado. É a mesma convenção
 * que a Auditoria de Cortes já usa no painel principal.
 */
function comoData(iso) {
    return iso ? new Date(iso + 'Z') : null;
}

/** Um valor qualquer, pronto para ser lido por uma pessoa. */
function valorLegivel(valor) {
    if (valor === null || valor === undefined || valor === '') {
        return i18n.auditVazio || '(vazio)';
    }
    if (valor === true) return i18n.auditSim || 'Sim';
    if (valor === false) return i18n.auditNao || 'Não';
    if (typeof valor === 'object') return JSON.stringify(valor);
    return String(valor);
}

function ehParDeMudanca(valor) {
    return valor && typeof valor === 'object' && !Array.isArray(valor)
        && ('antes' in valor || 'depois' in valor);
}

/**
 * Reduz os `detalhes` gravados a linhas de (campo, antes, depois).
 *
 * Os detalhes não têm todos a mesma forma — uns são um mapa de campos alterados
 * (`{campos: {CHAVE: {antes, depois}}}`), outros são um retrato do que foi
 * apagado (`{pagamento: {...}}`), outros um par solto (`{antes, depois}`). Em
 * vez de um ramo por formato, achata-se tudo para a mesma tabela: um formato
 * novo aparece legível sem ninguém vir aqui acrescentar um caso.
 */
function linhasDosDetalhes(detalhes) {
    if (!detalhes || typeof detalhes !== 'object') return [];

    // O par solto, sem nome de campo à volta.
    if (ehParDeMudanca(detalhes)) {
        return [{ campo: '', antes: detalhes.antes, depois: detalhes.depois }];
    }

    const linhas = [];
    for (const [chave, valor] of Object.entries(detalhes)) {
        if (ehParDeMudanca(valor)) {
            linhas.push({ campo: chave, antes: valor.antes, depois: valor.depois });
        } else if (valor && typeof valor === 'object' && !Array.isArray(valor)) {
            // Um nível de aninhamento ('campos', 'perfil', 'pagamento'...).
            for (const [subChave, subValor] of Object.entries(valor)) {
                if (ehParDeMudanca(subValor)) {
                    linhas.push({ campo: subChave, antes: subValor.antes, depois: subValor.depois });
                } else {
                    linhas.push({ campo: subChave, valor: subValor });
                }
            }
        } else {
            linhas.push({ campo: chave, valor: valor });
        }
    }
    return linhas;
}

function tabelaDeDetalhes(linhas) {
    const setaAntes = '<span class="text-gray-400 dark:text-gray-500 line-through">';
    const corpo = linhas.map(linha => {
        const campo = linha.campo
            ? `<td class="py-1 pr-3 align-top font-mono text-[11px] text-gray-500 dark:text-gray-400 whitespace-nowrap">${escapeHTML(linha.campo)}</td>`
            : '<td class="py-1 pr-3"></td>';

        if ('valor' in linha) {
            return `<tr>${campo}<td class="py-1 align-top text-gray-700 dark:text-gray-300 break-all">${escapeHTML(valorLegivel(linha.valor))}</td></tr>`;
        }
        return `<tr>${campo}<td class="py-1 align-top break-all">
            ${setaAntes}${escapeHTML(valorLegivel(linha.antes))}</span>
            <span class="mx-1.5 text-gray-400 dark:text-gray-500">&rarr;</span>
            <span class="font-semibold text-gray-800 dark:text-gray-100">${escapeHTML(valorLegivel(linha.depois))}</span>
        </td></tr>`;
    }).join('');

    return `<table class="w-full text-xs mt-2 border-t border-gray-200 dark:border-gray-700 pt-2"><tbody>${corpo}</tbody></table>`;
}

function cartaoDeRegistro(registo) {
    const familia = familiaDa(registo.acao);
    const cor = CORES[familia.cor] || CORES.slate;
    const data = comoData(registo.timestamp);
    const linhas = linhasDosDetalhes(registo.detalhes);
    const destrutiva = DESTRUTIVAS.has(registo.acao);

    // Quem agiu. Uma tarefa de fundo ou um webhook de gateway não têm autor, e
    // a coluna vazia diz a verdade: isto não partiu de ninguém a clicar.
    const autor = registo.ator
        ? `<strong class="text-gray-800 dark:text-gray-100">${escapeHTML(registo.ator)}</strong>`
        : `<span class="italic text-gray-500 dark:text-gray-400">${escapeHTML(i18n.auditSemAutor || 'Sistema')}</span>`;

    const alvo = registo.alvo_id
        ? `<span class="text-gray-500 dark:text-gray-400"> &middot; </span><span class="font-mono text-xs text-gray-600 dark:text-gray-300">${escapeHTML(registo.alvo_id)}</span>`
        : '';

    const ip = registo.endereco_ip
        ? `<span class="text-gray-400 dark:text-gray-500">&middot;</span><span class="font-mono">${escapeHTML(registo.endereco_ip)}</span>`
        : '';

    const detalhes = linhas.length
        ? `<details class="mt-2 group">
               <summary class="cursor-pointer text-xs font-semibold text-yellow-700 dark:text-yellow-500 hover:underline select-none">
                   ${escapeHTML(i18n.auditVerDetalhes || 'Ver detalhes')} (${linhas.length})
               </summary>
               ${tabelaDeDetalhes(linhas)}
           </details>`
        : '';

    return `
    <div class="flex items-start gap-3 p-3 rounded-lg bg-white dark:bg-gray-800/60 border border-gray-200 dark:border-gray-700/50">
        <span class="p-2 ${cor} rounded-full flex-shrink-0">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="${familia.icone}"></path></svg>
        </span>
        <div class="min-w-0 flex-1">
            <p class="text-sm text-gray-700 dark:text-gray-300 break-words">
                ${autor}
                <span class="text-gray-500 dark:text-gray-400">&mdash;</span>
                <span class="${destrutiva ? 'font-semibold text-red-600 dark:text-red-400' : 'font-semibold'}">${escapeHTML(rotuloDaAcao(registo.acao))}</span>
                ${alvo}
            </p>
            <p class="mt-1 flex flex-wrap items-center gap-x-1.5 text-[11px] font-medium text-gray-500 dark:text-gray-400">
                <span class="whitespace-nowrap">${escapeHTML(formatarDataHora(data))}</span>
                ${ip}
            </p>
            ${detalhes}
        </div>
    </div>`;
}

function vazio() {
    return `<p class="text-center text-sm text-gray-500 dark:text-gray-400 py-10">${escapeHTML(i18n.auditVazioLista || 'Nenhuma ação registrada ainda.')}</p>`;
}

function preencherFiltro(acoes) {
    const filtro = elemento('audit-action-filter');
    if (!filtro || estado.filtroPreenchido) return;

    // A primeira opção ("Todas as ações") vem do template e fica.
    for (const acao of acoes || []) {
        const opcao = document.createElement('option');
        opcao.value = acao;
        opcao.textContent = rotuloDaAcao(acao);
        filtro.appendChild(opcao);
    }
    estado.filtroPreenchido = true;
}

/**
 * Busca uma página e desenha-a.
 * @param {boolean} reiniciar começa do princípio (troca de filtro, atualizar)
 */
export async function carregar(reiniciar = true) {
    const lista = elemento('audit-list');
    if (!lista || estado.carregando) return;

    estado.carregando = true;
    if (reiniciar) estado.desvio = 0;

    const maisButton = elemento('audit-load-more');
    if (maisButton) maisButton.classList.add('hidden');

    try {
        const resposta = await api.getAuditLogs({
            limit: POR_PAGINA,
            offset: estado.desvio,
            action: estado.acao,
        });

        preencherFiltro(resposta.acoes);

        const cartoes = (resposta.logs || []).map(cartaoDeRegistro).join('');
        if (reiniciar) {
            lista.innerHTML = cartoes || vazio();
        } else {
            lista.insertAdjacentHTML('beforeend', cartoes);
        }

        estado.desvio += (resposta.logs || []).length;

        const contagem = elemento('audit-count');
        if (contagem) {
            const modelo = i18n.auditContagem || '{mostrados} de {total}';
            contagem.textContent = modelo
                .replace('{mostrados}', estado.desvio)
                .replace('{total}', resposta.total ?? estado.desvio);
        }

        if (maisButton) maisButton.classList.toggle('hidden', !resposta.ha_mais);
    } catch (erro) {
        // 🐛 A aba de Configurações carrega várias coisas ao abrir. Deixar o
        // erro subir daqui derrubava a página inteira, como já aconteceu na
        // "Minha Conta" com o `Promise.all`: a auditoria é secundária e a sua
        // falha tem de ficar contida nela.
        console.error('Falha ao carregar a auditoria:', erro);
        lista.innerHTML = `<p class="text-center text-sm text-red-600 dark:text-red-400 py-10">${escapeHTML(i18n.auditFalha || 'Não foi possível carregar a auditoria.')}</p>`;
        showToast(i18n.auditFalha || 'Não foi possível carregar a auditoria.', 'error');
    } finally {
        estado.carregando = false;
    }
}

export function initAuditListeners() {
    elemento('audit-refresh')?.addEventListener('click', () => carregar(true));

    elemento('audit-load-more')?.addEventListener('click', () => carregar(false));

    elemento('audit-action-filter')?.addEventListener('change', (evento) => {
        estado.acao = evento.target.value;
        carregar(true);
    });
}
