// Dimuat dari file (bukan <script> inline) agar lolos CSP script-src 'self'.
document.addEventListener('htmx:beforeSwap', (evt) => {
  const xhr = evt.detail.xhr;
  // 400/422: form dirender ulang dengan pesan error. HX-Retarget: server sengaja mengirim fragment
  // (toast / modal) untuk status lain (403/404/429/5xx).
  if (xhr.status === 400 || xhr.status === 422 || xhr.getResponseHeader('HX-Retarget')) {
    evt.detail.shouldSwap = true;
    evt.detail.isError = false;
  }
});

// Modal: tombol/atribut [data-close] atau tombol Esc mengosongkan #modal.
const closeModal = () => document.getElementById('modal')?.replaceChildren();
document.addEventListener('click', (evt) => {
  if (evt.target.closest('[data-close]')) closeModal();
});
document.addEventListener('keydown', (evt) => {
  if (evt.key === 'Escape') closeModal();
});

// Toast hilang sendiri setelah 4,5 detik.
let toastTimer;
document.addEventListener('htmx:afterSettle', () => {
  const toast = document.getElementById('toast');
  if (toast && toast.textContent.trim()) {
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.replaceChildren(), 4500);
  }
});

// Filter GET (kamera / tanggal): ganti pilihan langsung kirim form, tanpa tombol.
document.addEventListener('change', (evt) => {
  if (evt.target.matches('[data-autosubmit]')) evt.target.form?.requestSubmit();
});
