const $ = (s) => document.querySelector(s);
let audioCtx, musicGain, musicTimer, classicalPlaying = false;

function audioStart(){
  if(!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  if(audioCtx.state === 'suspended') audioCtx.resume();
  if(!musicGain){
    musicGain = audioCtx.createGain();
    musicGain.gain.value = 0.045;
    musicGain.connect(audioCtx.destination);
  }
}

function playIndianClassicalWelcome(){
  if(localStorage.getItem('syraSound')==='off') return;
  audioStart();
  if(classicalPlaying) return;
  classicalPlaying = true;
  if(musicTimer) clearTimeout(musicTimer);

  // Original Indian-classical-inspired welcome phrase: tanpura-like drone + bansuri/sitar-style melody.
  const drone = [130.81,196.00,261.63]; // C3, G3, C4 drone
  const phrase = [261.63,293.66,329.63,392.00,440.00,392.00,329.63,293.66,261.63,329.63,392.00,523.25];
  const now = audioCtx.currentTime;

  drone.forEach((freq, i)=>{
    const o=audioCtx.createOscillator(), g=audioCtx.createGain();
    o.type='sine'; o.frequency.value=freq;
    g.gain.setValueAtTime(0, now);
    g.gain.linearRampToValueAtTime(i===0?.022:.014, now+.35);
    g.gain.exponentialRampToValueAtTime(.001, now+3.6);
    o.connect(g); g.connect(musicGain); o.start(now); o.stop(now+3.8);
  });

  phrase.forEach((freq,i)=>{
    const t=now+0.35+i*0.23;
    const o=audioCtx.createOscillator(), g=audioCtx.createGain(), lfo=audioCtx.createOscillator(), lg=audioCtx.createGain();
    o.type='triangle'; o.frequency.setValueAtTime(freq,t);
    // Gentle pitch glide for a bansuri-like expression.
    o.frequency.linearRampToValueAtTime(freq*(i%3===0?1.008:0.997),t+.20);
    g.gain.setValueAtTime(0,t); g.gain.linearRampToValueAtTime(.075,t+.035); g.gain.exponentialRampToValueAtTime(.001,t+.29);
    lfo.type='sine'; lfo.frequency.value=5.2; lg.gain.value=1.7; lfo.connect(lg); lg.connect(o.frequency);
    o.connect(g); g.connect(musicGain); o.start(t); lfo.start(t); o.stop(t+.32); lfo.stop(t+.32);
  });

  // Soft bell-like ending.
  const bellTime=now+3.05;
  [523.25,659.25].forEach((freq,i)=>{
    const o=audioCtx.createOscillator(), g=audioCtx.createGain();
    o.type='sine'; o.frequency.value=freq;
    g.gain.setValueAtTime(.035,bellTime+i*.04); g.gain.exponentialRampToValueAtTime(.001,bellTime+1.0);
    o.connect(g); g.connect(musicGain); o.start(bellTime+i*.04); o.stop(bellTime+1.05);
  });

  musicTimer=setTimeout(()=>{classicalPlaying=false;},4300);
}

function sfx(kind){
  if(localStorage.getItem('syraSound')==='off') return;
  audioStart();
  const now=audioCtx.currentTime;
  const o=audioCtx.createOscillator(), g=audioCtx.createGain(); o.connect(g); g.connect(audioCtx.destination);
  if(kind==='error'){o.type='sawtooth';o.frequency.setValueAtTime(180,now);o.frequency.exponentialRampToValueAtTime(75,now+.35);g.gain.setValueAtTime(.12,now);g.gain.exponentialRampToValueAtTime(.001,now+.4);o.start();o.stop(now+.42)}
  else {o.type='sine';o.frequency.setValueAtTime(520,now);o.frequency.exponentialRampToValueAtTime(920,now+.16);g.gain.setValueAtTime(.08,now);g.gain.exponentialRampToValueAtTime(.001,now+.45);o.start();o.stop(now+.46)}
}
function showMsg(text, good=false){const el=$('#msg');el.textContent=text;el.className=good?'good':'bad'}
function tab(name){document.querySelectorAll('.tabs button').forEach(b=>b.classList.toggle('active',b.dataset.tab===name));document.querySelectorAll('.form').forEach(f=>f.classList.toggle('active',f.id===name));showMsg('')}

document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>tab(b.dataset.tab));

document.addEventListener('click',()=>{try{audioStart()}catch(e){}},{once:true});

$('#login').addEventListener('submit',async e=>{e.preventDefault();const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({identifier:$('#li').value,password:$('#lp').value})});const j=await r.json();if(j.ok){window.playSyraWelcome&&window.playSyraWelcome();showMsg('Login verified. Welcome to SYRA…',true);const overlay=$('#welcomeOverlay');const status=$('#welcomeStatus');status.textContent='WELCOME BACK, '+(j.name||'PILOT').toUpperCase()+' • SYRA CORE ONLINE';overlay.classList.remove('show');void overlay.offsetWidth;overlay.classList.add('show','login-success');playIndianClassicalWelcome();setTimeout(()=>location.href='/dashboard',4300)}else showMsg(j.error)});
$('#register').addEventListener('submit',async e=>{e.preventDefault();const r=await fetch('/api/register/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:$('#rn').value,email:$('#re').value,mobile:$('#rm').value,password:$('#rp').value})});const j=await r.json();if(j.ok){showMsg(j.message,true);$('#welcomeOverlay').classList.add('show');setTimeout(()=>location.href='/dashboard',900)}else showMsg(j.error)});
$('#reset').addEventListener('submit',async e=>{e.preventDefault();const r=await fetch('/api/reset/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({identifier:$('#ri').value})});const j=await r.json();if(j.ok){showMsg(j.message,true)}else showMsg(j.error)});

window.addEventListener('load',()=>{setTimeout(()=>$('#welcomeOverlay').classList.add('show'),250);setTimeout(()=>$('#welcomeOverlay').classList.remove('show'),1900)});
