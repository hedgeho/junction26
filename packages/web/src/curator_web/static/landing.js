
const NS="http://www.w3.org/2000/svg";
const el=(t,a,p)=>{const e=document.createElementNS(NS,t);for(const k in a)e.setAttribute(k,a[k]);p&&p.appendChild(e);return e};
const reduce=window.matchMedia&&matchMedia("(prefers-reduced-motion: reduce)").matches;


document.querySelectorAll(".keyline").forEach(svg=>{
  svg.setAttribute("viewBox","0 0 2400 22");let d="";
  for(let x=2;x<2400;x+=24)d+=`M${x} 20H${x+18}V3H${x+6}V15H${x+14}V9H${x+10}`;
  el("path",{d,fill:"none",stroke:"#4E5B2C","stroke-width":2,"stroke-linecap":"square"},svg);
});


function branch(svg,p0,c,p2,n,size,fruitEvery){
  el("path",{d:`M${p0[0]} ${p0[1]} Q${c[0]} ${c[1]} ${p2[0]} ${p2[1]}`,class:"stem"},svg);
  for(let i=1;i<=n;i++){const t=i/(n+1),u=1-t;
    const x=u*u*p0[0]+2*u*t*c[0]+t*t*p2[0],y=u*u*p0[1]+2*u*t*c[1]+t*t*p2[1];
    const dx=2*u*(c[0]-p0[0])+2*t*(p2[0]-c[0]),dy=2*u*(c[1]-p0[1])+2*t*(p2[1]-c[1]);
    const ang=Math.atan2(dy,dx)*180/Math.PI,side=i%2?1:-1,s=size*(0.75+0.35*Math.sin(t*Math.PI));
    const g=el("g",{transform:`translate(${x} ${y}) rotate(${ang+side*38})`},svg);
    el("ellipse",{cx:s*.95,cy:0,rx:s,ry:s*.34,class:i%3===0?"leaf alt":"leaf"},g);
    if(fruitEvery&&i%fruitEvery===0)el("ellipse",{cx:x+side*s*.5,cy:y+s*.4,rx:s*.26,ry:s*.2,class:"fruit"},svg);
  }}
function sprig(svg,x,y0,y1,amp,seg,leaf){let y=y0,dir=1;while(y<y1){const ny=Math.min(y1,y+seg);branch(svg,[x,y],[x+amp*dir,(y+ny)/2],[x,ny],5,leaf,dir>0?5:0);y=ny;dir*=-1}}
sprig(document.getElementById("sideL"),44,-50,1050,20,110,8);
sprig(document.getElementById("sideR"),44,-60,1080,20,120,8);


(function(){const b=document.getElementById("bust");
  el("rect",{x:46,y:368,width:208,height:14,class:"marble",opacity:.8},b);
  el("path",{class:"marble",d:"M180 96 C190 104 197 116 197 130 C198 136 200 140 202 144 L214 168 C215 173 209 176 203 177 C206 180 207 183 206 186 C203 188 201 190 200 192 C204 194 206 197 204 200 C201 203 199 205 198 207 C203 213 204 219 201 225 C198 231 192 235 185 237 C183 252 184 270 188 290 C193 310 214 328 250 342 L258 368 L42 368 L50 344 C84 332 106 314 114 294 C120 272 120 250 117 230 C105 213 99 196 101 180 C97 176 94 170 93 164 C76 162 60 150 58 132 C56 112 70 98 88 96 C100 70 126 58 150 60 C166 62 176 78 180 96 Z"},b);
  el("circle",{cx:80,cy:126,r:27,class:"marble"},b);
  el("path",{class:"chisel",d:"M80 104 C98 106 104 132 88 144 C72 152 58 136 66 120 C72 110 88 112 90 124 C91 132 82 136 78 130"},b);
  ["M179 98 C162 82 132 76 104 94","M181 110 C162 96 132 94 106 110","M176 122 C158 112 134 112 110 128","M160 134 C146 130 128 134 112 146"].forEach(d=>el("path",{class:"chisel soft",d},b));
  ["M178 134 C184 130 192 130 197 133","M180 143 C184 146 190 146 194 143","M205 171 C207 173 209 174 211 173","M200 192 L206 191","M146 142 C137 141 134 158 142 164","M150 248 C155 268 157 290 162 306","M62 352 C92 344 112 330 122 312","M238 350 C214 340 198 326 192 306","M110 360 C124 350 132 338 136 326"].forEach(d=>el("path",{class:"chisel",d},b));
  branch(b,[176,90],[150,72],[108,96],7,7,3);
})();


