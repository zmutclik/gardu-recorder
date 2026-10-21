// Live View: tiap tile memutar HLS /live/<cam>/index.m3u8 (proxy Gardu → MediaMTX).
// hls.js (MSE) bila didukung; Safari memakai HLS bawaan. Gagal → coba ulang tiap RETRY_MS, jadi kamera yang baru
// online atau MediaMTX yang baru restart tersambung sendiri tanpa memuat ulang halaman.
(() => {
  const RETRY_MS = 5000;
  const SNAP_MS = 3000;          // jeda antar foto di mode snapshot
  const DECODE_FAILS = 2;        // gagal memutar setelah playlist terbaca (umumnya H.265 tak bisa didekode) → mode foto
  const WATCHDOG_MS = 12000;     // playlist terbaca tapi video tak kunjung main = gagal dekode

  // Pilihan jumlah tampilan (1/4/9) disimpan di browser; buka /live tanpa ?n= memakai pilihan terakhir.
  const KEY = 'gardu.live.n';
  const seg = document.querySelectorAll('.seg [data-n]');
  const store = (fn) => { try { return fn(localStorage); } catch { return null; } };
  const saved = store((ls) => ls.getItem(KEY));
  const cur = document.querySelector('.seg .on')?.dataset.n;
  if (saved && saved !== cur && !new URLSearchParams(location.search).has('n')) {
    const target = [...seg].find((a) => a.dataset.n === saved);
    if (target) { location.replace(target.href); return; }
  }
  for (const a of seg) a.addEventListener('click', () => store((ls) => ls.setItem(KEY, a.dataset.n)));

  const tiles = document.querySelectorAll('.tile[data-live]');
  if (!tiles.length) return;
  const useHlsJs = window.Hls && Hls.isSupported();
  const native = document.createElement('video').canPlayType('application/vnd.apple.mpegurl');
  const CODEC_ERRORS = window.Hls ? [Hls.ErrorDetails.MANIFEST_INCOMPATIBLE_CODECS_ERROR,
    Hls.ErrorDetails.BUFFER_ADD_CODEC_ERROR, Hls.ErrorDetails.BUFFER_INCOMPATIBLE_CODECS_ERROR] : [];

  function show(tile, state, text) {
    const tag = tile.querySelector('[data-tag]');
    const msg = tile.querySelector('[data-msg]');
    const live = state === 'live' || state === 'snapshot';
    tile.classList.toggle('offline', !live);
    tag.classList.toggle('off', !live);
    tag.textContent = state === 'snapshot' ? '◐ FOTO' : live ? '● LIVE' : state === 'unsupported' ? 'TIDAK DIDUKUNG' : 'OFFLINE';
    msg.textContent = text || '';
    msg.classList.toggle('hide', live);
  }

  // Mode foto: browser tak bisa memutar stream (mis. H.265 di Chrome Android) → JPEG /cameras/<id>/snapshot
  // dimuat berulang. Bukan video mulus, tapi tak butuh dekoder apa pun.
  function snapshotMode(tile) {
    clearTimeout(tile.retryTimer);
    clearTimeout(tile.watchdog);
    tile.hls?.destroy();
    tile.hls = null;
    tile.snapshot = true;
    const video = tile.querySelector('video');
    video.removeAttribute('src');
    video.load();
    const img = document.createElement('img');
    img.className = 'snapimg';
    img.alt = '';
    tile.prepend(img);
    const w = tile.clientWidth * (window.devicePixelRatio || 1);
    const size = w <= 320 ? 320 : w <= 640 ? 640 : 960;
    const next = (ms) => { tile.retryTimer = setTimeout(load, ms); };
    function load() {
      if (document.hidden) return next(SNAP_MS);
      img.src = `/cameras/${tile.dataset.cam}/snapshot?w=${size}&t=${Date.now()}`;
    }
    img.addEventListener('load', () => {
      tile.classList.add('snap');
      show(tile, 'snapshot');
      next(SNAP_MS);
    });
    img.addEventListener('error', () => {
      tile.classList.remove('snap');
      show(tile, 'retry', 'Foto tidak tersedia — mencoba ulang…');
      next(RETRY_MS);
    });
    load();
  }

  function decodeFailed(tile) {
    tile.decodeFails = (tile.decodeFails || 0) + 1;
    if (tile.decodeFails >= DECODE_FAILS) snapshotMode(tile);
    else retry(tile);
  }

  function retry(tile) {
    show(tile, 'retry', 'Live tidak tersedia — mencoba ulang…');
    clearTimeout(tile.retryTimer);
    tile.retryTimer = setTimeout(() => start(tile), RETRY_MS);
  }

  function start(tile) {
    const video = tile.querySelector('video');
    const src = tile.dataset.live;
    if (useHlsJs) {
      tile.hls?.destroy();
      // enableWorker false: worker dari blob: diblokir CSP (script-src 'self')
      const hls = new Hls({ enableWorker: false, liveSyncDurationCount: 2, backBufferLength: 10 });
      tile.hls = hls;
      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        video.play().catch(() => {});
        clearTimeout(tile.watchdog);
        tile.watchdog = setTimeout(() => {      // playlist terbaca, tapi video tak main: dekode gagal diam-diam
          hls.destroy();
          tile.hls = null;
          decodeFailed(tile);
        }, WATCHDOG_MS);
      });
      hls.on(Hls.Events.ERROR, (_evt, data) => {
        if (!data.fatal) return;
        clearTimeout(tile.watchdog);
        const parsed = !!hls.levels?.length;
        hls.destroy();
        tile.hls = null;
        if (CODEC_ERRORS.includes(data.details)) snapshotMode(tile);
        else if (parsed && data.type === Hls.ErrorTypes.MEDIA_ERROR) decodeFailed(tile);
        else retry(tile);
      });
      hls.loadSource(src);
      hls.attachMedia(video);
    } else if (native) {
      video.src = src;
      video.play().catch(() => {});
    } else {
      snapshotMode(tile);
    }
  }

  for (const tile of tiles) {
    const video = tile.querySelector('video');
    video.addEventListener('playing', () => {
      clearTimeout(tile.watchdog);
      tile.decodeFails = 0;
      show(tile, 'live');
    });
    video.addEventListener('error', () => {      // jalur HLS bawaan (Safari)
      if (useHlsJs) return;
      if (video.error?.code === 4) snapshotMode(tile);
      else retry(tile);
    });
    start(tile);
  }

  // Tab disembunyikan: hentikan unduhan segmen; kembali: lompat ke ujung live.
  document.addEventListener('visibilitychange', () => {
    for (const tile of tiles) {
      if (!tile.hls) continue;
      if (document.hidden) tile.hls.stopLoad();
      else tile.hls.startLoad(-1);
    }
  });

  document.querySelector('[data-fullscreen]')?.addEventListener('click', () => {
    document.querySelector('#live-grid .tile')?.requestFullscreen?.();
  });
})();
