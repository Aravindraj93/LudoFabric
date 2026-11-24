import * as THREE from 'three';
import { OrbitControls } from '/static/vendor/OrbitControls.js';

const ROLES = ['Director', 'Tech Lead', 'Assets', 'Specialists', 'Unity', 'Art Director', 'Ship'];
const COLORS = [0xbb7aff, 0x35dcff, 0xffbe55, 0x55e6b3, 0x7989ff, 0xff70b8, 0xff9955];

export function createOffice(host, info, fallback) {
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x090d21);
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.3;
  host.appendChild(renderer.domElement);
  renderer.domElement.setAttribute('aria-label', '3D robot office; drag to orbit, scroll to zoom');
  const camera = new THREE.PerspectiveCamera(42, 1, .1, 120);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true; controls.dampingFactor = .07;
  controls.minDistance = 12; controls.maxDistance = 42;
  controls.maxPolarAngle = Math.PI * .48; controls.enablePan = false;
  function reset() { camera.position.set(17, 17, 22); controls.target.set(0, 0, 0); controls.update(); }
  reset();
  scene.add(new THREE.HemisphereLight(0xd6e4ff, 0x363055, 2.4));
  const key = new THREE.DirectionalLight(0xffe4cb, 3.4);
  key.position.set(-6, 15, 8); key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048);
  Object.assign(key.shadow.camera, {left: -17, right: 17, top: 15, bottom: -15, near: .5, far: 50});
  key.shadow.normalBias = .035; scene.add(key);
  const rim = new THREE.DirectionalLight(0x618fff, 2); rim.position.set(4, 8, -9); scene.add(rim);
  const materials = new Map();
  function mat(color, glow = false) {
    const id = `${color}/${glow}`;
    if (!materials.has(id)) materials.set(id, new THREE.MeshStandardMaterial({color, roughness: .55,
      metalness: .15, emissive: glow ? color : 0, emissiveIntensity: glow ? 1 : 0}));
    return materials.get(id);
  }
  const cube = new THREE.BoxGeometry(1, 1, 1);
  const sphere = new THREE.SphereGeometry(1, 16, 12);
  const cylinder = new THREE.CylinderGeometry(1, 1, 1, 16);
  function shape(parent, geo, color, pos, scale, glow = false) {
    const m = new THREE.Mesh(geo, mat(color, glow)); m.position.set(...pos); m.scale.set(...scale);
    m.castShadow = true; m.receiveShadow = true; parent.add(m); return m;
  }
  const box = (p,c,x,y,z,a,b,d,g=false) => shape(p,cube,c,[x,y,z],[a,b,d],g);
  const ball = (p,c,x,y,z,r,g=false) => shape(p,sphere,c,[x,y,z],[r,r,r],g);
  const cyl = (p,c,x,y,z,r,h) => shape(p,cylinder,c,[x,y,z],[r,h,r]);
  function label(text, color = '#dce5ff', width = 3) {
    const canvas = document.createElement('canvas'); canvas.width = 512; canvas.height = 128;
    const ctx = canvas.getContext('2d'), texture = new THREE.CanvasTexture(canvas);
    const s = new THREE.Sprite(new THREE.SpriteMaterial({map: texture, depthWrite: false}));
    s.scale.set(width, width / 4, 1);
    s.userData.write = value => {
      ctx.clearRect(0, 0, 512, 128); ctx.fillStyle = 'rgba(10,16,35,.88)'; ctx.fillRect(8, 12, 496, 104);
      ctx.strokeStyle = color; ctx.lineWidth = 3; ctx.strokeRect(8, 12, 496, 104);
      ctx.font = 'bold 29px Segoe UI,sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillStyle = color; ctx.fillText(value, 256, 65, 470); texture.needsUpdate = true;
    };
    s.userData.write(text); return s;
  }
  box(scene,0x18243d,0,-.25,0,26,.5,16);
  box(scene,0x334363,0,-.55,0,26.5,.2,16.5);
  for(let x=-12;x<=12;x+=2) box(scene,0x25334d,x,.01,0,.018,.015,16);
  for(let z=-7;z<=7;z+=2) box(scene,0x25334d,0,.01,z,26,.015,.018);
  box(scene,0x253858,0,2.6,-7.7,26,5.2,.28);
  box(scene,0x415475,-12.8,1.2,-3.5,.25,2.4,8);
  box(scene,0x415475,12.8,1.2,-3.5,.25,2.4,8);
  for(let x=-10;x<=10;x+=4) {
    box(scene,0x182440,x,3,-7.48,3.65,3,.08);
    box(scene,0x428fba,x,3,-7.42,3.2,2.65,.04,true);
    box(scene,0x263a58,x,3,-7.35,.1,2.8,.08);
    for(let j=0;j<4;j++) box(scene,0x162d57,x-1.2+j*.8,2.5+j%2*.5,-7.3,.4,1.2+j%2,.05);
  }
  const sign = label('LUDOFABRIC / NIGHT SHIFT','#8df3ff',7); sign.position.set(0,4.4,-7.1); scene.add(sign);
  box(scene,0x5d65ff,0,.02,-.3,22,.025,.06,true);
  const desks = ROLES.map((_,i)=>-9+i*3), screens=[];
  ROLES.forEach((role,i)=>{
    const x=desks[i], c=COLORS[i];
    box(scene,0x926d56,x,1.05,-4.3,2.6,.16,1.4);
    for(const dx of [-1,1]) box(scene,0x314160,x+dx,.5,-4.3,.12,1,1);
    box(scene,0x222d46,x,1.45,-4.6,.16,.65,.12);
    box(scene,0x111727,x,1.92,-4.6,1.5,.95,.13);
    const screen=box(scene,c,x,1.92,-4.52,1.34,.79,.015,true);
    screen.material=screen.material.clone(); screens.push(screen);
    for(let j=0;j<4;j++) box(scene,0xe4f5ff,x-.48+j%2*.16,2.14-j*.13,-4.5,.6+j%2*.15,.035,.018,true);
    box(scene,0x334462,x,1.17,-3.85,.9,.05,.32); cyl(scene,c,x+.95,1.25,-4,.12,.28);
    box(scene,0x253752,x,.55,-3,.75,.12,.7); box(scene,0x253752,x,.9,-2.62,.75,.65,.12);
    cyl(scene,0x172335,x,.27,-3,.12,.45);
    const l=label(role,new THREE.Color(c).getStyle(),2.2); l.position.set(x,.16,-5.9); scene.add(l);
  });
  box(scene,0x644b58,10,.65,4.7,3.7,1.3,1.1); box(scene,0xb69a7a,10,1.35,4.7,3.9,.15,1.3);
  box(scene,0x18223a,10,1.8,4.6,.8,.8,.6); box(scene,0x4cf2c4,10,1.85,4.94,.45,.22,.04,true);
  cyl(scene,0xffc165,8.7,1.65,4.6,.22,.5);
  const coffee=label('COFFEE = MORE TOKENS','#ffd48b',3.5); coffee.position.set(10,2.6,4.7); scene.add(coffee);
  box(scene,0x745590,-9,.43,4.8,4,.6,1.5); box(scene,0x513f72,-9,1,5.4,4,1.3,.25);
  for(let i=0;i<3;i++) box(scene,0x9d77c4,-10.2+i*1.2,.8,4.85,1.05,.18,1.05);
  const nap=label('NAP LAB / DO NOT DEBUG','#d7b4ff',3.5); nap.position.set(-9,2.3,5.4); scene.add(nap);
  function plant(x,z) {
    cyl(scene,0xb69b85,x,.3,z,.35,.6); cyl(scene,0x456753,x,1,z,.06,1.4);
    for(let j=0;j<5;j++) { const l=ball(scene,0x3ab985,x+Math.sin(j*2)*.3,1.1+j*.13,z+Math.cos(j*2)*.25,.28); l.scale.y*=1.8; }
  }
  plant(-11.8,-6.2); plant(11.8,-6.2); plant(12,6.4); plant(-12,6.4);
  box(scene,0x22395a,0,.02,4.5,8,.04,4); cyl(scene,0x936f58,0,1,4.5,1.5,.16); cyl(scene,0x36466b,0,.5,4.5,.32,.95);
  const meeting=label('STAND-UP (SITTING)','#9caeff',3); meeting.position.set(0,1.6,4.5); scene.add(meeting);
  const robots=[], pickable=[];
  ROLES.forEach((role,i)=>{
    const root=new THREE.Group(), rig=new THREE.Group(); scene.add(root); root.add(rig); const c=COLORS[i];
    box(rig,c,0,1.14,0,.65,.65,.4); ball(rig,0x8cdfff,0,1.24,.23,.1,true);
    const head=new THREE.Group(); head.position.y=1.76; rig.add(head);
    box(head,0xe1e9f1,0,0,0,.85,.65,.58); box(head,0x111e38,0,0,.304,.72,.4,.025);
    const eyes=[-.2,.2].map(dx=>box(head,c,dx,.03,.325,.15,.11,.025,true));
    box(head,c,0,-.12,.326,.2,.025,.025,true); cyl(head,0x7989a3,0,.47,0,.035,.3); ball(head,c,0,.64,0,.07,true);
    const arms=[], legs=[];
    for(const side of [-1,1]) {
      const arm=new THREE.Group(); arm.position.set(side*.45,1.36,0); rig.add(arm);
      ball(arm,0x7c8ba6,0,0,0,.13); box(arm,c,0,-.22,0,.19,.43,.2); ball(arm,0xdde4ef,0,-.46,0,.13); arms.push(arm);
      const leg=new THREE.Group(); leg.position.set(side*.2,.82,0); rig.add(leg);
      box(leg,0x8293aa,0,-.24,0,.18,.45,.2); box(leg,c,0,-.55,.07,.28,.17,.43); legs.push(leg);
    }
    const badge=label('',new THREE.Color(c).getStyle(),2.3); badge.position.y=2.8; root.add(badge);
    root.position.set(desks[i],0,-.8);
    root.traverse(o=>{if(o.isMesh){o.userData.agent=i;pickable.push(o);}});
    robots.push({i,root,rig,head,arms,legs,eyes,badge,mode:'',path:[],timer:4+i*2,destination:'desk',tick:i*1.8,key:''});
  });
  let snapshot={done:0,active:-1,running:false}, selected=0, visible=false, failed=false, raf=0,last=0,time=0;
  const motion=matchMedia('(prefers-reduced-motion: reduce)');
  function route(r,x,z,destination) {
    r.destination=destination;
    r.path=[new THREE.Vector3(r.root.position.x,0,0),new THREE.Vector3(x,0,0),new THREE.Vector3(x,0,z)];
    if(motion.matches){r.root.position.set(x,0,z);r.path=[];}
  }
  function refreshInfo() {
    const r=robots[selected], action=r.path.length?'Walking through the office':r.destination==='nap'?'Z z z… saving brain cells':
      r.destination==='coffee'?'Recharging with coffee':r.mode==='working'?'Working at the monitor':'Waiting for the next assignment';
    info.textContent=`${ROLES[selected]} — ${r.mode}. ${action}. Stage visualization, not actual model activity.`;
  }
  function pose(r,dt) {
    r.tick+=dt; r.timer-=dt;
    if(r.path.length) {
      const goal=r.path[0],dx=goal.x-r.root.position.x,dz=goal.z-r.root.position.z,dist=Math.hypot(dx,dz);
      if(dist<.04)r.path.shift();
      else {const step=Math.min(dist,dt*(r.mode==='working'?2.5:1.15));r.root.position.x+=dx/dist*step;r.root.position.z+=dz/dist*step;
        const a=Math.atan2(dx,dz),d=Math.atan2(Math.sin(a-r.root.rotation.y),Math.cos(a-r.root.rotation.y));r.root.rotation.y+=d*Math.min(1,dt*9);}
    } else if(r.mode!=='working'&&r.mode!=='done'&&r.timer<=0&&!motion.matches) {
      const dest=r.destination==='desk'?(r.i%2?'nap':'coffee'):'desk';
      route(r,dest==='desk'?desks[r.i]:dest==='coffee'?9+r.i%3*.65:-10.5+r.i%3*.8,dest==='desk'?-.85:3.3,dest);r.timer=15+r.i*1.5;
    }
    const walk=r.path.length>0,sleep=!walk&&r.destination==='nap',work=!walk&&r.mode==='working';
    r.rig.position.y=walk?Math.sin(r.tick*12)*.045:Math.sin(r.tick*2)*.018;r.rig.rotation.z=sleep?-.22:0;
    r.head.rotation.x=sleep?.45:work?.13:0;
    r.arms.forEach((a,k)=>a.rotation.x=walk?Math.sin(r.tick*9+k*Math.PI)*.55:work?-.9+Math.sin(r.tick*19+k*Math.PI)*.14:0);
    r.legs.forEach((l,k)=>l.rotation.x=walk?Math.sin(r.tick*9+k*Math.PI+Math.PI)*.5:0);
    r.eyes.forEach(e=>e.scale.y=sleep?.022:.11);
    if(!walk&&r.destination==='desk')r.root.rotation.y=work?Math.PI:.2;
    r.badge.position.y=2.85+Math.sin(r.tick*2)*.07;
    const k=`${sleep}/${walk}/${r.mode}`;
    if(r.key!==k){r.key=k;r.badge.userData.write(`${ROLES[r.i]} / ${walk?'on my way!':sleep?'Z z z...':work?'typing...':r.mode==='done'?'done!':'coffee break?'}`);}
  }
  function renderOnce(){if(visible&&!failed)renderer.render(scene,camera);}
  function update(next) {
    snapshot={...snapshot,...next};
    robots.forEach(r=>{
      const mode=snapshot.running&&snapshot.active===r.i?'working':r.i<snapshot.done?'done':snapshot.running?'waiting':'idle';
      if(mode===r.mode)return;r.mode=mode;r.timer=4+r.i*1.7;
      if(mode==='working')route(r,desks[r.i],-2.25,'desk');
      else if(mode==='done')route(r,-10.5+r.i%3*.9,3.6,'nap');
      else route(r,desks[r.i],-.85,'desk');
      pose(r,0);
    });refreshInfo();renderOnce();
  }
  function frame(now) {
    raf=0;if(!visible||document.hidden||failed){last=0;return;}
    const dt=last?Math.min((now-last)/1000,.05):0;last=now;time+=dt;
    if(!motion.matches)robots.forEach(r=>pose(r,dt));
    controls.enableDamping=!motion.matches;controls.update();
    screens.forEach((s,i)=>s.material.emissiveIntensity=snapshot.running&&snapshot.active===i?1.6+Math.sin(time*3)*.3:.3);
    renderer.render(scene,camera);refreshInfo();if(!motion.matches)raf=requestAnimationFrame(frame);
  }
  function wake(){if(!raf&&visible&&!document.hidden&&!failed){last=0;raf=requestAnimationFrame(frame);}}
  function resize(){const w=host.clientWidth,h=host.clientHeight;if(!w||!h)return;renderer.setSize(w,h,false);camera.aspect=w/h;camera.updateProjectionMatrix();renderOnce();}
  new ResizeObserver(resize).observe(host);
  controls.addEventListener('change',()=>{if(motion.matches)renderOnce();});
  document.addEventListener('visibilitychange',wake);
  motion.addEventListener('change',()=>{if(motion.matches)robots.forEach(r=>{if(r.path.length){r.root.position.copy(r.path.at(-1));r.path=[];}pose(r,0);});wake();});
  const ray=new THREE.Raycaster();let down=null;
  renderer.domElement.addEventListener('pointerdown',e=>{down={x:e.clientX,y:e.clientY};});
  renderer.domElement.addEventListener('pointerup',e=>{
    if(!down||Math.hypot(e.clientX-down.x,e.clientY-down.y)>5)return;
    const rect=renderer.domElement.getBoundingClientRect();
    ray.setFromCamera(new THREE.Vector2((e.clientX-rect.left)/rect.width*2-1,-(e.clientY-rect.top)/rect.height*2+1),camera);
    const hit=ray.intersectObjects(pickable)[0];if(hit){selected=hit.object.userData.agent;refreshInfo();}
  });
  renderer.domElement.addEventListener('webglcontextlost',e=>{e.preventDefault();failed=true;fallback('Graphics context lost; pipeline is still available.');});
  update(window.officeSnapshot||snapshot);
  return {update,select(i){selected=i;refreshInfo();},reset(){reset();renderOnce();},
    show(on){visible=on;if(on){resize();wake();}else if(raf){cancelAnimationFrame(raf);raf=0;last=0;}}};
}
