/**
 * A aba "Sobre": a versão que está rodando e o fuso horário em que ela corre.
 *
 * ⚠️ **Nada aqui se grava**, e é por isso que a aba se declara
 * `data-somente-leitura` no template: o botão "Salvar Alterações" some
 * enquanto ela está aberta.
 *
 * 🛡️ **Tudo o que vem da release é texto de FORA** — o nome e a etiqueta são
 * escritos por quem publica no GitHub e chegam aqui para serem interpolados em
 * `innerHTML`. Passam todos por `escapeHTML`, pela mesma razão que o `username`
 * da auditoria passa: um valor externo dentro de marcação é código à espera de
 * correr na sessão de um administrador.
 */

import { escapeHTML, formatarData, formatarDataHora, showToast } from '../utils.js';
import { i18n } from './config.js';
import * as api from './api.js';

const estado = {
    carregado: false,
    verificando: false,
};

function elemento(id) {
    return document.getElementById(id);
}

function escrever(id, texto) {
    const alvo = elemento(id);
    if (alvo) alvo.textContent = texto;
}

/**
 * O fuso deste navegador, pelo nome (`America/Sao_Paulo`).
 *
 * Não é formatar uma data — é perguntar ao browser em que fuso ele está, que é
 * a metade que falta para a comparação com o painel fazer sentido.
 */
function fusoDoNavegador() {
    try {
        return Intl.DateTimeFormat().resolvedOptions().timeZone || '';
    } catch (e) {
        return '';
    }
}

function cartao(cor, titulo, corpo, acao = '') {
    // ⚠️ As classes são escritas por EXTENSO e nunca montadas com
    // `bg-${cor}-50`: o Tailwind procura nomes de classes literais nos
    // ficheiros, e uma classe montada em tempo de execução nunca chega ao CSS.
    const CORES = {
        verde: 'bg-emerald-50 dark:bg-emerald-900/20 border-emerald-200 dark:border-emerald-800/50 text-emerald-800 dark:text-emerald-300',
        azul: 'bg-blue-50 dark:bg-blue-900/20 border-blue-200 dark:border-blue-800/50 text-blue-800 dark:text-blue-300',
        cinza: 'bg-gray-100 dark:bg-gray-800/50 border-gray-200 dark:border-gray-700/50 text-gray-700 dark:text-gray-300',
        ambar: 'bg-amber-50 dark:bg-amber-900/20 border-amber-200 dark:border-amber-800/50 text-amber-800 dark:text-amber-300',
    };
    return `
        <div class="p-4 rounded-xl border ${CORES[cor] || CORES.cinza}">
            <p class="font-bold text-sm">${titulo}</p>
            <p class="text-sm mt-1">${corpo}</p>
            ${acao}
        </div>`;
}

function ligacao(url, texto) {
    return `<a href="${escapeHTML(url)}" target="_blank" rel="noopener noreferrer"
               class="inline-block mt-3 text-sm font-semibold underline">${escapeHTML(texto)}</a>`;
}

/** O que a aba mostra enquanto espera pela resposta do GitHub. */
function mostrarQueEstaVerificando() {
    const alvo = elemento('sobre-atualizacao');
    if (!alvo) return;
    alvo.innerHTML = cartao(
        'cinza',
        escapeHTML(i18n.sobreVerificando || 'Verificando...'),
        escapeHTML(i18n.sobreVerificandoDetalhe || 'Consultando as versões publicadas no GitHub.'));
}

/**
 * Desenha o resultado da verificação.
 *
 * ⚠️ São TRÊS estados e não dois: atualizado, há versão nova, e **não foi
 * possível verificar**. O terceiro existe porque há painéis sem rede de saída,
 * e mostrá-lo como "está atualizado" seria dizer uma coisa que não se sabe.
 */
function mostrarAtualizacao(dados) {
    const alvo = elemento('sobre-atualizacao');
    if (!alvo) return;

    if (!dados || !dados.disponivel) {
        alvo.innerHTML = cartao(
            'ambar',
            escapeHTML(i18n.sobreSemVerificacao || 'Não foi possível verificar'),
            escapeHTML(i18n.sobreSemVerificacaoDetalhe
                || 'O painel não conseguiu falar com o GitHub. Isso não afeta nada do que ele faz.'),
            ligacao(urlDasReleases(), i18n.sobreVerNoGithub || 'Ver as versões no GitHub'));
        return;
    }

    const versao = escapeHTML(dados.etiqueta || dados.versao || '');
    const publicada = formatarData(dados.publicada_em, { ausente: '' });

    if (dados.ha_atualizacao) {
        const detalhe = publicada
            ? `${escapeHTML(i18n.sobreNovaVersao || 'Há uma versão mais recente:')} <strong>${versao}</strong> (${escapeHTML(publicada)})`
            : `${escapeHTML(i18n.sobreNovaVersao || 'Há uma versão mais recente:')} <strong>${versao}</strong>`;
        alvo.innerHTML = cartao(
            'azul',
            escapeHTML(i18n.sobreAtualizacaoDisponivel || 'Atualização disponível'),
            detalhe,
            ligacao(dados.url || urlDasReleases(), i18n.sobreVerNovidades || 'Ver o que mudou'));
        return;
    }

    alvo.innerHTML = cartao(
        'verde',
        escapeHTML(i18n.sobreAtualizado || 'Você está na versão mais recente'),
        `${escapeHTML(i18n.sobreUltimaPublicada || 'Última publicada:')} <strong>${versao}</strong>`,
        ligacao(dados.url || urlDasReleases(), i18n.sobreVerNoGithub || 'Ver as versões no GitHub'));
}

