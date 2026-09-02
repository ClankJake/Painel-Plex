// Relógio de reprodução das "Sessões Ativas".
//
// O Plex não atualiza o `viewOffset` de forma contínua: o leitor só reporta a
// sua posição de tempos a tempos (na ordem dos 10 segundos) e a API devolve
// sempre a última posição reportada. Quando o painel copiava esse valor a cada
// sondagem, o cronómetro andava para trás alguns segundos ao receber uma
// leitura antiga e voltava a saltar para a frente na leitura seguinte.
//
// Aqui mantemos um relógio local por sessão que:
//   1. corre sozinho a partir de um relógio monotónico (imune a acertos de
//      hora do sistema);
//   2. ignora leituras do servidor que já sejam mais antigas do que aquilo que
//      ele próprio já sabe (o valor do Plex é sempre um limite inferior da
//      posição real);
//   3. quando aceita uma leitura nova, aproxima-se dela acelerando ou
//      abrandando ligeiramente em vez de dar um salto visível;
//   4. só sincroniza de golpe quando a diferença é grande o suficiente para
//      ser um avanço/recuo real feito pelo utilizador.

import { state } from './config.js';
import { formatTime } from './formatters.js';

// Cadência do relógio local. É propositadamente mais rápida do que um segundo:
// com um intervalo de 1000 ms exatos, o desvio acumulado do `setInterval` fazia
// com que houvesse segundos repetidos ou saltados no ecrã.
const TICK_MS = 250;

// Diferença a partir da qual assumimos um salto real na reprodução (o
// utilizador avançou ou recuou) e não simples latência: nesse caso o relógio
// sincroniza imediatamente.
const SEEK_THRESHOLD_MS = 10000;

// Correção suave: em reprodução o relógio local pode andar até 30% mais
// depressa ou mais devagar para absorver a diferença sem solavancos.
const MAX_RATE_ADJUST = 0.3;
const CORRECTION_WINDOW_MS = 5000;

// Em pausa não há tempo a correr, por isso a diferença é absorvida a um ritmo
// fixo: meio segundo por cada segundo real.
const PAUSED_CORRECTION_RATE = 0.5;

// Se o Plex repetir exatamente a mesma leitura durante mais do que isto sem que
// o estado mude, o leitor está encravado (e não apenas a reportar devagar): a
// partir daí deixamos de confiar no relógio local. É folgado de propósito —
// nenhum leitor real demora tanto entre reportes.
const STALE_SAMPLE_LIMIT_MS = 90000;

let tickerId = null;

const monotonicNow = () => (
    typeof performance !== 'undefined' && typeof performance.now === 'function'
        ? performance.now()
        : Date.now()
);

const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

const toMillis = (value) => (Number.isFinite(value) && value > 0 ? value : 0);

const isPlaying = (streamState) => streamState === 'playing';

/**
 * Assinatura do conteúdo em reprodução. Serve para detetar que a mesma sessão
 * passou a reproduzir outra coisa (o episódio seguinte, por exemplo), caso em
 * que o cartão e o relógio têm de recomeçar do zero. Usa apenas o que está
 * escrito no cartão — a duração fica de fora de propósito, para que uma leitura
 * momentaneamente a zero não obrigue a reconstruir tudo.
 * @param {object} session - A sessão vinda da API.
 * @returns {string} - Identificador estável do conteúdo.
 */
export function streamMediaSignature(session) {
    return `${session.title}|${session.subtitle}`;
}

function _createTimer(session, now) {
    const duration = toMillis(session.duration);
    const offset = clamp(toMillis(session.view_offset), 0, duration || Number.MAX_SAFE_INTEGER);

    return {
        duration,
        state: session.state,
        signature: streamMediaSignature(session),
        // Última leitura aceite do servidor e o instante em que a aceitámos.
        serverOffset: offset,
        serverClock: now,
        // Último valor visto (aceite ou não) e desde quando, para distinguir uma
        // medição nova de uma repetição da anterior.
        lastSample: offset,
        lastSampleClock: now,
        // Posição efetivamente mostrada, que persegue a leitura do servidor.
        displayOffset: offset,
        displayClock: now,
        renderedText: null,
        renderedWidth: null
    };
}

/**
 * Posição que o servidor implica neste momento: a última leitura aceite mais o
 * tempo que passou desde então (só enquanto está a reproduzir).
 */
function _extrapolate(timer, now) {
    if (!isPlaying(timer.state)) return timer.serverOffset;
    return timer.serverOffset + (now - timer.serverClock);
}

/**
 * Decide se uma leitura do servidor traz informação nova.
 *
 * Em reprodução, o `view_offset` do Plex é sempre um limite inferior da posição
 * real (foi medido algures no passado). Por isso só aceitamos uma leitura que
 * esteja à frente daquilo que já estimamos — ou que esteja tão atrás que só
 * possa ser um recuo deliberado do utilizador.
 */
function _shouldAcceptSample(timer, sampleOffset, extrapolated, now, forced) {
    if (forced) return true;
    if (!isPlaying(timer.state)) return true;

    // Leitura à frente da nossa estimativa: é medição mais recente, aproxima-nos
    // da posição verdadeira.
    if (sampleOffset > extrapolated) return true;

    const farBehind = sampleOffset <= extrapolated - SEEK_THRESHOLD_MS;

    // Uma leitura repetida é a mesma medição a ser devolvida de novo porque o
    // leitor ainda não reportou a sua posição — vai ficando cada vez mais velha
    // com o passar do tempo e não pode ser lida como um recuo. Só a primeira vez
    // que vemos um valor é que ele conta; era isto que fazia o cronómetro saltar
    // para trás quando o leitor reportava a posição espaçadamente. A exceção é o
    // leitor que ficou encravado: aí a leitura repete-se indefinidamente e é o
    // nosso relógio que está errado.
    if (sampleOffset === timer.lastSample) {
        return farBehind && (now - timer.lastSampleClock) >= STALE_SAMPLE_LIMIT_MS;
    }

    // Bem atrás da estimativa logo na primeira vez que a vemos: recuo real feito
    // pelo utilizador.
    return farBehind;
}

