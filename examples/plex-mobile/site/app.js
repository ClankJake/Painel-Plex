const byId = (id) => document.getElementById(id);
const welcome = byId('welcome');
const catalog = byId('catalog');
const detail = byId('detail');
const section = byId('section');
const video = byId('video');
const more = byId('more');
const status = byId('status');
const login = byId('login');
const authLink = byId('auth-again');
const pageSize = 30;
const clientId = crypto.randomUUID();
let serverToken = '';
let machine = '';
let start = 0;
let catalogRequest;
let detailRequest;

async function get(path, { token, method = 'GET', signal } = {}) {
  const headers = {
    Accept: 'application/json',
    'X-Plex-Product': 'Sala',
    'X-Plex-Client-Identifier': clientId,
  };
  if (token) headers['X-Plex-Token'] = token;
  const response = await fetch(path, { method, headers, signal, cache: 'no-store' });
  if (!response.ok) throw new Error('Falha na comunicação com o Plex (' + response.status + ').');
  return response.json();
}

login.addEventListener('click', async () => {
  login.disabled = true;
  try {
    status.textContent = 'Preparando login…';
    const pin = await get('account/pins?strong=true', { method: 'POST' });
    if (!Number.isSafeInteger(pin.id) || !pin.code) throw new Error('O Plex não retornou um código de login.');
    const query = new URLSearchParams({
      clientID: clientId,
      code: pin.code,
      'context[device][product]': 'Sala',
    });
    authLink.href = 'https://app.plex.tv/auth/#!?' + query;
    authLink.textContent = 'Abrir login do Plex';
    authLink.hidden = false;
    status.textContent = 'Abra o login do Plex. Após autorizar, volte para esta aba.';
    const deadline = Date.now() + Math.min(300, pin.expiresIn || 300) * 1000;
    while (Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 2500));
      const result = await get('account/pins/' + pin.id, { signal: AbortSignal.timeout(15000) });
      if (!result.authToken) continue;
      await connect(result.authToken);
      return;
    }
    throw new Error('O código expirou. Inicie o login novamente.');
  } catch (error) {
    status.textContent = error.message;
  } finally {
    authLink.hidden = true;
    authLink.removeAttribute('href');
    login.disabled = false;
  }
});

async function connect(accountToken) {
  const identity = await get('media/identity');
  machine = identity.MediaContainer.machineIdentifier;
  const resources = await get('account/resources?includeHttps=1', { token: accountToken });
  const server = Array.isArray(resources) && resources.find((entry) => entry.clientIdentifier === machine);
  if (!server) throw new Error('Esta conta não tem acesso ao servidor configurado.');
  const token = server.accessToken || accountToken;
  const result = await get('media/library/sections', { token });
  const libraries = (result.MediaContainer.Directory || []).filter(
    (entry) => ['movie', 'show'].includes(entry.type) && /^\d+$/.test(entry.key),
  );
  if (!libraries.length) throw new Error('Nenhuma biblioteca de filmes ou séries disponível.');
  section.replaceChildren(...libraries.map((entry) => new Option(entry.title, entry.key)));
  serverToken = token;
  welcome.hidden = true;
  catalog.hidden = false;
  byId('logout').hidden = false;
  await loadCatalog();
}

function mediaURL(path) {
  return 'media' + path + '?' + new URLSearchParams({ 'X-Plex-Token': serverToken });
}

function catalogCard(item) {
  const button = document.createElement('button');
  button.className = 'card';
  const poster = document.createElement(/^\/library\/metadata\/\d+\/thumb(?:\/\d+)?$/.test(item.thumb) ? 'img' : 'span');
  poster.className = 'poster';
  if (poster instanceof HTMLImageElement) {
    poster.alt = '';
    poster.loading = 'lazy';
    poster.src = mediaURL(item.thumb);
  }
  const title = document.createElement('strong');
  title.textContent = item.title + (item.year ? ' · ' + item.year : '');
  button.append(poster, title);
  button.addEventListener('click', () => showItem(item));
  return button;
}

