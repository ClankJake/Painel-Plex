// app/static/js/users_modules/dom.js

/**
 * Módulo DOM
 * * Centraliza a obtenção de referências para os elementos do DOM.
 * Isto evita a repetição de `document.getElementById` e facilita a manutenção
 * se os IDs dos elementos mudarem no futuro.
 */

export const userGrid = document.getElementById('userGrid');
export const inviteListDiv = document.getElementById('inviteList');
export const createInviteButton = document.getElementById('createInviteButton');
export const refreshButton = document.getElementById('refreshButton');
export const searchInput = document.getElementById('searchInput');
export const sortSelect = document.getElementById('sortSelect');
export const userTabs = document.getElementById('userTabs');
export const bulkActionsButton = document.getElementById('bulkActionsButton');

// Contadores nas abas
export const countAll = document.getElementById('count-all');
export const countActive = document.getElementById('count-active');
export const countTrial = document.getElementById('count-trial');
export const countBlocked = document.getElementById('count-blocked');
