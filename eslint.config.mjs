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
// O pytest não chega lá (não executa JavaScript) e o `npm run build` também
// não (o Tailwind lê os templates à procura de NOMES DE CLASSES, não analisa os
// módulos). Daí este passo, no job do frontend que já instala o npm.
//
// ⚠️ Os globais são declarados à mão, e é de propósito: o que NÃO estiver nesta
// lista é um erro. Uma biblioteca nova carregada por <script> no template tem
// de ser acrescentada aqui — que é precisamente o momento em que alguém deve
// reparar que ela existe.

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
        languageOptions: {
            ecmaVersion: "latest",
            sourceType: "module",
            globals: soLeitura([...NAVEGADOR, ...BIBLIOTECAS]),
        },
        rules: { "no-undef": "error" },
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