function _syncTimer(timer, session, now) {
    const signature = streamMediaSignature(session);
    if (signature !== timer.signature) {
        return _createTimer(session, now);
    }

    const duration = toMillis(session.duration) || timer.duration;
    const sampleOffset = clamp(toMillis(session.view_offset), 0, duration || Number.MAX_SAFE_INTEGER);
    const stateChanged = timer.state !== session.state;
    const extrapolated = _extrapolate(timer, now);

    timer.duration = duration;
    timer.state = session.state;

    if (_shouldAcceptSample(timer, sampleOffset, extrapolated, now, stateChanged)) {
        timer.serverOffset = sampleOffset;
        timer.serverClock = now;
    }

    if (sampleOffset !== timer.lastSample) {
        timer.lastSample = sampleOffset;
        timer.lastSampleClock = now;
    }

    return timer;
}

/**
 * Faz o relógio mostrado avançar até `now`, perseguindo a leitura do servidor.
 */
function _advance(timer, now) {
    const elapsed = Math.max(0, now - timer.displayClock);
    timer.displayClock = now;

    const target = _extrapolate(timer, now);
    const delta = target - timer.displayOffset;

    if (Math.abs(delta) >= SEEK_THRESHOLD_MS) {
        // Salto real na reprodução: acompanhar de imediato.
        timer.displayOffset = target;
    } else if (isPlaying(timer.state)) {
        // Acelera ou abranda ligeiramente. O ritmo nunca chega a zero, por isso
        // o tempo mostrado nunca recua durante a reprodução.
        const rate = 1 + clamp(delta / CORRECTION_WINDOW_MS, -MAX_RATE_ADJUST, MAX_RATE_ADJUST);
        timer.displayOffset += elapsed * rate;
    } else {
        timer.displayOffset += clamp(
            delta,
            -elapsed * PAUSED_CORRECTION_RATE,
            elapsed * PAUSED_CORRECTION_RATE
        );
    }

    timer.displayOffset = timer.duration > 0
        ? clamp(timer.displayOffset, 0, timer.duration)
        : Math.max(0, timer.displayOffset);

    return timer.displayOffset;
}

function _renderTimer(sessionKey, timer) {
    const timeEl = document.getElementById(`time-${sessionKey}`);
    const progressEl = document.getElementById(`progress-${sessionKey}`);

    if (timeEl) {
        const text = `${formatTime(timer.displayOffset)}/${formatTime(timer.duration)}`;
        if (text !== timer.renderedText) {
            timeEl.textContent = text;
            timer.renderedText = text;
        }
    }

    if (progressEl && timer.duration > 0) {
        const width = clamp((timer.displayOffset / timer.duration) * 100, 0, 100).toFixed(2);
        if (width !== timer.renderedWidth) {
            progressEl.style.width = `${width}%`;
            timer.renderedWidth = width;
        }
    }
}

function _tick() {
    const now = monotonicNow();
    const keys = Object.keys(state.activeTimers);

    if (keys.length === 0) {
        _stopTicker();
        return;
    }

    keys.forEach(sessionKey => {
        const timer = state.activeTimers[sessionKey];
        if (!timer) return;
        _advance(timer, now);
        _renderTimer(sessionKey, timer);
    });
}

function _startTicker() {
    if (tickerId !== null) return;
    tickerId = setInterval(_tick, TICK_MS);
}

function _stopTicker() {
    if (tickerId === null) return;
    clearInterval(tickerId);
    tickerId = null;
}

// Com o separador em segundo plano o navegador estrangula os temporizadores.
// Como a posição é sempre calculada a partir do relógio monotónico, basta
// forçar uma passagem assim que a página volta a estar visível.
if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden && tickerId !== null) _tick();
    });
}

/**
 * Reconcilia os relógios locais com a lista de sessões devolvida pelo servidor.
 * @param {Array<object>} sessions - Sessões ativas.
 */
export function syncStreamTimers(sessions) {
    const now = monotonicNow();
    const liveKeys = new Set();

    (sessions || []).forEach(session => {
        const sessionKey = session.session_key;
        if (!sessionKey) return;
        liveKeys.add(sessionKey);

        const timer = state.activeTimers[sessionKey];
        state.activeTimers[sessionKey] = timer
            ? _syncTimer(timer, session, now)
            : _createTimer(session, now);
    });

    Object.keys(state.activeTimers).forEach(sessionKey => {
        if (!liveKeys.has(sessionKey)) delete state.activeTimers[sessionKey];
    });

    if (Object.keys(state.activeTimers).length > 0) {
        _startTicker();
        // Pinta já o estado atual em vez de esperar pelo primeiro tique.
        _tick();
    } else {
        _stopTicker();
    }
}

/**
 * Descarta o relógio de uma sessão que terminou.
 * @param {string} sessionKey - A chave da sessão.
 */
export function removeStreamTimer(sessionKey) {
    delete state.activeTimers[sessionKey];
    if (Object.keys(state.activeTimers).length === 0) _stopTicker();
}

/**
 * Descarta todos os relógios (usado quando deixa de haver sessões ativas).
 */
export function clearStreamTimers() {
    Object.keys(state.activeTimers).forEach(key => delete state.activeTimers[key]);
    _stopTicker();
}
