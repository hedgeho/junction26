function bindNotesToggle(toggle) {
 const fieldset = document.getElementById(toggle.dataset.toggle);
 if (!fieldset) return;
 const update = () => { fieldset.hidden = !toggle.checked; fieldset.disabled = !toggle.checked; };
 toggle.addEventListener('change', update);
 update();
}
document.querySelectorAll('[data-toggle]').forEach(bindNotesToggle);
function revealHash() {
 const target = document.getElementById(location.hash.slice(1));
 if (target instanceof HTMLDetailsElement) target.open = true;
}
revealHash();
window.addEventListener('hashchange', revealHash);
document.addEventListener('submit', event => {
 if (event.defaultPrevented || !(event.target instanceof HTMLFormElement)) return;
 const form = event.target;
 form.setAttribute('aria-busy', 'true');
 form.querySelectorAll('button[type="submit"], button:not([type])').forEach(button => {
  button.disabled = true;
  button.classList.add('is-busy');
 });
});
window.addEventListener('pageshow', () => {
 document.querySelectorAll('form[aria-busy]').forEach(form => form.removeAttribute('aria-busy'));
 document.querySelectorAll('button.is-busy').forEach(button => { button.disabled = false; button.classList.remove('is-busy'); });
});
document.querySelectorAll('time[datetime]').forEach(node => {
 const date = new Date(node.dateTime);
 if (!Number.isNaN(date.valueOf())) node.textContent = new Intl.DateTimeFormat(undefined, {month:'short',day:'numeric'}).format(date);
});
function pollStatus(url, node, isDone, render) {
 let delay = 3000;
 async function poll() {
  try {
   const response = await fetch(url, {cache:'no-store', headers:{Accept:'application/json'}});
   if (!response.ok || response.redirected) throw new Error('status unavailable');
   const result = await response.json();
   delay = 3000;
   render(result);
   if (isDone(result)) return;
  } catch (_) { delay = Math.min(delay * 2, 30000); }
  setTimeout(poll, delay);
 }
 setTimeout(poll, delay);
}
function refreshFeedWhenSafe() {
 const dialog = document.querySelector('dialog[open]');
 const draft = document.getElementById('more-feedback');
 if (dialog) { dialog.addEventListener('close', refreshFeedWhenSafe, {once:true}); return; }
 if (draft?.value.trim()) {
  const waitForClear = () => {
   if (!draft.value.trim()) { draft.removeEventListener('input', waitForClear); refreshFeedWhenSafe(); }
  };
  draft.addEventListener('input', waitForClear);
  return;
 }
 location.reload();
}
const pending = document.getElementById('curation-pending');
if (pending) pollStatus('/api/curation', pending, result => result.state === 'ready', result => {
 if (result.state === 'ready') refreshFeedWhenSafe();
 else pending.textContent = result.label;
});
const job = document.getElementById('job-status');
if (job && ['queued','processing'].includes(job.dataset.state)) {
 const labels = {queued:'Queued', processing:'Updating', applied:'Updated', failed:'Update failed. Please try again.'};
 pollStatus('/api/jobs/' + job.dataset.job, job, result => ['applied','failed'].includes(result.state), result => {
  job.textContent = labels[result.state] || 'Queued';
  if (result.state === 'applied') {
   job.textContent = 'Filtering posts...';
   pollStatus('/api/curation', job, state => state.state === 'ready', state => {
    if (state.state === 'ready') refreshFeedWhenSafe();
    else job.textContent = state.label;
   });
  }
 });
}
const limitDialog = document.getElementById('daily-limit-dialog');
const limitOpen = document.getElementById('daily-limit-open');
if (limitDialog && limitOpen) {
 limitOpen.addEventListener('click', () => { limitDialog.showModal(); document.getElementById('feed-daily-limit').focus(); });
 document.getElementById('daily-limit-close').addEventListener('click', () => limitDialog.close());
 limitDialog.addEventListener('close', () => limitOpen.focus());
 limitDialog.addEventListener('click', event => {
  if (event.target !== limitDialog) return;
  const rect = limitDialog.getBoundingClientRect();
  if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) limitDialog.close();
 });
}
const dialog = document.getElementById('feedback-dialog');
if (dialog) {
 let trigger;
 const form = dialog.querySelector('form');
 document.querySelectorAll('.feedback-open').forEach(button => button.addEventListener('click', () => {
  trigger = button;
  const post = button.closest('.post');
  const key = post.dataset.postKey;
  // Keep a draft for the same post; do not carry feedback onto a different post.
  if (document.getElementById('feedback-post').value !== key) form.reset();
  document.getElementById('feedback-post').value = key;
  document.getElementById('feedback-context').textContent = post.querySelector('.post-text').textContent;
  dialog.showModal();
  document.getElementById('feedback').focus();
 }));
 document.getElementById('feedback-close').addEventListener('click', () => dialog.close());
 dialog.addEventListener('close', () => trigger?.focus());
 dialog.addEventListener('click', event => {
  if (event.target !== dialog) return;
  const rect = dialog.getBoundingClientRect();
  if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
 });
}
// Source text stays unchanged; facets supply destinations for displayed substrings.
function safeHttps(value) {
 try { const url = new URL(value); return url.protocol === 'https:' && !url.username && !url.password ? url.href : null; }
 catch (_) { return null; }
}
function externalLink(url, label) {
 const link = document.createElement('a');
 link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer';
 link.textContent = label;
 return link;
}
function renderPostText(node, facets = []) {
 const text = node.textContent;
 const bytes = new TextEncoder().encode(text);
 const decoder = new TextDecoder('utf-8', {fatal:true});
 const spans = [];
 for (const facet of Array.isArray(facets) ? facets : []) {
  if (!facet || typeof facet !== 'object') continue;
  const url = safeHttps(facet.url);
  if (!url || !Number.isInteger(facet.start) || !Number.isInteger(facet.end) || facet.start < 0 || facet.end > bytes.length || facet.start >= facet.end) continue;
  try {
   const before = decoder.decode(bytes.slice(0, facet.start));
   const content = decoder.decode(bytes.slice(facet.start, facet.end));
   decoder.decode(bytes.slice(facet.end));
   spans.push({start:before.length, end:before.length + content.length, url});
  } catch (_) { /* Ignore invalid UTF-8 boundaries. */ }
 }
 // Make plain HTTPS URLs usable when the source has no corresponding facet.
 for (const match of text.matchAll(/https:\/\/[^\s<>]+/gu)) {
  const trimmed = match[0].replace(/[.,!?;:]+$/u, '');
  const start = match.index, end = start + trimmed.length;
  const url = safeHttps(trimmed);
  if (url && !spans.some(span => start < span.end && end > span.start)) spans.push({start, end, url});
 }
 spans.sort((a, b) => a.start - b.start || a.end - b.end);
 const fragment = document.createDocumentFragment();
 let cursor = 0;
 for (const span of spans) {
  if (span.start < cursor) continue;
  fragment.append(document.createTextNode(text.slice(cursor, span.start)), externalLink(span.url, text.slice(span.start, span.end)));
  cursor = span.end;
 }
 fragment.append(document.createTextNode(text.slice(cursor)));
 node.replaceChildren(fragment);
}
function sourceImage(url, alt) {
 const img = document.createElement('img');
 img.src = url; img.alt = alt || ''; img.loading = 'lazy'; img.referrerPolicy = 'no-referrer';
 return img;
}
function renderPostDetails(post, details) {
 const name = post.querySelector('.author');
 if (details.author_name) name.textContent = details.author_name;
 const handle = post.querySelector('.author-handle');
 if (details.author_handle) { handle.textContent = '@' + details.author_handle.replace(/^@/, ''); handle.hidden = false; }
 const avatar = safeHttps(details.author_avatar);
 if (avatar) {
  const box = post.querySelector('.author-avatar');
  const img = sourceImage(avatar, '');
  img.addEventListener('error', () => { box.hidden = true; }, {once:true});
  const profile = safeHttps(details.author_url);
  if (profile) { const link = externalLink(profile, ''); link.setAttribute('aria-label', 'View profile'); link.append(img); box.replaceChildren(link); }
  else box.replaceChildren(img);
  box.hidden = false;
 }
 renderPostText(post.querySelector('.post-text'), details.facets || []);
 const container = post.querySelector('.post-embeds');
 const images = Array.isArray(details.images) ? details.images.slice(0, 4) : [];
 const grid = document.createElement('div'); grid.className = 'post-images';
 for (const image of images) {
  if (!image || typeof image !== 'object') continue;
  const url = safeHttps(image.url), full = safeHttps(image.full_url);
  if (!url || !full) continue;
  const link = externalLink(full, ''); link.append(sourceImage(url, image.alt));
  if (!image.alt) link.setAttribute('aria-label', 'Open image');
  grid.append(link);
 }
 if (grid.childElementCount) container.append(grid);
 const item = details.external;
 const url = item && safeHttps(item.url);
 if (url) {
  const card = externalLink(url, ''); card.className = 'link-card';
  const image = safeHttps(item.image); if (image) card.append(sourceImage(image, ''));
  const body = document.createElement('span'); body.className = 'link-card-body';
  const title = document.createElement('strong'); title.textContent = item.title || new URL(url).hostname; body.append(title);
  if (item.description) { const desc = document.createElement('span'); desc.textContent = item.description; body.append(desc); }
  const host = document.createElement('small'); host.textContent = new URL(url).hostname; body.append(host);
  card.append(body); container.append(card);
 }
}
const postCards = [...document.querySelectorAll('.post[data-post-key]')];
postCards.forEach(post => renderPostText(post.querySelector('.post-text')));
if (postCards.length) fetch('/api/post-details', {cache:'no-store', headers:{Accept:'application/json'}})
 .then(response => response.ok && !response.redirected ? response.json() : null)
 .then(data => { if (data?.items) postCards.forEach(post => {const details = data.items[post.dataset.postKey]; if (details) renderPostDetails(post, details);}); })
 .catch(() => {});
