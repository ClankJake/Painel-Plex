// eslint.config.mjs
//
// UMA regra só: `no-undef`. Isto não é um linter de estilo — o projeto não tem
// nem quer um formatador — é um guarda contra uma classe de erro que nenhum
// teste da suíte consegue ver.
//
// 🐛 Um módulo ES é código estrito, e um identificador que não existe só
// levanta quando a linha CORRE. Uma referência deixada para trás numa
// substituição incompleta não dá erro a carregar a página: fica à espera, e
// rebenta na cara de quem clicar no botão que a executa. Foi o que aconteceu ao
// `allInvites` dentro do `onclick` do botão "Detalhes" do convite — a lista
// tinha mudado de nome quando a filtragem das abas passou para o servidor, o
// painel carregava sem uma queixa, e o modal simplesmente não abria.
//
// 🐛 A segunda regra existe porque a primeira NÃO apanhava o irmão deste erro.
// `import * as ui` só traz o que o módulo EXPORTA, e `ui.showToast` era uma
// função que o `ui.js` apenas IMPORTAVA do `utils.js` — para o `no-undef` o
// `ui` existe e está tudo bem; o `TypeError: ui.showToast is not a function` só
// aparecia quando um pagamento entrava e o socket disparava o evento. E pior do
// que o aviso perdido: a exceção matava a linha seguinte, que era a que
// recarregava a lista. O `import-x/namespace` compara cada `NS.membro` com os
// exports reais do módulo.
//
// ⚠️ `allowComputed` fica LIGADO. O `api[endpoint]` do `handleTestConnection` é
// uma despachagem dinâmica de propósito, e já trata sozinha o nome desconhecido
// (`typeof !== 'function'` → erro claro). Marcá-la seria obrigar a calar a regra
// num sítio onde o código está certo — e um guarda com exceções espalhadas
// deixa de ser lido.
//
// O pytest não chega lá (não executa JavaScript) e o `npm run build` também
// não (o Tailwind lê os templates à procura de NOMES DE CLASSES, não analisa os
// módulos). Daí este passo, no job do frontend que já instala o npm.
//
// ⚠️ Os globais são declarados à mão, e é de propósito: o que NÃO estiver nesta
// lista é um erro. Uma biblioteca nova carregada por <script> no template tem
// de ser acrescentada aqui — que é precisamente o momento em que alguém deve
// reparar que ela existe.

import importX from "eslint-plugin-import-x";

const NAVEGADOR = [
    // Documento e janela
    "window", "document", "location", "history", "navigator", "screen", "self",
    "getComputedStyle", "matchMedia", "structuredClone", "queueMicrotask",
    // Tipos do DOM
    "Element", "HTMLElement", "Node", "NodeList", "Event", "CustomEvent",
    "DOMParser", "Image", "FormData",
    // Temporizadores
    "setTimeout", "clearTimeout", "setInterval", "clearInterval",
    "requestAnimationFrame", "cancelAnimationFrame",
    // Rede
    "fetch", "Headers", "Request", "Response", "AbortController",
    "XMLHttpRequest", "WebSocket", "EventSource", "URL", "URLSearchParams",
    // Armazenamento e ficheiros
    "localStorage", "sessionStorage", "indexedDB", "Blob", "File", "FileReader",
    // Observadores
    "IntersectionObserver", "MutationObserver", "ResizeObserver",
    // Diálogos e diagnóstico
    "console", "alert", "confirm", "prompt", "performance",
    // Codificação e cifra
    "atob", "btoa", "crypto", "TextEncoder", "TextDecoder", "Intl",
    // Notificações (push.js)
    "Notification", "PushManager", "ServiceWorkerRegistration",
];

// O service worker corre noutro contexto: não tem `document` e tem estes.
const SERVICE_WORKER = [
    "self", "caches", "clients", "registration", "skipWaiting", "importScripts",
    "fetch", "Response", "Request", "Headers", "URL", "console",
];

// Carregadas por <script> no template, não importadas pelos módulos.
const BIBLIOTECAS = ["io", "Chart"];

const soLeitura = (nomes) =>
    Object.fromEntries(nomes.map((nome) => [nome, "readonly"]));

export default [
    {
        files: ["app/static/js/**/*.js"],
        ignores: ["app/static/js/service-worker.js"],
        plugins: { "import-x": importX },
        languageOptions: {
            ecmaVersion: "latest",
            sourceType: "module",
            globals: soLeitura([...NAVEGADOR, ...BIBLIOTECAS]),
        },
        rules: {
            "no-undef": "error",
            "import-x/namespace": ["error", { allowComputed: true }],
        },
    },
    {
        files: ["app/static/js/service-worker.js"],
        languageOptions: {
            ecmaVersion: "latest",
            sourceType: "script",
            globals: soLeitura(SERVICE_WORKER),
        },
        rules: { "no-undef": "error" },
    },
];