async function loadCatalog(append = false) {
  catalogRequest?.abort();
  const request = new AbortController();
  catalogRequest = request;
  const grid = byId('items');
  if (!append) {
    start = 0;
    grid.replaceChildren();
  }
  more.hidden = true;
  status.textContent = 'Carregando títulos…';
  const query = new URLSearchParams({
    'X-Plex-Container-Start': start,
    'X-Plex-Container-Size': pageSize,
  });
  const title = byId('search').value.trim();
  if (title) query.set('title', title);
  try {
    const { MediaContainer: page } = await get(
      'media/library/sections/' + section.value + '/all?' + query,
      { token: serverToken, signal: request.signal },
    );
    if (request.signal.aborted) return;
    const items = page.Metadata || [];
    grid.append(...items.map(catalogCard));
    start += items.length;
    more.hidden = items.length === 0 || start >= (page.totalSize ?? (items.length < pageSize ? start : start + 1));
    status.textContent = !grid.childElementCount ? 'Nenhum título encontrado.' : '';
  } catch (error) {
    if (request.signal.aborted) return;
    status.textContent = error.message;
    more.hidden = false;
  }
}

function resetVideo() {
  video.pause();
  video.removeAttribute('src');
  video.load();
  video.hidden = true;
}

async function showItem(item) {
  const key = String(item.ratingKey);
  if (!/^\d+$/.test(key)) return;
  catalogRequest?.abort();
  detailRequest?.abort();
  const request = new AbortController();
  detailRequest = request;
  resetVideo();
  const episodes = byId('episodes');
  episodes.replaceChildren();
  catalog.hidden = true;
  detail.hidden = false;
  byId('detail-title').textContent = item.title;
  byId('summary').textContent = item.summary || '';
  byId('web').href = 'https://app.plex.tv/desktop/#!/server/' +
    encodeURIComponent(machine) + '/details?key=' + encodeURIComponent('/library/metadata/' + key);
  byId('back').focus();
  status.textContent = 'Carregando…';
  try {
    const prefix = 'media/library/metadata/' + key;
    const { MediaContainer: page } = await get(
      prefix + (['show', 'season'].includes(item.type) ? '/children' : ''),
      { token: serverToken, signal: request.signal },
    );
    if (request.signal.aborted) return;
    const entries = page.Metadata || [];
    status.textContent = '';
    if (['show', 'season'].includes(item.type)) {
      for (const entry of entries) {
        const button = document.createElement('button');
        button.textContent = entry.type === 'episode' && entry.index ? entry.index + '. ' + entry.title : entry.title;
        button.addEventListener('click', () => showItem(entry));
        episodes.append(button);
      }
      if (!entries.length) status.textContent = 'Nenhum episódio ou temporada disponível.';
      return;
    }
    const part = entries[0]?.Media?.filter((media) => media.Part?.length === 1)
      .flatMap((media) => media.Part)
      .find(({ key: path, container }) =>
        ['mp4', 'm4v', 'webm'].includes(container) && /^\/library\/parts\/[\w./%-]+$/.test(path) &&
        path.split('/').every((segment) => segment !== '..' && !segment.includes('%')));
    if (!part) {
      status.textContent = 'Use o Plex Web para reproduzir este formato.';
      return;
    }
    video.src = mediaURL(part.key);
    video.hidden = false;
  } catch (error) {
    if (!request.signal.aborted) status.textContent = error.message;
  }
}

video.addEventListener('error', () => {
  if (!video.hidden && video.hasAttribute('src')) {
    status.textContent = 'O navegador não conseguiu reproduzir este arquivo. Abra no Plex Web.';
  }
});
section.addEventListener('change', () => loadCatalog());
byId('search-form').addEventListener('submit', (event) => {
  event.preventDefault();
  loadCatalog();
});
more.addEventListener('click', () => loadCatalog(start > 0));
byId('back').addEventListener('click', () => {
  detailRequest?.abort();
  resetVideo();
  detail.hidden = true;
  catalog.hidden = false;
  status.textContent = '';
  section.focus();
  if (!byId('items').childElementCount) loadCatalog();
});
byId('logout').addEventListener('click', () => location.reload());