(function(){
  const svg=document.getElementById("logoStory");
  const P=[[8,92],[92,92],[92,8],[8,8],[8,72],[72,72],[72,28],[28,28],[28,42]];
  let s=9;const r=()=>{s=(s*9301+49297)%233280;return s/233280},tiles=[];
  for(let i=0;i<P.length-1;i++){const[a,b]=[P[i],P[i+1]],n=Math.max(1,Math.round(Math.hypot(b[0]-a[0],b[1]-a[1])/11));
    for(let j=(i===0?0:1);j<=n;j++){const t=j/n;tiles.push({x:a[0]+(b[0]-a[0])*t,y:a[1]+(b[1]-a[1])*t,r:(r()-.5)*10})}}
  tiles.forEach((t,i)=>{t.sx=-12+r()*124;t.sy=-12+r()*124;t.sr=(r()-.5)*90;t.i=i;t.el=el("rect",{width:9.2,height:9.2,fill:"currentColor"},svg)});
  const reds=Array.from({length:14},()=>({sx:-12+r()*124,sy:-12+r()*124,sr:(r()-.5)*90,el:el("rect",{width:9.2,height:9.2,fill:"#D9583B"},svg)}));
  const line=el("path",{d:"M8 92H92V8H8V72H72V28H28V52",fill:"none",stroke:"currentColor","stroke-width":12,"stroke-linecap":"square"},svg);
  const L=line.getTotalLength();line.style.strokeDasharray=L;
  const dot=el("circle",{cx:52,cy:52,r:8,fill:"#FFB000"},svg);
  const C=v=>Math.max(0,Math.min(1,v)),S=(p,a,b)=>C((p-a)/(b-a)),E=t=>1-Math.pow(1-t,3),n=tiles.length;
  const render=p=>{
    reds.forEach((t,i)=>{const f=E(S(p,.18+i*.01,.45+i*.01));t.el.setAttribute("x",t.sx-4.6);t.el.setAttribute("y",t.sy+f*70-4.6);
      t.el.setAttribute("opacity",(1-f).toFixed(2));t.el.setAttribute("transform",`rotate(${t.sr+f*60} ${t.sx} ${t.sy+f*70})`)});
    const fade=1-S(p,.74,.92);
    tiles.forEach(t=>{const a=.3+t.i/n*.34,f=E(S(p,a,a+.14)),x=t.sx+(t.x-t.sx)*f,y=t.sy+(t.y-t.sy)*f,rot=t.sr+(t.r-t.sr)*f;
      t.el.setAttribute("x",x-4.6);t.el.setAttribute("y",y-4.6);t.el.setAttribute("transform",`rotate(${rot.toFixed(1)} ${x} ${y})`);t.el.setAttribute("opacity",fade.toFixed(2))});
    line.style.strokeDashoffset=L*(1-E(S(p,.66,.9)));
    dot.setAttribute("r",(8*E(S(p,.88,1))).toFixed(2))};
  render(1); // complete at rest
  if(!reduce){const t0=performance.now();const tick=now=>{const p=Math.min(1,(now-t0)/3000);render(p);if(p<1)requestAnimationFrame(tick)};requestAnimationFrame(tick)}
})();


(function(){
  const blocks=[...document.querySelectorAll("[data-type]")];
  blocks.forEach(b=>{
    const words=b.textContent.trim().split(/\s+/);
    b.textContent="";
    words.forEach((w,i)=>{const s=document.createElement("span");s.className="w";s.textContent=w;b.appendChild(s);if(i<words.length-1)b.appendChild(document.createTextNode(" "))});
    b.setAttribute("aria-label",words.join(" "));
  });
  const type=b=>{
    if(b.dataset.started)return;b.dataset.started="1";
    const ws=[...b.querySelectorAll(".w")],speed=+b.dataset.speed||110,caret=document.createElement("span");
    caret.className="caret";caret.setAttribute("aria-hidden","true");
    if(reduce){ws.forEach(w=>w.classList.add("on"));b.classList.add("done");return}
    let i=0;const step=()=>{if(i<ws.length){ws[i].classList.add("on");ws[i].after(caret);i++;setTimeout(step,speed)}else setTimeout(()=>b.classList.add("done"),700)};
    setTimeout(step,+b.dataset.delay||150);
  };
  if(!("IntersectionObserver" in window)||reduce){blocks.forEach(type);return}
  const io=new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting){type(e.target);io.unobserve(e.target)}}),{threshold:.55});
  blocks.forEach(b=>io.observe(b));
})();
