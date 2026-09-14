/**
 * password.js
 * As duas páginas públicas de palavra-passe: pedir o link e escolher a nova.
 *
 * É um ficheiro só porque são dois passos do mesmo fluxo e partilham os mesmos
 * textos — cada página traz o formulário que lhe diz respeito, e o que não
 * existir fica simplesmente sem quem o ligue.
 */

import { chaveEmCamelCase } from './utils.js';

const scriptTag = document.getElementById('password-script');

const urls = {};
const i18n = {};
for (const key in scriptTag.dataset) {
    if (key.startsWith('urls')) {
        urls[chaveEmCamelCase(key, 4)] = scriptTag.dataset[key];
    } else if (key.startsWith('i18n')) {
        i18n[chaveEmCamelCase(key, 4)] = scriptTag.dataset[key];
    }
}

/** Um texto do dicionário, nunca `undefined` na cara de quem está a ler. */
function texto(chave, alternativa = '') {
    return i18n[chave] || alternativa;
}

function mostrar(elemento, mensagem, sucesso) {
    elemento.textContent = mensagem;
    elemento.classList.toggle('text-green-500', !!sucesso);
    elemento.classList.toggle('text-red-500', !sucesso);
}

/**
 * Liga um formulário ao seu pedido, tratando botão, spinner e erros de rede.
 * Os dois formulários fazem exatamente isto — só muda o corpo e o que se faz
 * com a resposta.
 */
function ligarFormulario({ form, botao, textoBotao, spinner, resultado, textoAEnviar, textoNormal, corpo, aoConcluir }) {
    form.addEventListener('submit', async (evento) => {
        evento.preventDefault();

        const dados = corpo();
        if (dados.erro) {
            mostrar(resultado, dados.erro, false);
            return;
        }

        botao.disabled = true;
        spinner?.classList.remove('hidden');
        if (textoBotao) textoBotao.textContent = textoAEnviar;
        mostrar(resultado, '', true);

        try {
            const resposta = await fetch(dados.url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(dados.corpo),
            });
            const json = await resposta.json();

            // Uma resposta sem corpo útil (429 do limitador, 500) deixava a
            // pessoa sem saber o que aconteceu.
            mostrar(resultado, json.message || texto('networkError'), json.success);

            if (json.success) {
                aoConcluir?.(form, botao);
                return;
            }
        } catch (e) {
            mostrar(resultado, texto('networkError'), false);
        }

        spinner?.classList.add('hidden');
        botao.disabled = false;
        if (textoBotao) textoBotao.textContent = textoNormal;
    });
}

// --- Pedir o link ---------------------------------------------------------

const forgotForm = document.getElementById('forgot-form');
if (forgotForm) {
    const resultado = document.getElementById('forgot-result');

    ligarFormulario({
        form: forgotForm,
        botao: document.getElementById('forgot-submit'),
        textoBotao: document.getElementById('forgot-submit-text'),
        spinner: document.getElementById('forgot-spinner'),
        resultado,
        textoAEnviar: texto('sending'),
        textoNormal: texto('send'),
        corpo: () => {
            const identificador = document.getElementById('forgot-identifier').value.trim();
            if (!identificador) return { erro: texto('identifierRequired') };
            return { url: urls.forgot, corpo: { identifier: identificador } };
        },
        // 🛡️ A resposta é a mesma exista a conta ou não. O botão fica
        // desativado a seguir: repetir o pedido não traz informação nova, e o
        // servidor tem um intervalo mínimo entre envios.
        aoConcluir: (form, botao) => {
            botao.disabled = true;
            document.getElementById('forgot-spinner')?.classList.add('hidden');
        },
    });
}

// --- Escolher a palavra-passe nova ----------------------------------------

const resetForm = document.getElementById('reset-form');
if (resetForm) {
    const resultado = document.getElementById('reset-result');

    ligarFormulario({
        form: resetForm,
        botao: document.getElementById('reset-submit'),
        textoBotao: document.getElementById('reset-submit-text'),
        spinner: document.getElementById('reset-spinner'),
        resultado,
        textoAEnviar: texto('saving'),
        textoNormal: texto('save'),
        corpo: () => {
            const palavraPasse = document.getElementById('reset-password').value;
            const confirmacao = document.getElementById('reset-password-confirm').value;

            // ⚠️ Verificar aqui poupa um pedido e, sobretudo, o token: ele
            // gasta-se ao ser usado, e um engano a escrever obrigaria a pedir
            // outro link.
            if (palavraPasse.length < 6) return { erro: texto('tooShort') };
            if (palavraPasse !== confirmacao) return { erro: texto('mismatch') };

            return {
                url: urls.reset,
                corpo: { token: resetForm.dataset.token, password: palavraPasse },
            };
        },
        aoConcluir: (form) => {
            form.querySelectorAll('input, button').forEach((campo) => { campo.disabled = true; });
            setTimeout(() => { window.location.href = urls.login; }, 2500);
        },
    });

    // Ver o que se escreveu: é a palavra-passe que a pessoa vai passar a usar.
    const alternar = document.getElementById('toggle-reset-password');
    alternar?.addEventListener('click', () => {
        const campo = document.getElementById('reset-password');
        const visivel = campo.type === 'text';

        campo.type = visivel ? 'password' : 'text';
        document.getElementById('reset-icon-eye')?.classList.toggle('hidden', !visivel);
        document.getElementById('reset-icon-eye-off')?.classList.toggle('hidden', visivel);
        alternar.setAttribute('aria-pressed', String(!visivel));
        alternar.setAttribute('aria-label', (visivel ? texto('showPassword') : texto('hidePassword')));
        campo.focus();
    });
}