function urlDasReleases() {
    const alvo = elemento('sobre-repositorio');
    return (alvo && alvo.dataset.url) || 'https://github.com';
}

/** Escreve o bloco do fuso horário, dos dois lados. */
function mostrarFuso(fuso) {
    escrever('sobre-fuso-nome', fuso.nome || '—');
    escrever('sobre-fuso-agora',
        `${i18n.sobreAgoraNoPainel || 'Agora:'} ${fuso.agora || ''} (UTC${fuso.deslocamento ? formatarDeslocamento(fuso.deslocamento) : ''})`);
    escrever('sobre-fuso-origem', fuso.origem === 'TZ'
        ? `${i18n.sobreFusoDaVariavel || 'Definido pela variável TZ:'} ${fuso.tz_ambiente || ''}`
        : (i18n.sobreFusoDoSistema || 'Detectado do sistema (a variável TZ não está definida).'));

    const doNavegador = fusoDoNavegador();
    escrever('sobre-navegador-fuso', doNavegador || '—');
    escrever('sobre-navegador-agora',
        `${i18n.sobreAgoraNoPainel || 'Agora:'} ${formatarDataHora(new Date(), { ausente: '' })}`);

    mostrarAvisoDeFuso(fuso.nome, doNavegador);
}

/** `-0300` -> `-03:00`, que é como se lê um deslocamento. */
function formatarDeslocamento(bruto) {
    const texto = String(bruto);
    return texto.length === 5 ? `${texto.slice(0, 3)}:${texto.slice(3)}` : texto;
}

/**
 * 🐛 **Os dois lados desacordados já custaram três horas em cada vencimento.**
 * O formulário manda a hora escolhida no navegador e o painel lê-a no fuso
 * dele; num contentor sem `TZ` isso é UTC, e "23:59 no Brasil" ficava gravado
 * como 20:59 — a deslizar outras três a cada gravação. Hoje o navegador manda
 * o deslocamento junto e o caso está fechado, mas continua a valer a pena
 * dizer que os relógios não batem certo: as tarefas (a hora universal de
 * expiração, os avisos diários) correm no fuso do PAINEL.
 */
function mostrarAvisoDeFuso(noPainel, noNavegador) {
    const alvo = elemento('sobre-fuso-aviso');
    if (!alvo) return;

    if (!noPainel || !noNavegador || noPainel === noNavegador) {
        alvo.innerHTML = '';
        return;
    }

    alvo.innerHTML = cartao(
        'ambar',
        escapeHTML(i18n.sobreFusosDiferentes || 'O painel e este navegador estão em fusos diferentes'),
        escapeHTML(i18n.sobreFusosDiferentesDetalhe
            || 'As tarefas automáticas e a hora universal de expiração seguem o fuso do painel. Se quiser que os dois coincidam, defina a variável TZ no docker-compose.'));
}

/** Pede a última release e desenha o resultado. */
async function verificarAtualizacao(forcar = false) {
    if (estado.verificando) return;
    estado.verificando = true;

    const botao = elemento('sobre-verificar');
    if (botao) botao.disabled = true;
    mostrarQueEstaVerificando();

    try {
        mostrarAtualizacao(await api.getLatestRelease(forcar));
    } catch (error) {
        // A rota responde 200 mesmo sem conseguir falar com o GitHub, por isso
        // chegar aqui é uma falha do PAINEL — e essa merece um toast.
        mostrarAtualizacao(null);
        showToast(escapeHTML(error.message), 'error');
    } finally {
        estado.verificando = false;
        if (botao) botao.disabled = false;
    }
}

/**
 * Carrega a aba.
 *
 * ⚠️ A versão e o fuso vêm de uma rota que não fala com ninguém de fora, e são
 * escritos primeiro: mesmo sem rede, a aba abre com o que interessa. A release
 * vem a seguir, por sua conta.
 */
export async function carregarSobre(recarregar = false) {
    if (estado.carregado && !recarregar) return;

    try {
        const dados = await api.getAbout();
        estado.carregado = true;
        escrever('sobre-versao', dados.versao || '—');

        const repositorio = elemento('sobre-repositorio');
        if (repositorio) {
            repositorio.textContent = dados.repositorio || '';
            repositorio.dataset.url = dados.url_das_releases || '';
        }

        mostrarFuso(dados.fuso || {});
    } catch (error) {
        showToast(escapeHTML(error.message), 'error');
        return;
    }

    verificarAtualizacao(false);
}

export function ligarOuvintesDoSobre() {
    const botao = elemento('sobre-verificar');
    // ⚠️ `forcar` salta a cache de seis horas do servidor: quem clica no botão
    // quer saber AGORA, e não o que se soube de manhã.
    if (botao) botao.addEventListener('click', () => verificarAtualizacao(true));
}
