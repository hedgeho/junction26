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
const pending = document.getElementById('curation-pending');
if (pending) pollStatus('/api/curation', pending, result => result.state === 'ready', result => {
 if (result.state === 'ready') {
  const dialog = document.getElementById('feedback-dialog');
  if (dialog?.open) dialog.addEventListener('close', () => location.reload(), {once:true});
  else location.reload();
 } else pending.textContent = result.label;
});
const job = document.getElementById('job-status');
if (job && ['queued','processing'].includes(job.dataset.state)) {
 const labels = {queued:'Queued', processing:'Updating', applied:'Updated', failed:'Update failed. Please try again.'};
 pollStatus('/api/jobs/' + job.dataset.job, job, result => ['applied','failed'].includes(result.state), result => {job.textContent = labels[result.state] || 'Queued';});
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
