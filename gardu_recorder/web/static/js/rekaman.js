// Player Rekaman: dua <video> bergantian. Satu memutar menit sekarang, satu memuat menit berikutnya diam-diam;
// saat 'ended', keduanya ditukar sehingga sambungan antar segmen hampir tak terasa.
(() => {
  const root = document.getElementById('player');
  if (!root) return;
  let [cur, nxt] = root.querySelectorAll('video');
  const msg = document.getElementById('r-msg');
  const now = document.getElementById('r-now');
  const dl = document.getElementById('r-dl');
  const toggle = document.getElementById('r-toggle');
  const steps = document.querySelectorAll('[data-step]');
  let list = [];          // [{file, t, size}] satu hari, terurut
  let idx = -1;           // segmen yang sedang diputar
  let nextIdx = -1;       // segmen yang sudah dimuat di `nxt`
  let rate = 1;

  const url = (i) => root.dataset.base + list[i].file;
  const say = (text) => { msg.textContent = text; msg.classList.toggle('hide', !text); };

  async function refresh() {
    try {
      const r = await fetch(root.dataset.list, { headers: { Accept: 'application/json' } });
      if (r.ok) list = await r.json();
    } catch { /* jaringan putus: pakai daftar lama */ }
  }

  function preload() {
    nextIdx = idx + 1 < list.length ? idx + 1 : -1;
    if (nextIdx >= 0) {
      nxt.src = url(nextIdx);
      nxt.load();
    } else {
      nxt.removeAttribute('src');
    }
  }

  const prog = document.getElementById('r-prog');
  const track = prog.querySelector('.track');
  const fill = track.querySelector('span');
  const startSec = (seg) => Number(seg.file.slice(6, 8));      // segmen hasil restart bisa mulai di tengah menit

  function mark() {
    document.querySelectorAll('#mins .s').forEach((e) => e.classList.remove('s'));
    const seg = list[idx];
    if (!seg) return;
    document.querySelector(`#mins [data-t="${seg.t}"]`)?.classList.add('s');
  }

  // Progress bar di bawah player: posisi di dalam menit yang sedang diputar (:00–:59).
  function progress() {
    const seg = list[idx];
    if (!seg) return;
    const sec = Math.min(startSec(seg) + (cur.currentTime || 0), 60);
    fill.style.width = `${(sec / 60 * 100).toFixed(1)}%`;
    track.setAttribute('aria-valuenow', Math.floor(sec));
  }

  const hour = (seg) => Number(seg.t.slice(0, 2));
  const showHour = (h) => window.htmx && htmx.ajax('GET', `${root.dataset.timeline}&jam=${h}`,
    { target: '#timeline', swap: 'outerHTML' });

  // Pemutaran menyeberang jam (mis. 14:59 → 15:00) saat grid sedang menampilkan jam lama: ikut pindah jam.
  function follow(from, to) {
    const timeline = document.getElementById('timeline');
    const [a, b] = [hour(from), hour(to)];
    if (timeline && a !== b && Number(timeline.dataset.jam) === a) showHour(b);
  }

  function clock() {
    const seg = list[idx];
    if (!seg) return;
    const [h, m, s] = seg.file.slice(0, 8).split('-').map(Number);
    const t = h * 3600 + m * 60 + s + Math.floor(cur.currentTime || 0);
    const pad = (n) => String(n).padStart(2, '0');
    now.textContent = `${root.dataset.label} · ${pad(Math.floor(t / 3600) % 24)}:${pad(Math.floor(t / 60) % 60)}:${pad(t % 60)}`;
  }

  function play(i) {
    if (i < 0 || i >= list.length) return;
    if (i === nextIdx) {
      [cur, nxt] = [nxt, cur];
    } else {
      cur.src = url(i);
    }
    idx = i;
    cur.classList.remove('hide');
    nxt.classList.add('hide');
    nxt.pause();
    cur.playbackRate = rate;
    cur.play().catch(() => {});
    say('');
    dl.href = `${url(i)}?unduh=1`;
    dl.classList.remove('hide');
    toggle.disabled = false;
    steps.forEach((b) => { b.disabled = false; });
    prog.classList.remove('off');
    document.getElementById('r-from').textContent = `${list[i].t}:00`;
    document.getElementById('r-to').textContent = `${list[i].t}:59`;
    clock();
    mark();
    progress();
    preload();
  }

  async function advance() {
    if (idx + 1 >= list.length && root.dataset.live === '1') await refresh();   // hari ini: mungkin ada segmen baru
    if (idx + 1 < list.length) {
      follow(list[idx], list[idx + 1]);
      play(idx + 1);
    } else if (root.dataset.next) {
      // Lewat 23:59: lanjut ke tanggal berikutnya (halaman baru), dengan kecepatan yang sama
      say('Lanjut ke tanggal berikutnya…');
      location.href = `${root.dataset.next}&putar=1&laju=${rate}`;
    } else {
      say('Akhir rekaman di tanggal ini.');
    }
  }

  for (const v of [cur, nxt]) {
    v.defaultPlaybackRate = 1;
    v.addEventListener('ended', () => { if (v === cur) advance(); });
    v.addEventListener('timeupdate', () => { if (v === cur) { clock(); progress(); } });
    v.addEventListener('play', () => { if (v === cur) toggle.textContent = '❚❚ Jeda'; });
    v.addEventListener('pause', () => { if (v === cur) toggle.textContent = '▶ Putar'; });
    v.addEventListener('error', () => {
      if (v !== cur || !v.getAttribute('src')) return;
      const code = v.error?.code;
      say(code === 3 || code === 4
        ? 'Format video tidak didukung browser ini (H.265). Unduh segmen lalu putar di VLC.'
        : 'Segmen gagal dimuat.');
    });
  }

  document.addEventListener('click', async (evt) => {
    const minute = evt.target.closest('#mins [data-t]');
    if (minute) {
      let i = list.findIndex((s) => s.t === minute.dataset.t);
      if (i < 0) {
        await refresh();
        i = list.findIndex((s) => s.t === minute.dataset.t);
      }
      play(i);
      return;
    }
    if (evt.target.closest('#r-prog .track') && list[idx] && cur.getAttribute('src')) {
      const box = track.getBoundingClientRect();
      const sec = ((evt.clientX - box.left) / box.width) * 60 - startSec(list[idx]);
      cur.currentTime = Math.max(0, Math.min(sec, (cur.duration || 60) - 0.1));
      progress();
      return;
    }
    const step = evt.target.closest('[data-step]');
    if (step) {
      const i = idx + Number(step.dataset.step);
      if (list[idx] && list[i]) follow(list[idx], list[i]);
      play(i);
      return;
    }
    const speed = evt.target.closest('[data-rate]');
    if (speed) {
      setRate(speed.dataset.rate);
      return;
    }
    if (evt.target === toggle && cur.getAttribute('src')) {
      if (cur.paused) cur.play().catch(() => {});
      else cur.pause();
    }
  });

  function setRate(r) {
    const btn = document.querySelector(`[data-rate="${r}"]`);
    if (!btn) return;
    rate = Number(r);
    for (const v of [cur, nxt]) {
      v.defaultPlaybackRate = rate;
      v.playbackRate = rate;
    }
    document.querySelectorAll('[data-rate]').forEach((b) => b.classList.toggle('on', b === btn));
  }

  document.addEventListener('htmx:afterSettle', mark);     // grid jam baru: tandai menit yang sedang diputar

  // Datang dari tanggal sebelumnya (?putar=1): langsung putar segmen pertama hari ini.
  const params = new URLSearchParams(location.search);
  const autoplay = params.get('putar') === '1';
  if (autoplay) {
    setRate(params.get('laju'));
    params.delete('putar');
    params.delete('laju');
    history.replaceState(null, '', `${location.pathname}?${params}`);   // muat ulang tidak memutar otomatis lagi
  }
  refresh().then(() => {
    if (!autoplay || !list.length) return;
    play(0);
    const timeline = document.getElementById('timeline');
    if (timeline && Number(timeline.dataset.jam) !== hour(list[0])) showHour(hour(list[0]));
  });
})();
