/**
 * Esperar por um reinício do painel.
 *
 * 🐛 Isto era um `setTimeout(..., 8000)` repetido em três sítios — o fim do
 * assistente, o restauro de backup no assistente e o restauro nas definições —
 * e oito segundos era um palpite, não uma medida. O processo antigo não morre
 * quando lhe mandamos o SIGTERM: sob gunicorn ele só sai quando não houver
 * ligações a ser servidas, e um separador aberto no navegador chega para
 * segurar a saída durante todo o tempo de cortesia. O que o palpite fazia,
 * então, era recarregar a página a tempo de apanhar o processo que estava a
 * morrer — e depois de trocar o servidor para Jellyfin isso queria dizer a
 * página de login do PLEX, como se a configuração não tivesse sido gravada.
 *
 * A saída é não adivinhar: cada arranque do painel tem uma marca
 * (`BOOT_ID`), quem pede o reinício recebe a marca ATUAL, e ficamos a
 * perguntar até ela mudar. Enquanto for a mesma, quem responde é o processo
 * antigo.
 */

const INTERVALO_MS = 1000;
// ⚠️ Um limite tem de existir: um painel que não volte (um contentor sem
// política de reinício, por exemplo) não pode deixar a página a girar para
// sempre sem dizer nada. Ao fim deste tempo seguimos em frente na mesma —
// pode ser que tenha voltado e a pergunta é que esteja a falhar.
const LIMITE_MS = 120000;
// Cada pergunta tem o seu próprio limite: enquanto não há worker a aceitar,
// a ligação fica pendurada em vez de ser recusada, e sem isto o ciclo parava
// na primeira tentativa.
const LIMITE_POR_PERGUNTA_MS = 4000;

async function perguntar(url) {
    const controlador = new AbortController();
    const desistir = setTimeout(() => controlador.abort(), LIMITE_POR_PERGUNTA_MS);
    try {
        const resposta = await fetch(url, { cache: 'no-store', signal: controlador.signal });
        if (!resposta.ok) return null;
        return await resposta.json();
    } catch (e) {
        // O painel está em baixo, ou a ligação foi cortada a meio do
        // encerramento. Não é um erro: é o que estamos à espera de ver.
        return null;
    } finally {
        clearTimeout(desistir);
    }
}

const esperar = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Espera que o painel volte e só então executa `aoVoltar`.
 *
 * @param {string} url       Rota de estado (`/api/system/status`).
 * @param {string} bootId    A marca do processo que vai morrer.
 * @param {Function} aoVoltar O que fazer quando o painel novo responder.
 * @param {number} [limiteMs] Quanto tempo esperar antes de seguir na mesma.
 */
export async function aguardarReinicio({ url, bootId, aoVoltar, limiteMs = LIMITE_MS }) {
    const fim = Date.now() + limiteMs;

    // Sem marca não há o que comparar (uma resposta de uma versão anterior do
    // painel, por exemplo): fica o palpite antigo, que é o melhor que resta.
    if (!url || !bootId) {
        await esperar(Math.min(8000, limiteMs));
        aoVoltar();
        return;
    }

    while (Date.now() < fim) {
        await esperar(INTERVALO_MS);
        const estado = await perguntar(url);
        if (estado && estado.boot_id && estado.boot_id !== bootId) {
            aoVoltar();
            return;
        }
    }

    aoVoltar();
}
