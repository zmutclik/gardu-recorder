(function(){
var NOWM=14*60+37,GAPS=[[192,200],[545,547]],MB_MIN=23.3,PERDAY=10.8;
var CAMS=[{"id": "igd-pintu", "name": "IGD Pintu Masuk", "ip": "192.168.10.51", "dev": 12345678, "on": 1, "mbps": 3.1, "rs": 0, "last": "14:37:02", "fps": 16, "fpsd": 16, "usr": "admin", "q": 1, "st": 0}, {"id": "igd-lobby", "name": "IGD Lobby", "ip": "192.168.10.52", "dev": 23456789, "on": 1, "mbps": 3.0, "rs": 1, "last": "14:37:01", "fps": 16, "fpsd": 16, "usr": "admin", "q": 1, "st": 0}, {"id": "parkir", "name": "Parkir Depan", "ip": "192.168.10.53", "dev": 34567890, "on": 1, "mbps": 2.6, "rs": 2, "last": "14:37:02", "fps": 16, "fpsd": 16, "usr": "admin", "q": 1, "st": 0}, {"id": "poli", "name": "Koridor Poli", "ip": "192.168.10.54", "dev": 45678901, "on": 1, "mbps": 3.4, "rs": 0, "last": "14:37:00", "fps": 16, "fpsd": 25, "usr": "admin", "q": 1, "st": 0}, {"id": "apotek", "name": "Apotek", "ip": "192.168.10.55", "dev": 56789012, "on": 0, "mbps": 0, "rs": 14, "last": "10:00:12", "fps": 16, "fpsd": null, "usr": "admin", "q": 1, "st": 0}, {"id": "belakang", "name": "Gerbang Belakang", "ip": "192.168.10.56", "dev": 67890123, "on": 1, "mbps": 2.4, "rs": 0, "last": "14:37:02", "fps": 16, "fpsd": 16, "usr": "admin", "q": 1, "st": 0}];
var HIST=[{t:'14:05',cam:'IGD Lobby',range:'13:40–14:00',who:'Siti',size:'412 MB',ok:1},
 {t:'11:32',cam:'Parkir Depan',range:'11:00–12:00',who:'Budi',size:'1,3 GB',ok:1},
 {t:'09:10',cam:'Apotek',range:'08:00–10:00',who:'Siti',size:'—',ok:0}];
var LOGL=[['07:58','192.168.10.20','Login berhasil'],['07:41','192.168.10.31','Login gagal (3×)'],['Kemarin 22:10','192.168.10.20','Login berhasil']];
var LOGK=[['Kemarin 16:02','192.168.10.20','Ubah kamera Koridor Poli (fps 16)'],['17 Okt 09:30','192.168.10.20','Tambah kamera Gerbang Belakang']];
var ME='192.168.10.20';
function $(s,r){return (r||document).querySelector(s)}
function $$(s,r){return Array.prototype.slice.call((r||document).querySelectorAll(s))}
function pad(n){return ('0'+n).slice(-2)}
function hm(t){return pad(Math.floor(t/60))+':'+pad(t%60)}
function tm(v){if(!v)return NaN;var p=v.split(':');return +p[0]*60+ +p[1]}
function fmt(n){return Math.round(n).toString().replace(/\B(?=(\d{3})+(?!\d))/g,'.')}
function size(mb){return mb>=1000?(mb/1000).toFixed(1).replace('.',',')+' GB':Math.round(mb)+' MB'}
function slug(s){return s.toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'')}
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;')}
function dec(n){return String(n).replace('.',',')}
function gap(t){return GAPS.some(function(g){return t>=g[0]&&t<g[1]})}
function endOf(c){return c.on?NOWM:tm(c.last.slice(0,5))}
var tt=null;
function toast(t){var e=$('#toast');e.textContent=t;clearTimeout(tt);tt=setTimeout(function(){e.textContent=''},4500)}
function tag(c){return c.st?'<span class="tag off">MENGHUBUNGKAN…</span>':c.on?'<span class="tag">ONLINE</span>':'<span class="tag off">OFFLINE</span>'}
function mbTxt(c){return c.on?dec(c.mbps)+' Mbps':(c.st?'menghubungkan…':'tidak ada data')}

// ---------- uji kamera
var ICON={ok:'✔',warn:'⚠',fail:'✘',skip:'—'};
function steps(c){var s=[['Port 8800 terbuka','ok','nc -vz '+c.ip+' 8800']];
 if(!c.on){s.push(['Login (loginResult 1001)','fail','loginResult bukan 1001 → cocokkan Device ID '+c.dev+', username, dan password dengan aplikasi V380 Pro'],['Data video mengalir','skip',''],['FPS sesuai config','skip',''],['Segmen terakhir tertulis','skip','Terakhir '+c.last]);return s}
 s.push(['Login (loginResult 1001)','ok','memakai sesi pipeline yang berjalan'],['Data video mengalir','ok',dec(c.mbps)+' Mbps, data terakhir '+c.last]);
 s.push(c.fpsd&&c.fpsd!=c.fps?['FPS sesuai config','warn','Terdeteksi '+c.fpsd+' fps, config '+c.fps+' → durasi video akan salah. Ubah fps sesuai ffprobe']:['FPS sesuai config','ok',c.fps+' fps']);
 s.push(['Segmen terakhir tertulis','ok','14-37-00.mp4 (< 1 menit lalu)']);return s}
function mode(c){return c.on?'Kamera online: memeriksa pipeline yang berjalan (tanpa koneksi baru, kamera V380 membatasi jumlah koneksi).':'Kamera offline: mencoba login langsung ke kamera.'}
function verdict(st){var f=st.filter(function(x){return x[1]=='fail'})[0],w=st.filter(function(x){return x[1]=='warn'})[0];return f?'✘ Gagal di: '+f[0]:w?'⚠ '+w[0]+' perlu perhatian':'✔ Semua langkah OK'}
function runSteps(st,el,done){var ul=$('ul',el);st.forEach(function(x,i){setTimeout(function(){var li=document.createElement('li');li.className=x[1];li.innerHTML=ICON[x[1]]+' '+x[0]+(x[2]?'<span class="muted">'+esc(x[2])+'</span>':'');ul.appendChild(li);if(i==st.length-1&&done)done(verdict(st))},(i+1)*450)})}
function run(c,el,done){el.innerHTML='<div class="muted">'+mode(c)+'</div><ul class="ts"></ul>';runSteps(steps(c),el,done)}

// ---------- DASHBOARD
var sec=0,tick_=null,lgTab='e';
function rDash(){var on=CAMS.filter(function(c){return c.on}),off=CAMS.filter(function(c){return !c.on&&!c.st}),k=$('#k-on');
 k.classList.toggle('bad',off.length>0);
 k.innerHTML='<span>Kamera online</span><b>'+on.length+' / '+CAMS.length+'</b><span>'+(off.length?off.length+' offline: '+off.map(function(c){return esc(c.name)}).join(', '):'semua online')+'</span>';
 var tot=on.reduce(function(a,c){return a+c.mbps},0);
 $('#d-b').textContent=dec(tot.toFixed(1))+' Mbps';$('#d-gb').textContent='≈ '+Math.round(tot*PERDAY)+' GB / hari';
 $('#i-tay').textContent=on.length+' dari '+CAMS.length;$('#i-reg').textContent=CAMS.length;rLog()}
function rLog(){var rows;
 if(lgTab=='e')rows=HIST.map(function(h){return [h.t,h.who,'Ekspor '+h.cam+' '+h.range+(h.ok==2?' (memproses…)':h.ok?' ('+h.size+')':' — gagal: tidak ada rekaman')]});
 else rows=lgTab=='l'?LOGL:LOGK;
 $('#lg-body').innerHTML='<table class="tbl"><tr><th>Waktu</th><th>Pelaku</th><th>Kejadian</th></tr>'+rows.map(function(r){return '<tr><td>'+esc(r[0])+'</td><td>'+esc(r[1])+'</td><td>'+esc(r[2])+'</td></tr>'}).join('')+'</table>'}
function dtick(){sec+=10;var t=14*3600+37*60+sec;$('#d-t').textContent=pad(Math.floor(t/3600))+':'+pad(Math.floor(t%3600/60))+':'+pad(t%60);
 var on=CAMS.filter(function(c){return c.on}),tot=on.reduce(function(a,c){return a+c.mbps},0)+(Math.random()-.5)*.6;$('#d-b').textContent=dec(tot.toFixed(1))+' Mbps'}
function auto(){clearInterval(tick_);if($('#d-sw').classList.contains('on'))tick_=setInterval(dtick,10000)}
$('#d-sw').onclick=function(){this.classList.toggle('on');auto()};$('#d-r').onclick=dtick;auto();
$('#lg').onclick=function(e){var b=e.target.closest('button');if(!b)return;$$('#lg .btn').forEach(function(x){x.classList.remove('on')});b.classList.add('on');lgTab=b.dataset.l;rLog()};
$('#k-on').onclick=function(){location.hash='#/kamera'};

// ---------- LIVE VIEW
var gSel=0,gN=9;
function liveTile(c,i){if(c.st)return '<div class="tile offline" data-i="'+i+'"><span>Menghubungkan…</span><span class="nm">'+esc(c.name)+'</span><span class="st"><span class="tag off">BARU</span></span></div>';
 if(!c.on)return '<div class="tile offline" data-i="'+i+'"><span>⚠ Offline — mencoba ulang (restart '+c.rs+'×)</span><span class="nm">'+esc(c.name)+'</span><span class="st"><span class="tag off">OFFLINE</span></span></div>';
 return '<div class="tile" data-i="'+i+'"><span class="play">▶</span><span class="st"><span class="tag">● LIVE</span></span><span class="tm">'+c.last+'</span><span class="nm">'+esc(c.name)+'</span></div>'}
function rLive(){var g=$('#g-grid'),s=$('#g-strip');if(gSel>=CAMS.length)gSel=0;
 $('#g-n').textContent=CAMS.filter(function(c){return c.on}).length+' dari '+CAMS.length+' kamera online';
 g.className='grid g'+gN;
 g.innerHTML=CAMS.map(function(c,i){return (gN==1?i==gSel:i<gN)?liveTile(c,i):''}).join('');
 $$('#g-seg .btn').forEach(function(b){b.classList.toggle('on',+b.dataset.n==gN)});
 s.classList.toggle('hide',gN!=1);s.innerHTML=gN==1?CAMS.map(function(c,i){return i==gSel?'':liveTile(c,i)}).join(''):''}
$('#g-seg').onclick=function(e){var b=e.target.closest('button');if(!b)return;gN=+b.dataset.n;rLive()};
$('#g-full').onclick=function(){gN=1;rLive()};
$('#g-grid').onclick=function(e){var t=e.target.closest('.tile');if(!t)return;gSel=+t.dataset.i;gN=1;rLive()};
$('#g-strip').onclick=function(e){var t=e.target.closest('.tile');if(!t)return;gSel=+t.dataset.i;rLive()};

// ---------- REKAMAN
var rc=0,rh=14,rsel=null;
function rReset(){var e=endOf(CAMS[rc]);rsel=Math.max(0,e-10);rh=Math.floor(rsel/60)}
function rMins(){var c=CAMS[rc],end=endOf(c),o='';$('#h-title').textContent='Jam '+pad(rh)+':00–'+pad(rh)+':59';
 for(var i=0;i<60;i++){var t=rh*60+i;o+=t>end?'<i style="background:#eee;cursor:default"></i>':'<i class="'+(gap(t)?'g':'')+(t==rsel?' s':'')+'" data-t="'+t+'" title="'+hm(t)+'"></i>'}
 $('#mins').innerHTML=o;$('#r-now').textContent='18 Okt 2026 · '+hm(rsel)+':00'}
function rRek(){if(!CAMS.length)return;if(rc>=CAMS.length){rc=0;rReset()}var c=CAMS[rc],end=endOf(c);
 $('#r-cam').innerHTML=CAMS.map(function(x,i){return '<option value="'+i+'"'+(i==rc?' selected':'')+'>'+esc(x.name)+'</option>'}).join('');
 $('#r-nm').textContent=c.name;
 var h='';for(var hh=0;hh<24;hh++){var dis=hh*60>end,n=0;if(!dis)for(var t=hh*60;t<Math.min(hh*60+60,end+1);t++)if(!gap(t))n++;
  h+='<button class="btn'+(hh==rh?' on':'')+(dis?' dis':'')+'" data-h="'+hh+'">'+pad(hh)+'<small>'+(dis?'—':n+' mnt')+'</small></button>'}
 $('#hrs').innerHTML=h;rMins()}
rReset();
$('#r-cam').onchange=function(){rc=+this.value;rReset();rRek()};
$('#hrs').onclick=function(e){var b=e.target.closest('button');if(!b||b.classList.contains('dis'))return;rh=+b.dataset.h;if(Math.floor(rsel/60)!=rh)rsel=rh*60;$$('#hrs .btn').forEach(function(x){x.classList.remove('on')});b.classList.add('on');rMins()};
$('#mins').onclick=function(e){var c=e.target;if(c.tagName!='I'||!c.dataset.t)return;rsel=+c.dataset.t;rMins()};
$$('.seg.tg').forEach(function(s){if(s.id=='lg')return;s.onclick=function(e){if(e.target.tagName=='BUTTON'){$$('.btn',s).forEach(function(b){b.classList.remove('on')});e.target.classList.add('on')}}});

// ---------- EKSPOR
function xCheck(s,e,c){if(isNaN(s)||isNaN(e))return 'Isi jam mulai dan jam selesai.';if(e<=s)return 'Jam selesai harus setelah jam mulai.';if(e-s>120)return 'Rentang tidak valid (maksimal 2 jam).';if(s>=endOf(c))return 'Tidak ada rekaman di rentang itu.';return ''}
function xStat(s,e,c){var end=endOf(c),seg=0;for(var t=s;t<e&&t<=end;t++)if(!gap(t))seg++;return {d:e-s,seg:seg,mb:seg*MB_MIN}}
function fname(id,s,e){return id+'_20261018_'+hm(s).replace(':','')+'-'+hm(e).replace(':','')+'.mp4'}
function xCams(){var sel=$('#x-cam'),cur=sel.value;sel.innerHTML=CAMS.map(function(c){return '<option value="'+esc(c.id)+'">'+esc(c.name)+'</option>'}).join('');if(cur&&CAMS.some(function(c){return c.id==cur}))sel.value=cur}
function xUpd(){var c=CAMS.filter(function(x){return x.id==$('#x-cam').value})[0],btn=$('#x-btn');
 if(!c){$('#x-sum').innerHTML='<span>Belum ada kamera.</span>';btn.disabled=true;return}
 var s=tm($('#x-s').value),e=tm($('#x-e').value),er=xCheck(s,e,c);$('#x-err').textContent=er?'⚠ '+er:'';btn.disabled=!!er;
 $('#x-sum').innerHTML=er?'<span>Durasi<b>—</b></span><span>Segmen<b>—</b></span><span>Perkiraan ukuran<b>—</b></span>':(function(){var st=xStat(s,e,c);return '<span>Rentang<b>'+hm(s)+'–'+hm(e)+' ('+st.d+' mnt)</b></span><span>Segmen<b>'+st.seg+' file</b></span><span>Perkiraan ukuran<b>'+size(st.mb)+'</b></span><span>Nama file<b style="font-size:11px">'+esc(fname(c.id,s,e))+'</b></span>'})()}
function xTbl(){$('#x-c').textContent=HIST.filter(function(h){return h.ok==1}).length+' selesai';
 $('#x-tbl').innerHTML='<tr><th>Jam</th><th>Kamera</th><th>Rentang</th><th>Pemohon</th><th>Ukuran</th><th>Status</th><th></th></tr>'+HIST.map(function(h,i){
  var st=h.ok==2?'<span class="tag">Memproses…</span>':h.ok?'<span class="tag">Siap</span>':'<span class="tag off">Gagal: tidak ada rekaman di rentang itu</span>',
  ac=h.ok==2?'':h.ok?'<button class="btn sm">⬇ Unduh</button>':'<button class="btn sm" data-re="'+i+'">Ulangi</button>';
  return '<tr><td>'+h.t+'</td><td>'+esc(h.cam)+'</td><td>'+h.range+'</td><td>'+esc(h.who)+'</td><td>'+h.size+'</td><td>'+st+'</td><td>'+ac+'</td></tr>'}).join('')}
['x-cam','x-d','x-s','x-e'].forEach(function(id){var el=$('#'+id);el.oninput=el.onchange=xUpd});
$('#x-btn').onclick=function(){var n=$('#x-n').value.trim(),er=$('#x-err');
 if(!n){er.textContent='⚠ Nama pemohon wajib diisi (dicatat di log).';$('#x-n').focus();return}
 var c=CAMS.filter(function(x){return x.id==$('#x-cam').value})[0],s=tm($('#x-s').value),e=tm($('#x-e').value),st=xStat(s,e,c);
 var h={t:hm(NOWM),cam:c.name,range:hm(s)+'–'+hm(e),who:n,size:'—',ok:2};HIST.unshift(h);xTbl();rLog();
 setTimeout(function(){h.ok=1;h.size=size(st.mb);xTbl();rLog()},1800)};
$('#x-tbl').onclick=function(e){var b=e.target.closest('[data-re]');if(!b)return;var h=HIST[+b.dataset.re],c=CAMS.filter(function(x){return x.name==h.cam})[0];if(!c)return;
 $('#x-cam').value=c.id;var p=h.range.split('–');$('#x-s').value=p[0];$('#x-e').value=p[1];xUpd();window.scrollTo(0,0)};

// ---------- KAMERA
var kq='',ks='all';
function rKam(){$('#c2-n').textContent=CAMS.length+' kamera';
 $('#tbl').innerHTML='<tr><th></th><th>Kamera</th><th>Alamat</th><th>Kualitas</th><th>Status</th><th>Bitrate</th><th>Restart</th><th>Data terakhir</th><th></th></tr>'+CAMS.map(function(c,i){
  var t=(c.name+c.id+c.ip).toLowerCase();if(t.indexOf(kq)<0||(ks!='all'&&String(c.on?1:0)!=ks))return '';
  return '<tr data-i="'+i+'"><td><span class="th"></span></td><td><b>'+esc(c.name)+'</b><br><span class="muted">'+esc(c.id)+'</span></td><td>'+esc(c.ip)+':8800</td><td>'+(c.q?'HD':'SD')+' · '+c.fps+' fps</td><td>'+tag(c)+'</td><td>'+(c.on?dec(c.mbps)+' Mbps':'—')+'</td><td>'+c.rs+'×</td><td>'+c.last+'</td><td style="white-space:nowrap"><button class="btn sm" data-a="uji">Uji</button> <button class="btn sm" data-a="ubah">Ubah</button> <button class="btn sm danger" data-a="hapus">Hapus</button></td></tr><tr class="rs hide"><td colspan="9"></td></tr>'}).join('')}
$('#t-q').oninput=function(){kq=this.value.toLowerCase();rKam()};$('#t-s').onchange=function(){ks=this.value;rKam()};
var km=$('#km'),kd=$('#kd'),kmode='add',kidx=-1;
function F(k){return $('[data-f="'+k+'"]',km)}
function kIdv(){$('[data-id]',km).textContent=kmode=='add'?'ID kamera: '+(slug(F('name').value)||'(dari nama)'):'ID kamera: '+CAMS[kidx].id+' (tidak berubah)'}
function kOpen(i){kmode=i==null?'add':'edit';kidx=i==null?-1:i;var c=CAMS[i]||{name:'',ip:'',dev:'',usr:'admin',fps:16,q:1};
 $('#km-t').textContent=kmode=='add'?'Tambah kamera':'Ubah kamera · '+c.name;['name','ip','dev','usr','fps'].forEach(function(k){F(k).value=c[k]});F('q').value=c.q;F('pwd').value='';F('pwd').placeholder=kmode=='add'?'wajib':'kosongkan = tidak diubah';
 $('[data-res]',km).innerHTML='';$('[data-err]',km).textContent='';kIdv();km.classList.remove('hide')}
F('name').oninput=kIdv;
$$('[data-x]',km).forEach(function(b){b.onclick=function(){km.classList.add('hide')}});
$$('[data-x]',kd).forEach(function(b){b.onclick=function(){kd.classList.add('hide')}});
$('[data-test]',km).onclick=function(){var ip=F('ip').value.trim(),dev=F('dev').value.trim(),r=$('[data-res]',km);$('[data-err]',km).textContent='';
 if(!ip){$('[data-err]',km).textContent='⚠ Isi IP kamera dulu.';return}
 var st=[['Port 8800 terbuka','ok','nc -vz '+ip+' 8800']];
 if(!dev)st.push(['Login (loginResult 1001)','fail','Device ID kosong — lihat di aplikasi V380 Pro'],['Data video mengalir','skip','']);else st.push(['Login (loginResult 1001)','ok',''],['Data video mengalir','ok','H.265']);
 r.innerHTML='<ul class="ts"></ul>';runSteps(st,r)};
$('[data-save]',km).onclick=function(){var er=$('[data-err]',km),name=F('name').value.trim(),ip=F('ip').value.trim(),dev=F('dev').value.trim(),pw=F('pwd').value,id=kmode=='add'?slug(name):CAMS[kidx].id;
 if(!name||!ip||!dev){er.textContent='⚠ Nama, IP, dan Device ID wajib diisi.';return}
 if(kmode=='add'&&!pw){er.textContent='⚠ Password wajib diisi.';return}
 if(CAMS.some(function(c,i){return i!=kidx&&(c.id==id||c.ip==ip)})){er.textContent='⚠ ID atau IP sudah dipakai kamera lain.';return}
 var f=+F('fps').value||16;
 if(kmode=='add'){var c={id:id,name:name,ip:ip,dev:dev,usr:F('usr').value,q:+F('q').value,fps:f,fpsd:f,on:0,st:1,mbps:3.0,rs:0,last:'—'};CAMS.push(c);
  LOGK.unshift(['14:37',ME,'Tambah kamera '+name]);
  setTimeout(function(){c.st=0;c.on=1;c.last='14:37:05';all()},1800);toast('✔ Kamera '+name+' disimpan. Pipeline dimulai; menunggu data video pertama…')}
 else{var c=CAMS[kidx],ch=c.ip!=ip||c.dev!=dev||c.usr!=F('usr').value||pw||c.fps!=f||c.q!=+F('q').value;c.name=name;c.ip=ip;c.dev=dev;c.usr=F('usr').value;c.q=+F('q').value;c.fps=f;
  LOGK.unshift(['14:37',ME,'Ubah kamera '+name]);
  toast('✔ Perubahan '+name+' disimpan.'+(ch?' Pipeline kamera ini dimulai ulang; kamera lain tidak terganggu.':''))}
 km.classList.add('hide');all()};
$('#k-add').onclick=function(){kOpen()};
$('[data-del]',kd).onclick=function(){var nm=CAMS[kidx].name;CAMS.splice(kidx,1);LOGK.unshift(['14:37',ME,'Hapus kamera '+nm]);kd.classList.add('hide');toast('✔ Kamera '+nm+' dihapus. Pipeline dihentikan; rekaman lama tetap di NAS sampai lewat retensi.');all()};
$('#tbl').onclick=function(e){var b=e.target.closest('[data-a]');if(!b)return;var tr=b.closest('tr'),i=+tr.dataset.i,a=b.dataset.a,c=CAMS[i];
 if(a=='ubah')kOpen(i);else if(a=='hapus'){kidx=i;$('#kd-t').textContent='Hapus kamera “'+c.name+'”?';kd.classList.remove('hide')}
 else{var rs=tr.nextElementSibling;rs.classList.remove('hide');var td=rs.firstElementChild;td.innerHTML='⏳ Menguji…';run(c,td)}};

// ---------- router
var VIEWS=['dashboard','live','rekaman','ekspor','kamera'];
function all(){rDash();rLive();rRek();xCams();xUpd();xTbl();rKam()}
function route(){var v=(location.hash.replace('#/','')||'dashboard');if(VIEWS.indexOf(v)<0)v='dashboard';
 VIEWS.forEach(function(x){$('#v-'+x).classList.toggle('hide',x!=v)});$$('.topnav a[data-v]').forEach(function(a){a.classList.toggle('on',a.dataset.v==v)});
 document.title='Gardu — '+v.charAt(0).toUpperCase()+v.slice(1);window.scrollTo(0,0)}
window.addEventListener('hashchange',route);
all();route();
})();