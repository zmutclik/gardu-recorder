// Live View: tiap tile memutar HLS /live/<cam>/index.m3u8 (proxy Gardu → MediaMTX).
// hls.js (MSE) bila didukung; Safari memakai HLS bawaan. Gagal → coba ulang tiap RETRY_MS, jadi kamera yang baru
// online atau MediaMTX yang baru restart tersambung sendiri tanpa memuat ulang halaman.
(() => {
  const RETRY_MS = 5000;
  const tiles = document.querySelectorAll('.tile[data-live]');
  if (!tiles.length) return;
  const useHlsJs = window.Hls && Hls.isSupported();
  const native = document.createElement('video').canPlayType('application/vnd.apple.mpegurl');
  const UNSUPPORTED = 'Format video (H.265) tidak didukung browser ini.';
  const CODEC_ERRORS = window.Hls ? [Hls.ErrorDetails.MANIFEST_INCOMPATIBLE_CODECS_ERROR,
    Hls.ErrorDetails.BUFFER_ADD_CODEC_ERROR, Hls.ErrorDetails.BUFFER_INCOMPATIBLE_CODECS_ERROR] : [];

  function show(tile, state, text) {
    const tag = tile.querySelector('[data-tag]');
    const msg = tile.querySelector('[data-msg]');
    const live = state === 'live';
    tile.classList.toggle('offline', !live);
    tag.classList.toggle('off', !live);
    tag.textContent = live ? '● LIVE' : state === 'unsupported' ? 'TIDAK DIDUKUNG' : 'OFFLINE';
    msg.textContent = text || '';
    msg.classList.toggle('hide', live);
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
      hls.on(Hls.Events.MANIFEST_PARSED, () => video.play().catch(() => {}));
      hls.on(Hls.Events.ERROR, (_evt, data) => {
        if (!data.fatal) return;
        hls.destroy();
        tile.hls = null;
        if (CODEC_ERRORS.includes(data.details)) show(tile, 'unsupported', UNSUPPORTED);
        else retry(tile);
      });
      hls.loadSource(src);
      hls.attachMedia(video);
    } else if (native) {
      video.src = src;
      video.play().catch(() => {});
    } else {
      show(tile, 'unsupported', UNSUPPORTED);
    }
  }

  for (const tile of tiles) {
    const video = tile.querySelector('video');
    video.addEventListener('playing', () => show(tile, 'live'));
    video.addEventListener('error', () => {      // jalur HLS bawaan (Safari)
      if (useHlsJs) return;
      if (video.error?.code === 4) show(tile, 'unsupported', UNSUPPORTED);
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
