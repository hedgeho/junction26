(function () {
 let savedTheme;
 try { savedTheme = localStorage.getItem('agora-theme'); } catch (_) {}
 const theme = savedTheme === 'light' || savedTheme === 'dark'
  ? savedTheme : (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
 document.documentElement.dataset.theme = theme;
 document.addEventListener('DOMContentLoaded', () => {
  const toggle = document.querySelector('.theme-toggle');
  if (!toggle) return;
  const update = () => {
   const dark = document.documentElement.dataset.theme === 'dark';
   toggle.setAttribute('aria-label', `Switch to ${dark ? 'light' : 'dark'} theme`);
   toggle.title = `Switch to ${dark ? 'light' : 'dark'} theme`;
   document.querySelector('meta[name="theme-color"]').content = dark ? '#151a12' : '#f1f2ec';
  };
  toggle.addEventListener('click', () => {
   const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
   document.documentElement.dataset.theme = next;
   try { localStorage.setItem('agora-theme', next); } catch (_) {}
   update();
  });
  update();
 });
})();
