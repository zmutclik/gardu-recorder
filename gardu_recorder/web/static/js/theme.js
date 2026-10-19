// Tema terang/gelap. Dimuat tanpa defer di <head> agar data-theme terpasang sebelum render (tanpa kedip).
// Tanpa pilihan tersimpan, CSS mengikuti prefers-color-scheme sistem.
(() => {
  const KEY = 'gardu-theme';
  const root = document.documentElement;
  const read = () => { try { return localStorage.getItem(KEY); } catch { return null; } };
  const saved = read();
  if (saved === 'dark' || saved === 'light') root.dataset.theme = saved;

  const current = () => root.dataset.theme
    || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  const label = () => {
    document.querySelectorAll('[data-theme-toggle]').forEach((btn) => {
      const dark = current() === 'dark';
      btn.textContent = dark ? '☀' : '☾';
      btn.title = dark ? 'Mode terang' : 'Mode gelap';
      btn.setAttribute('aria-label', btn.title);
    });
  };

  document.addEventListener('DOMContentLoaded', label);
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', label);
  document.addEventListener('click', (evt) => {
    if (!evt.target.closest('[data-theme-toggle]')) return;
    const next = current() === 'dark' ? 'light' : 'dark';
    root.dataset.theme = next;
    try { localStorage.setItem(KEY, next); } catch { /* mode privat: tema berlaku sampai reload */ }
    label();
  });
})();
