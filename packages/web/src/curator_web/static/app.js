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
const feedbackDrafts = new Map();
document.querySelectorAll('.feed-feedback-form').forEach(form => form.addEventListener('submit', async event => {
 event.preventDefault();
 const input = form.querySelector('textarea'), status = form.querySelector('.feedback-progress'), button = form.querySelector('[type="submit"]');
 const text = input.value, postKey = form.querySelector('[name="post_key"]')?.value;
 button.disabled = true; status.textContent = 'Saving your feedback?';
 try {
  const response = await fetch(form.action, {method:'POST', body:new FormData(form), headers:{Accept:'application/json'}});
  if (!response.ok || response.redirected) throw new Error();
  const result = await response.json(); const keyField = form.querySelector('[name="key"]'); keyField.value = keyField.defaultValue = result.next_key;
  input.value = ''; if (postKey) feedbackDrafts.delete(postKey);
  document.dispatchEvent(new Event('feedback-draft-change'));
  status.textContent = 'Saved ? updating your preferences?';
  pollStatus('/api/jobs/' + result.job_id, status, result => ['applied','failed'].includes(result.state), result => {
   if (result.state === 'queued') status.textContent = 'Saved ? waiting to update your preferences?';
   else if (result.state === 'processing') status.textContent = 'Updating your preferences?';
   else if (result.state === 'failed') {
    status.textContent = 'Could not update your preferences. Please try again.';
    if (!input.value.trim()) { input.value = text; if (postKey) feedbackDrafts.set(postKey, text); }
   } else if (result.state === 'applied') {
    status.textContent = 'Preferences updated ? finding posts?';
    pollStatus('/api/curation', status, state => state.state === 'ready', state => {
     if (state.state === 'ready') { status.textContent = 'Feed updated'; refreshFeedWhenSafe(); }
     else if (state.state === 'failed' || state.state === 'offline' || state.state === 'not_configured') status.textContent = 'Preferences updated ? ' + state.label;
    });
   }
  });
 } catch (_) { status.textContent = 'Could not save your feedback. Please try again.'; }
 finally { button.disabled = false; }
}));
function refreshFeedWhenSafe() {
 const dialog = document.querySelector('dialog[open]');
 const draft = document.getElementById('more-feedback');
 const panel = document.getElementById('feedback-panel');
 const feedbackDraft = document.getElementById('feedback');
 if (panel && !panel.hidden) { panel.addEventListener('feedback-close', refreshFeedWhenSafe, {once:true}); return; }
 if (feedbackDraft?.value.trim() || [...feedbackDrafts.values()].some(value => value.trim())) {
  const waitForClear = () => {
   if (!feedbackDraft?.value.trim() && ![...feedbackDrafts.values()].some(value => value.trim())) {
    document.removeEventListener('feedback-draft-change', waitForClear); refreshFeedWhenSafe();
   }
  };
  document.addEventListener('feedback-draft-change', waitForClear); return;
 }
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
const feedbackPanel = document.getElementById('feedback-panel');
if (feedbackPanel) {
 let trigger;
 const form = feedbackPanel.querySelector('form');
 const feedbackInput = document.getElementById('feedback');
 feedbackInput.addEventListener('input', () => {
  feedbackDrafts.set(document.getElementById('feedback-post').value, feedbackInput.value);
  document.dispatchEvent(new Event('feedback-draft-change'));
 });
 const closeFeedback = () => {
  feedbackPanel.hidden = true;
  trigger?.setAttribute('aria-expanded', 'false');
  trigger?.focus();
  feedbackPanel.dispatchEvent(new Event('feedback-close'));
 };
 document.querySelectorAll('.feedback-open').forEach(button => button.addEventListener('click', () => {
  if (trigger) trigger.setAttribute('aria-expanded', 'false');
  trigger = button;
  const post = button.closest('.post');
  const key = post.dataset.postKey;
  if (document.getElementById('feedback-post').value !== key) {
   form.reset(); feedbackInput.value = feedbackDrafts.get(key) || '';
  }
  document.getElementById('feedback-post').value = key;
  document.getElementById('feedback-context-author').textContent = post.querySelector('.author')?.textContent || 'Post';
  document.getElementById('feedback-context-text').textContent = post.querySelector('.post-text')?.textContent || '';
  form.querySelector('.feedback-progress').textContent = '';
  const original = document.getElementById('feedback-original');
  const source = post.querySelector('.post-actions')?.dataset.platform === 'bluesky' && safeHttps(post.dataset.sourceUrl);
  original.hidden = !source; if (source) original.href = source;
  feedbackPanel.hidden = false;
  button.setAttribute('aria-expanded', 'true');
  document.getElementById('feedback').focus();
 }));
 document.getElementById('feedback-close').addEventListener('click', closeFeedback);
 document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && !feedbackPanel.hidden && !document.querySelector('dialog[open]')) closeFeedback();
 });
}
const algorithmDialog = document.getElementById('algorithm-info-dialog');
let algorithmOpener = null;
if (algorithmDialog) {
 document.querySelectorAll('#algorithm-info-open, .funnel-open').forEach(button => button.addEventListener('click', () => {
  algorithmOpener = button;
  algorithmDialog.showModal();
  algorithmDialog.scrollTop = 0;
  loadFunnel(algorithmDialog.querySelector('.funnel'));
 }));

 document.getElementById('algorithm-info-close').addEventListener('click', () => algorithmDialog.close());
 algorithmDialog.addEventListener('close', () => algorithmOpener?.focus());
 algorithmDialog.addEventListener('click', event => {
  if (event.target !== algorithmDialog) return;
  const rect = algorithmDialog.getBoundingClientRect();
  if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) algorithmDialog.close();
 });
}
function el(tag, className, text) {
 const node = document.createElement(tag);
 if (className) node.className = className;
 if (text !== undefined) node.textContent = text;
 return node;
}
function renderRanking(box, ranking) {
 if (!box || !ranking) return;
 const term = (...children) => { const span = el('span', 'funnel-term'); span.append(...children); return span; };
 const weights = Object.entries(ranking.weights);
 const formula = [term('value =')];
 weights.forEach(([name, weight], i) => formula.push(' ', term(`${i ? '+ ' : '('}${weight.toFixed(2)} \u00d7 ${name}${i === weights.length - 1 ? ')' : ''}`)));
 formula.push(' ', term('\u00d7 \u00bd', el('sup', '', `age \u00f7 ${ranking.half_life_h} h`)));
 formula.push(' ', term(`\u00d7 ${ranking.flag_factor === 0.5 ? '\u00bd' : ranking.flag_factor} per content flag`));
 box.querySelector('.funnel-formula').replaceChildren(...formula);
 box.querySelector('.funnel-rule').textContent = `A post competes once the model includes it with relevance \u2265 ${ranking.min_relevance.toFixed(2)}. The ${ranking.daily_limit} highest values fill your feed, at most ${ranking.max_per_author} per author and one per conversation or linked story.`;
 box.hidden = false;
}
async function loadFunnel(section) {
 if (!section) return;
 const status = section.querySelector('.funnel-status');
 const list = section.querySelector('.funnel-stages');
 const notes = section.querySelector('.funnel-notes');
 const number = new Intl.NumberFormat();
 try {
  const response = await fetch('/api/funnel', {cache:'no-store', headers:{Accept:'application/json'}});
  if (!response.ok) throw new Error('funnel');
  const funnel = await response.json();
  if (!funnel.stages.length) { status.textContent = 'No posts counted yet.'; return; }
  list.replaceChildren(...funnel.stages.map(stage => {
   const item = el('li', 'funnel-stage');
   const head = el('div', 'funnel-head');
   head.append(el('span', 'funnel-count', number.format(stage.count)), el('span', 'funnel-label', stage.label));
   item.append(head);
   if (stage.drops.length) {
    const drops = el('ul', 'funnel-drops');
    for (const drop of stage.drops) {
     const share = stage.count ? drop.count / stage.count : 0;
     const percent = share >= 0.01 || !drop.count ? Math.round(share * 100) + '%' : '<1%';
     const row = el('li', 'funnel-drop');
     row.title = `${number.format(drop.count)} of ${number.format(stage.count)} (${percent})`;
     const bar = el('span', 'funnel-bar');
     const fill = el('span');
     fill.style.width = drop.count ? `max(2px, ${share * 100}%)` : '0';
     bar.append(fill);
     row.append(bar, el('span', 'funnel-drop-count', '\u2212' + number.format(drop.count)), el('span', 'funnel-drop-label', drop.label));
     drops.append(row);
    }
    item.append(drops);
   }
   return item;
  }));
  renderRanking(section.querySelector('.funnel-ranking'), funnel.ranking);
  const extra = [];
  if (funnel.notes.downranked?.length) extra.push('Ranked lower but still eligible: ' + funnel.notes.downranked.map(d => `${d.label.toLowerCase()} ${number.format(d.count)}`).join(', ') + '.');
  if (funnel.notes.flags_pending) extra.push(`${number.format(funnel.notes.flags_pending)} relevant posts are still waiting for content checks.`);
  notes.textContent = extra.join(' ');
  notes.hidden = !extra.length;
  list.hidden = false;
  status.hidden = true;
 } catch {
  status.textContent = 'Counts are unavailable right now.';
 }
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
 if (Number.isInteger(details.reply_count)) {
  post.dataset.replyCount = String(details.reply_count);
  const toggle = post.querySelector('.comments-toggle');
  if (toggle && (details.reply_count > 0 || (ownComments.get(post) || []).length)) toggle.hidden = false;
 }
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
  link.addEventListener('click', event => {
   const dialog = document.getElementById('image-dialog'), viewer = document.getElementById('image-full');
   if (!dialog || !viewer || typeof dialog.showModal !== 'function' || new URL(full).hostname === 'bsky.app') return;
   event.preventDefault(); viewer.src = full; viewer.alt = image.alt || ''; dialog.showModal();
  });
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

const imageDialog = document.getElementById('image-dialog');
if (imageDialog) {
 document.getElementById('image-close').addEventListener('click', () => imageDialog.close());
 imageDialog.addEventListener('click', event => { if (event.target === imageDialog) imageDialog.close(); });
 imageDialog.addEventListener('close', () => { document.getElementById('image-full').removeAttribute('src'); });
}
document.querySelectorAll('.share-post').forEach(button => button.addEventListener('click', async () => {
 const url = safeHttps(button.dataset.url);
 if (!url) return;
 try {
  if (navigator.share) await navigator.share({url});
  else if (navigator.clipboard) { await navigator.clipboard.writeText(url); button.title = 'Link copied'; button.setAttribute('aria-label', 'Link copied'); setTimeout(() => { button.title = 'Share post'; button.setAttribute('aria-label', 'Share post'); }, 2500); }
  else button.closest('.post').querySelector('.social-status').textContent = 'Sharing is unavailable in this browser.';
 } catch (_) { /* Sharing was cancelled or unavailable. */ }
}));

const socialDialog = document.getElementById('social-dialog');
const socialForm = document.getElementById('social-form');
let socialTarget = null;
const ownComments = new Map();
const deletedComments = new Set();
const deletedQuotes = new Set();
async function deleteContribution(post, uri, parentUri, kind) {
 const payload = new FormData(); payload.set('csrf', document.querySelector('input[name="csrf"]').value);
 payload.set('post_key', post.dataset.postKey); payload.set('comment_uri', uri); payload.set('parent_uri', parentUri); payload.set('kind', kind);
 const response = await fetch('/api/comment/delete', {method:'POST', body:payload});
 const result = await response.json();
 if (!response.ok) throw new Error(result.detail || 'Could not delete your post');
}
function quoteDeleteButton(post, uri, parentUri, onDeleted) {
 if (!uri) return null;
 const button = document.createElement('button'); button.type = 'button'; button.className = 'comment-delete'; button.textContent = 'Delete'; button.setAttribute('aria-label', 'Delete your quote post');
 button.addEventListener('click', async () => {
  if (!window.confirm('Delete your quote post from Bluesky?')) return;
  button.disabled = true;
  try { await deleteContribution(post, uri, parentUri, 'quote'); deletedQuotes.add(uri); onDeleted(); }
  catch (error) { post.querySelector('.social-status').textContent = error.message; button.disabled = false; }
 });
 return button;
}
function renderContribution(post, item) {
 if (!item || !['comment', 'quote'].includes(item.kind) || typeof item.text !== 'string' || deletedQuotes.has(item.uri)) return;
 if (item.kind === 'comment') {
  const previous = ownComments.get(post) || [];
  if (!item.uri || !previous.some(comment => comment.uri === item.uri)) previous.push({ ...item, author:'You', is_self:true, reply_count:0, state:{like:false, repost:false, save:false} });
  ownComments.set(post, previous);
  const toggle = post.querySelector('.comments-toggle'); if (toggle) toggle.hidden = false;
  if (!post.querySelector('.comments-list').hidden) renderComments(post);
  return;
 }
 const target = post.querySelector('.quote-contributions');
 if (item.uri && [...target.children].some(node => node.dataset.uri === item.uri)) return;
 const entry = document.createElement('div'); entry.className = 'contribution';
 if (item.uri) entry.dataset.uri = item.uri;
 const label = document.createElement('span'); label.className = 'contribution-label'; label.textContent = 'Your quote post';
 const content = document.createElement('div'); content.textContent = item.text;
 const remove = quoteDeleteButton(post, item.uri, post.dataset.postKey.slice('bluesky:'.length), () => entry.remove());
 entry.append(label, content); if (remove) entry.append(remove); target.append(entry);
}
function setRepostState(post, active) {
 const option = post.querySelector('.repost-choice[data-action="repost"]');
 option.setAttribute('aria-pressed', String(active)); option.textContent = active ? 'Undo repost' : 'Repost';
 post.querySelector('.repost-menu-open').classList.toggle('is-reposted', active);
 const target = post.querySelector('.quote-contributions'); let marker = target.querySelector('.direct-repost');
 if (active && !marker) { marker = document.createElement('div'); marker.className = 'contribution contribution-label direct-repost'; marker.textContent = 'You reposted'; target.prepend(marker); }
 if (!active && marker) marker.remove();
}
async function sendSocial(post, action, text = '') {
 const status = post.querySelector('.social-status');
 const body = new FormData();
 body.set('csrf', document.querySelector('input[name="csrf"]').value);
 body.set('post_key', post.dataset.postKey); body.set('action', action); body.set('text', text);
 const response = await fetch('/api/social-action', {method:'POST', body});
 const result = await response.json();
 if (!response.ok) throw new Error(result.detail || 'Could not complete action');
 const button = post.querySelector(`.social-interact[data-action="${action}"]`);
 if (button?.hasAttribute('aria-pressed')) button.setAttribute('aria-pressed', String(result.active));
 if (action === 'repost') setRepostState(post, result.active);
 if (action === 'comment' || action === 'quote') renderContribution(post, {kind:action, text, uri:result.record_uri});
 if (action === 'comment') { post.dataset.replyCount = String((Number(post.dataset.replyCount) || 0) + 1); showComments(post); }
 status.textContent = action === 'comment' ? 'Comment posted' : action === 'quote' ? 'Repost with comment posted' : '';
}
const blueskyCards = postCards.filter(post => post.querySelector('.post-actions')?.dataset.platform === 'bluesky');
postCards.filter(post => !blueskyCards.includes(post)).forEach(post => { post.querySelector('.social-status').textContent = 'X interactions are not connected yet.'; });
if (blueskyCards.length) fetch('/api/social-state', {cache:'no-store', headers:{Accept:'application/json'}})
 .then(async response => { if (!response.ok || response.redirected) throw new Error(); return response.json(); })
 .then(data => blueskyCards.forEach(post => {
  const state = data.items[post.dataset.postKey.slice('bluesky:'.length)];
  if (!state) { post.querySelector('.social-status').textContent = 'Post unavailable on Bluesky.'; return; }
  post.querySelector('.repost-menu-open').disabled = false;
  setRepostState(post, !!state.repost);
  (data.contributions?.[post.dataset.postKey.slice('bluesky:'.length)] || []).forEach(item => renderContribution(post, item));
  post.querySelectorAll('.social-interact').forEach(button => {
   button.disabled = false;
   if (button.hasAttribute('aria-pressed')) button.setAttribute('aria-pressed', String(!!state[button.dataset.action]));
  });
 }))
 .catch(() => blueskyCards.forEach(post => { post.querySelector('.social-status').textContent = 'Reconnect Bluesky in settings to enable interactions.'; }));
blueskyCards.forEach(post => post.querySelectorAll('.social-interact').forEach(button => button.addEventListener('click', async () => {
 const action = button.dataset.action;
 const menu = post.querySelector('.repost-menu');
 menu.hidden = true; post.querySelector('.repost-menu-open').setAttribute('aria-expanded', 'false');
 if (action === 'comment' || action === 'quote') {
  socialTarget = {post, action};
  document.getElementById('social-title').textContent = action === 'comment' ? 'Comment' : 'Repost with comment';
  document.getElementById('social-text').value = '';
  socialForm.querySelector('.social-dialog-status').textContent = '';
  socialDialog.showModal(); document.getElementById('social-text').focus(); return;
 }
 button.disabled = true;
 try { await sendSocial(post, action); } catch (error) { post.querySelector('.social-status').textContent = error.message; }
 finally { button.disabled = false; }
})));
document.querySelectorAll('.repost-control').forEach(control => {
 const opener = control.querySelector('.repost-menu-open'), menu = control.querySelector('.repost-menu');
 opener.addEventListener('click', () => {
  const open = menu.hidden;
  document.querySelectorAll('.repost-menu').forEach(other => { other.hidden = true; other.parentElement.querySelector('.repost-menu-open').setAttribute('aria-expanded', 'false'); });
  menu.hidden = !open; opener.setAttribute('aria-expanded', String(open));
  if (open) menu.querySelector('button').focus();
 });
 document.addEventListener('click', event => { if (!control.contains(event.target)) { menu.hidden = true; opener.setAttribute('aria-expanded', 'false'); } });
 control.addEventListener('keydown', event => { if (event.key === 'Escape' && !menu.hidden) { menu.hidden = true; opener.setAttribute('aria-expanded', 'false'); opener.focus(); event.stopPropagation(); } });
});
if (socialDialog) {
 document.getElementById('social-close').addEventListener('click', () => socialDialog.close());
 socialForm.addEventListener('submit', async event => {
  event.preventDefault(); if (!socialTarget) return;
  const button = socialForm.querySelector('[type=submit]'); button.disabled = true;
  try { const value = document.getElementById('social-text').value; if (socialTarget.submit) await socialTarget.submit(value); else await sendSocial(socialTarget.post, socialTarget.action, value); socialDialog.close(); }
  catch (error) { socialForm.querySelector('.social-dialog-status').textContent = error.message; }
  finally { button.disabled = false; }
 });
}

const threadComments = new Map();
const COMMENT_PAGE_SIZE = 3;
function renderCommentBatch(post, container, items, parentUri, total) {
 container.replaceChildren();
 const visible = items.filter(item => !deletedComments.has(item.uri));
 let shown = 0;
 function addMore() {
  const end = Math.min(shown + COMMENT_PAGE_SIZE, visible.length);
  for (; shown < end; shown++) container.insertBefore(makeComment(post, visible[shown], parentUri), more);
  more.hidden = shown >= visible.length;
 }
 const more = document.createElement('button'); more.type = 'button'; more.className = 'thread-more'; more.textContent = 'Show more comments';
 more.addEventListener('click', addMore); container.append(more); addMore();
 if (!visible.length) { const empty = document.createElement('p'); empty.className = 'comments-note'; empty.textContent = 'No comments available.'; container.prepend(empty); }
 if (Number.isInteger(total) && total > items.length) {
  const note = document.createElement('p'); note.className = 'comments-note'; note.textContent = `Bluesky returned ${items.length} of ${total} replies.`; container.append(note);
 }
}
function makeComment(post, item, parentUri) {
 const wrapper = document.createElement('div'); wrapper.className = 'thread-item';
 const row = document.createElement('article'); row.className = 'comment-row'; if (item.uri) row.dataset.uri = item.uri;
 const avatar = safeHttps(item.avatar);
 if (avatar) { const img = sourceImage(avatar, ''); img.className = 'comment-avatar'; row.append(img); }
 const body = document.createElement('div'); body.className = 'comment-body';
 const head = document.createElement('div'); head.className = 'comment-head';
 const name = document.createElement('strong'); name.textContent = item.author; head.append(name);
 if (item.handle) { const handle = document.createElement('span'); handle.textContent = '@' + item.handle.replace(/^@/, ''); head.append(handle); }
 if (item.is_self && item.author !== 'You') { const self = document.createElement('span'); self.textContent = 'You'; head.append(self); }
 if (item.is_self && item.uri) {
  const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'comment-delete'; remove.textContent = 'Delete'; remove.setAttribute('aria-label', 'Delete your comment');
  remove.addEventListener('click', async () => {
   if (!window.confirm('Delete your comment from Bluesky?')) return;
   remove.disabled = true;
   try {
    await deleteContribution(post, item.uri, parentUri, 'comment');
    deletedComments.add(item.uri);
    ownComments.set(post, (ownComments.get(post) || []).filter(comment => comment.uri !== item.uri));
    wrapper.remove();
    if (parentUri === post.dataset.postKey.slice('bluesky:'.length)) {
     if (post.dataset.replyCount !== undefined) {
      const count = Math.max(0, Number(post.dataset.replyCount) - 1); post.dataset.replyCount = String(count);
      if (!count) { post.querySelector('.comments-toggle').hidden = true; post.querySelector('.comments-list').hidden = true; }
     }
    }
   } catch (error) { post.querySelector('.social-status').textContent = error.message; remove.disabled = false; }
  });
  head.append(remove);
 }
 const text = document.createElement('div'); text.className = 'source-text'; text.textContent = item.text; renderPostText(text, item.facets);
 body.append(head, text); row.append(body); wrapper.append(row);
 if (item.uri) {
  let replies = [];
  const toggle = document.createElement('button'); toggle.type = 'button'; toggle.className = 'thread-more'; toggle.hidden = !(item.reply_count > 0); toggle.textContent = `Show replies (${item.reply_count || 0})`; toggle.setAttribute('aria-expanded', 'false');
  const children = document.createElement('div'); children.className = 'thread-children'; children.hidden = true;
  async function openReplies() {
   children.hidden = false; toggle.hidden = false; toggle.setAttribute('aria-expanded', 'true'); toggle.textContent = 'Hide replies';
   if (replies.length) renderCommentBatch(post, children, replies, item.uri, null); else children.textContent = 'Loading replies?';
   try {
    const response = await fetch('/api/comments?' + new URLSearchParams({post_key:post.dataset.postKey, parent_uri:item.uri}), {cache:'no-store', headers:{Accept:'application/json'}});
    if (!response.ok || response.redirected) throw new Error();
    const data = await response.json();
    for (const own of replies) if (!data.items.some(reply => own.uri && reply.uri === own.uri)) data.items.push(own);
    replies = data.items; renderCommentBatch(post, children, replies, item.uri, data.total);
   } catch (_) {
    if (replies.length) renderCommentBatch(post, children, replies, item.uri, null);
    else children.textContent = 'Could not load replies. Close and open to try again.';
   }
  }
  toggle.addEventListener('click', () => {
   if (!children.hidden) { children.hidden = true; toggle.setAttribute('aria-expanded', 'false'); toggle.textContent = `Show replies (${item.reply_count || 0})`; }
   else openReplies();
  });
  const quotes = document.createElement('div'); quotes.className = 'comment-quotes';
  function addQuote(quote) {
   if (deletedQuotes.has(quote.uri)) return;
   if (quote.uri && [...quotes.children].some(node => node.dataset.uri === quote.uri)) return;
   const entry = document.createElement('div'); entry.className = 'contribution'; if (quote.uri) entry.dataset.uri = quote.uri;
   const label = document.createElement('span'); label.className = 'contribution-label'; label.textContent = 'Your quote post';
   const content = document.createElement('div'); content.textContent = quote.text;
   const remove = quoteDeleteButton(post, quote.uri, item.uri, () => { entry.remove(); row.classList.toggle('quoted-comment', !!quotes.children.length || !!item.state?.repost); });
   entry.append(label, content); if (remove) entry.append(remove); quotes.append(entry);
   row.classList.add('quoted-comment');
  }
  (item.quotes || []).forEach(addQuote); wrapper.prepend(quotes);
  const directMarker = document.createElement('div'); directMarker.className = 'contribution-label'; directMarker.textContent = 'You reposted'; directMarker.hidden = !item.state?.repost; wrapper.prepend(directMarker);
  row.classList.toggle('quoted-comment', !!quotes.children.length || !!item.state?.repost);
  const actions = document.createElement('div'); actions.className = 'comment-actions';
  const paths = {
   comment:'M21 11.5a8.5 8.5 0 0 1-8.5 8.5H5l1.6-3.2A8.5 8.5 0 1 1 21 11.5Z',
   repost:'M17 3l4 4-4 4M3 11V9a2 2 0 0 1 2-2h16M7 21l-4-4 4-4M21 13v2a2 2 0 0 1-2 2H3',
   like:'M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8Z',
   save:'M6 3h12v18l-6-4-6 4Z', share:'M12 16V3m-5 5 5-5 5 5M5 13v7h14v-7'
  };
  function iconButton(action, label) {
   const button = document.createElement('button'); button.type = 'button'; button.className = 'social-action'; button.title = label; button.setAttribute('aria-label', label);
   const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true');
   const path = document.createElementNS('http://www.w3.org/2000/svg', 'path'); path.setAttribute('d', paths[action]); svg.append(path); button.append(svg); return button;
  }
  const message = document.createElement('p'); message.className = 'social-status'; message.setAttribute('role', 'status');
  async function interact(action, text = '') {
   const payload = new FormData(); payload.set('csrf', document.querySelector('input[name="csrf"]').value);
   payload.set('post_key', post.dataset.postKey); payload.set('target_uri', item.uri); payload.set('action', action); payload.set('text', text);
   const response = await fetch('/api/social-action', {method:'POST', body:payload}); const result = await response.json();
   if (!response.ok) throw new Error(result.detail || 'Could not complete action');
   message.textContent = ''; return result;
  }
  function compose(action) {
   socialTarget = {post, action, submit:async value => {
    const result = await interact(action, value);
    if (action === 'comment') {
     replies.push({uri:result.record_uri, text:value, author:'You', is_self:true, reply_count:0, state:{like:false, repost:false, save:false}});
     item.reply_count = (item.reply_count || 0) + 1; openReplies();
    } else addQuote({uri:result.record_uri, text:value});
   }};
   document.getElementById('social-title').textContent = action === 'comment' ? 'Comment' : 'Repost with comment';
   document.getElementById('social-text').value = ''; socialForm.querySelector('.social-dialog-status').textContent = '';
   socialDialog.showModal(); document.getElementById('social-text').focus();
  }
  const comment = iconButton('comment', 'Reply'); comment.disabled = !item.state; comment.addEventListener('click', () => compose('comment')); actions.append(comment);
  const repostControl = document.createElement('div'); repostControl.className = 'repost-control';
  const repost = iconButton('repost', 'Repost options'); repost.classList.add('repost-menu-open'); repost.disabled = !item.state; repost.setAttribute('aria-expanded', 'false'); repost.setAttribute('aria-haspopup', 'true'); repost.classList.toggle('is-reposted', !!item.state?.repost);
  const menu = document.createElement('div'); menu.className = 'repost-menu'; menu.hidden = true;
  const direct = document.createElement('button'); direct.type = 'button'; direct.className = 'repost-choice'; direct.textContent = item.state?.repost ? 'Undo repost' : 'Repost';
  const quote = document.createElement('button'); quote.type = 'button'; quote.className = 'repost-choice'; quote.textContent = 'Quote post';
  function closeMenu() { menu.hidden = true; repost.setAttribute('aria-expanded', 'false'); }
  repost.addEventListener('click', () => { menu.hidden = !menu.hidden; repost.setAttribute('aria-expanded', String(!menu.hidden)); if (!menu.hidden) direct.focus(); });
  repostControl.addEventListener('focusout', event => { if (!repostControl.contains(event.relatedTarget)) closeMenu(); });
  repostControl.addEventListener('keydown', event => { if (event.key === 'Escape' && !menu.hidden) { closeMenu(); repost.focus(); event.stopPropagation(); } });
  direct.addEventListener('click', async () => { closeMenu(); repost.disabled = true; try { const result = await interact('repost'); item.state.repost = result.active; repost.classList.toggle('is-reposted', result.active); directMarker.hidden = !result.active; row.classList.toggle('quoted-comment', result.active || !!quotes.children.length); direct.textContent = result.active ? 'Undo repost' : 'Repost'; } catch (error) { message.textContent = error.message; } finally { repost.disabled = false; } });
  quote.addEventListener('click', () => { closeMenu(); compose('quote'); }); menu.append(direct, quote); repostControl.append(repost, menu); actions.append(repostControl);
  for (const [action, label] of [['like', 'Like'], ['save', 'Save']]) {
   const button = iconButton(action, label); button.disabled = !item.state; button.setAttribute('aria-pressed', String(!!item.state?.[action]));
   button.addEventListener('click', async () => { button.disabled = true; try { const result = await interact(action); item.state[action] = result.active; button.setAttribute('aria-pressed', String(result.active)); } catch (error) { message.textContent = error.message; } finally { button.disabled = false; } }); actions.append(button);
  }
  const share = iconButton('share', 'Share'); share.addEventListener('click', async () => {
   const parts = /^at:\/\/(did:[^/]+)\/app\.bsky\.feed\.post\/([^/]+)$/.exec(item.uri);
   if (!parts) { message.textContent = 'Sharing is unavailable for this post.'; return; }
   const url = 'https://bsky.app/profile/' + encodeURIComponent(parts[1]) + '/post/' + encodeURIComponent(parts[2]);
   try { if (navigator.share) await navigator.share({url}); else if (navigator.clipboard) { await navigator.clipboard.writeText(url); message.textContent = 'Link copied'; } else message.textContent = 'Sharing is unavailable in this browser.'; } catch (_) { /* Cancelled. */ }
  }); actions.append(share);
  if (!item.state) message.textContent = 'Reconnect Bluesky in settings to enable interactions.';
  body.append(actions, message, toggle, children);
 }

 return wrapper;
}
function renderComments(post) {
 const section = post.querySelector('.comments-list');
 const available = threadComments.get(post) || {items:[], total:null};
 const items = [...available.items];
 for (const own of ownComments.get(post) || []) {
  if (!own.uri || !items.some(item => item.uri === own.uri)) items.push(own);
 }
 renderCommentBatch(post, section, items, post.dataset.postKey.slice('bluesky:'.length), available.total);
}
async function showComments(post) {
 const button = post.querySelector('.comments-toggle'), section = post.querySelector('.comments-list');
 section.hidden = false; button.setAttribute('aria-expanded', 'true'); button.textContent = 'Hide comments';
 if (ownComments.get(post)?.length) renderComments(post); else section.textContent = 'Loading comments?';
 try {
  const response = await fetch('/api/comments?' + new URLSearchParams({post_key:post.dataset.postKey}), {cache:'no-store', headers:{Accept:'application/json'}});
  if (!response.ok || response.redirected) throw new Error();
  threadComments.set(post, await response.json()); renderComments(post);
 } catch (_) {
  if (ownComments.get(post)?.length) { renderComments(post); const note = document.createElement('p'); note.className = 'comments-note'; note.textContent = 'Other comments could not be loaded.'; section.append(note); }
  else section.textContent = 'Could not load comments. Close and open to try again.';
 }
}
document.querySelectorAll('.comments-toggle').forEach(button => button.addEventListener('click', () => {
 const post = button.closest('.post'), section = post.querySelector('.comments-list');
 if (section.hidden) showComments(post);
 else { section.hidden = true; button.setAttribute('aria-expanded', 'false'); button.textContent = 'Show comments'; }
}));
