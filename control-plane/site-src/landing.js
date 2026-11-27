import * as THREE from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {RoomEnvironment} from 'three/addons/environments/RoomEnvironment.js';
{
const hero = document.querySelector('.hero'), box = document.querySelector('.stagebox'), canvas = document.getElementById('stage');
const loader = document.getElementById('loader'), hotsEl = document.getElementById('hots'), dimsEl = document.getElementById('dims');
const cap = document.getElementById('caption'), capK = document.getElementById('capK'), capT = document.getElementById('capT'), capP = document.getElementById('capP');
const tourBtn = document.getElementById('tour'), resetBtn = document.getElementById('reset'), exploreBtn = document.getElementById('explore');
const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
let renderer;
try { renderer = new THREE.WebGLRenderer({canvas, antialias: true, alpha: true}); }
catch (e) { hero.classList.add('nogl'); loader.textContent = ''; }
if (renderer) {
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.05;
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.shadowMap.enabled = true; renderer.shadowMap.type = THREE.PCFSoftShadowMap;

const scene = new THREE.Scene();
const pm = new THREE.PMREMGenerator(renderer);
scene.environment = pm.fromScene(new RoomEnvironment(), 0.04).texture;
scene.environmentIntensity = 0.55;
const camera = new THREE.PerspectiveCamera(30, 1, 0.05, 100);
const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true; controls.dampingFactor = 0.06; controls.enablePan = false;
controls.minDistance = 0.55; controls.maxDistance = 6; controls.maxPolarAngle = Math.PI * 0.49;
controls.autoRotate = !reduce; controls.autoRotateSpeed = 0.6;

// lights: one overhead studio key with soft shadow, gold rim, faint fill
const key = new THREE.DirectionalLight(0xfff4e6, 2.6); key.position.set(0.6, 5, 1.4); key.castShadow = true; key.shadow.radius = 6;
key.shadow.mapSize.set(2048, 2048); key.shadow.camera.left = -2; key.shadow.camera.right = 2; key.shadow.camera.top = 2; key.shadow.camera.bottom = -2; key.shadow.bias = -0.0004;
scene.add(key);
const rim = new THREE.PointLight(0xf2b33d, 14, 8, 2); rim.position.set(-1.8, 1.2, -1.6); scene.add(rim);
const fill = new THREE.DirectionalLight(0xcfd8ff, 0.25); fill.position.set(-3, 1.5, 2); scene.add(fill);

// floor: invisible shadow catcher so the device grounds itself on the backdrop's table
const floor = new THREE.Mesh(new THREE.CircleGeometry(4, 64), new THREE.ShadowMaterial({opacity: 0.6}));
floor.rotation.x = -Math.PI / 2; floor.receiveShadow = true; scene.add(floor);

// device
const pivot = new THREE.Group(); pivot.rotation.y = Math.PI; scene.add(pivot);
const inner = new THREE.Group(); pivot.add(inner);
const M = {
  base: new THREE.MeshPhysicalMaterial({color: 0x1d1e22, roughness: 0.55, metalness: 0.1, clearcoat: 0.25}),
  shell: new THREE.MeshPhysicalMaterial({color: 0xb9bbbe, roughness: 0.42, metalness: 0.05, clearcoat: 0.3, clearcoatRoughness: 0.4}),
  bezel: new THREE.MeshPhysicalMaterial({color: 0x2b2d30, roughness: 0.48, metalness: 0.1, clearcoat: 0.3}),
  glass: new THREE.MeshPhysicalMaterial({color: 0x030304, roughness: 0.04, metalness: 0, clearcoat: 1})
};
// hotspots in model space (glTF millimetres): point the camera looks at, camera direction hint, distance,
// and where the marker sits: on an edge next to the feature, so it never covers the screen, vents or ports
const HOT = [
  {k: 'YOUR BRAND', t: 'Your logo on the screen', p: 'The 3.5″ touch screen shows your company\'s logo and tagline, set from the management console. Tap it to start a meeting.', at: [42.5, 31.5, -28], mark: [0, 32.5, -57], dir: [0, 1.25, 0.55], d: 0.85},
  {k: 'RUGGED', t: 'Rugged, solid build', p: 'An armoured three-part shell with a faceted, studded surface that shrugs off knocks and scuffs, built to survive travel bags and busy boardroom tables.', at: [-6.5, 18, -62.5], dir: null, d: 0.62},
  {k: 'COOLING', t: 'Silent passive cooling', p: 'Air enters below and leaves through angled side vents, keeping the device at a steady temperature. No fan, no noise.', at: [52.5, 21.5, -62.5], mark: [52.5, 5, -63.2], dir: null, d: 0.62},
  {k: 'PLUG & PLAY', t: 'Plug in and meet', p: 'Connect Ethernet for your local network or the internet, add USB-C power, and it\'s ready. No software to install.', at: [94, 15, -45.8], mark: [94.7, 27, -45.8], dir: null, d: 0.62}
];
let center = new THREE.Vector3(), ready = false;
const tex = new THREE.TextureLoader().load('/static/site/vipcon_screen.jpg', t => { t.colorSpace = THREE.SRGBColorSpace; t.flipY = false; t.anisotropy = 8; });
tex.flipY = false;
new GLTFLoader().load('/static/site/vipcon_device.glb?v=3', onModel, undefined, onFail);
function onFail() { hero.classList.add('nogl'); loader.textContent = ''; }
function onModel(g) {
  g.scene.traverse(o => {
    if (!o.isMesh) return;
    const n = o.name.toLowerCase();
    o.castShadow = o.receiveShadow = true;
    // ports, PCB and board parts keep the materials made for them in Blender (Web_*)
    if (o.material && o.material.name.startsWith('Web_')) { o.castShadow = false; o.material.envMapIntensity = 1.4; return; }
    if (n.includes('screen')) { o.material = new THREE.MeshBasicMaterial({map: tex, toneMapped: false, polygonOffset: true, polygonOffsetFactor: -2}); o.castShadow = false; }
    else if (n.includes('glass')) o.material = M.glass;
    else if (n.includes('shell')) o.material = M.shell;
    else if (n.includes('bezel')) o.material = M.bezel;
    else o.material = M.base;
  });
  inner.add(g.scene); inner.scale.setScalar(0.01);
  // centre the device on the stage: measure with the pivot unrotated, then turn it to face the camera
  pivot.rotation.y = 0; pivot.updateMatrixWorld(true);
  const bb = new THREE.Box3().setFromObject(inner), c = bb.getCenter(new THREE.Vector3());
  inner.position.set(-c.x, -bb.min.y, -c.z);
  pivot.rotation.y = Math.PI; pivot.updateMatrixWorld(true);
  center = new THREE.Box3().setFromObject(pivot).getCenter(new THREE.Vector3());
  HOT.forEach((h, i) => {
    h.w = inner.localToWorld(new THREE.Vector3(...h.at));
    h.m = inner.localToWorld(new THREE.Vector3(...(h.mark || h.at)));
    const dir = h.dir ? new THREE.Vector3(...h.dir) : h.w.clone().sub(center).setY(0).normalize().add(new THREE.Vector3(0, 0.55, 0));
    h.cam = h.w.clone().add(dir.normalize().multiplyScalar(h.d));
    const b = document.createElement('button'); b.className = 'hot'; b.type = 'button';
    b.innerHTML = '<i>+</i><span></span>'; b.querySelector('span').textContent = h.t;
    b.setAttribute('aria-label', 'Zoom to ' + h.t);
    b.addEventListener('click', e => { e.stopPropagation(); stopTour(); focus(i); });
    hotsEl.appendChild(b); h.el = b;
  });
  buildDims();
  ready = true; loader.style.opacity = 0; goHome(false);
  playIntro(6800);
}

// opening "portable" intro: white dimension lines draw along the device, then its size and weight,
// with the caption; it fades out by itself (or as soon as the visitor takes over) and the turntable starts
const PORTABLE = {k: 'PORTABLE', t: 'A meeting server you can carry', p: 'The whole meeting server fits in your hand: 10.2 × 6.9 × 3.3 cm and about 145 g. Take it to any office, site or hotel room, plug it in and meet, with full control over every call.'};
const lineMat = new THREE.LineBasicMaterial({color: 0xffffff, transparent: true, opacity: 0, depthTest: false, toneMapped: false});
const extMat = new THREE.LineBasicMaterial({color: 0xffffff, transparent: true, opacity: 0, depthTest: false, toneMapped: false});
const dotMat = new THREE.MeshBasicMaterial({color: 0xffffff, transparent: true, opacity: 0, depthTest: false, toneMapped: false});
const dimsG = new THREE.Group(); dimsG.visible = false;
const DIMS = [];
let intro = null;
function buildDims() {
  // model space, millimetres: case x -7.2…94.7, y 0…32.5, z -63.2…6.2; z = -63.2 and x = -7.2 face the camera
  const X0 = -7.2, X1 = 94.7, Z0 = -63.2, Z1 = 6.2, H = 32.5, O = 10, y = 0.3, c = O * 0.75;
  const V = (a, b, d) => new THREE.Vector3(a, b, d);
  const spec = [
    {label: '10.2 cm', a: V(X1, y, Z0 - O), b: V(X0, y, Z0 - O), ext: [V(X1, y, Z0 - 2), V(X1, y, Z0 - O - 3), V(X0, y, Z0 - 2), V(X0, y, Z0 - O - 3)], t0: 300, d: 900},
    {label: '6.9 cm', a: V(X0 - O, y, Z0), b: V(X0 - O, y, Z1), ext: [V(X0 - 2, y, Z0), V(X0 - O - 3, y, Z0), V(X0 - 2, y, Z1), V(X0 - O - 3, y, Z1)], t0: 1100, d: 800, at: 0.8},
    {label: '3.3 cm', a: V(X1 + c, 0, Z0 - c), b: V(X1 + c, H, Z0 - c), ext: [V(X1 + 1, H, Z0 - 1), V(X1 + c + 3, H, Z0 - c - 3)], t0: 1800, d: 600},
  ];
  spec.forEach(s => {
    const geo = new THREE.BufferGeometry().setFromPoints([s.a, s.a.clone()]);
    const line = new THREE.Line(geo, lineMat); line.renderOrder = 10;
    const ext = new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(s.ext), extMat); ext.renderOrder = 10;
    const dot = r => { const m = new THREE.Mesh(new THREE.SphereGeometry(1.1, 12, 8), dotMat); m.renderOrder = 11; return m; };
    const d0 = dot(), d1 = dot(); d0.position.copy(s.a); d1.position.copy(s.b);
    dimsG.add(line, ext, d0, d1);
    DIMS.push({...s, geo, ext, d0, d1, mid: s.a.clone().lerp(s.b, s.at ?? 0.5)});
  });
  DIMS.push({label: '≈ 145 g', weight: true, mid: V((X0 + X1) / 2, H + 16, (Z0 + Z1) / 2), t0: 2500, d: 400});
  inner.add(dimsG);
  DIMS.forEach(d => {
    const el = document.createElement('span'); el.className = 'dimpill' + (d.weight ? ' weight' : ''); el.textContent = d.label;
    dimsEl.appendChild(el); d.el = el;
  });
}
function playIntro(hold) {
  if (!DIMS.length) return;
  intro = {s: performance.now() - (reduce ? 4000 : 0), hold: reduce ? Math.max(hold, 4000) + 4000 : hold, cut: Infinity};
  controls.autoRotate = false; dimsG.visible = true; hotsEl.classList.add('intro');
  capK.textContent = PORTABLE.k; capT.textContent = PORTABLE.t; capP.textContent = PORTABLE.p; cap.classList.add('show', 'intro');
}
function endIntro(still = false) {
  if (!intro) return;
  if (still) intro.still = true;  // the visitor took over: keep the device where they put it
  const t = performance.now() - intro.s;
  intro.cut = Math.min(intro.cut, t); intro.hold = Math.min(intro.hold, t);
}
function finishIntro() {
  const was = intro; intro = null; cap.classList.remove('intro'); dimsG.visible = false; hotsEl.classList.remove('intro');
  DIMS.forEach(d => { d.el.style.opacity = 0; });
  if (focused < 0) { cap.classList.remove('show'); if (!tourT && !was.still) controls.autoRotate = !reduce; }
}
function introFrame(r) {
  const t = performance.now() - intro.s, te = Math.min(t, intro.cut);
  const seg = (a, d) => reduce ? 1 : Math.min(1, Math.max(0, (te - a) / d));
  const fade = t < intro.hold ? 1 : Math.max(0, 1 - (t - intro.hold) / 500);
  lineMat.opacity = dotMat.opacity = 0.95 * fade; extMat.opacity = 0.45 * fade;
  DIMS.forEach(d => {
    const p = ease(seg(d.t0, d.d));
    if (d.geo) {
      const pos = d.geo.attributes.position; v.copy(d.a).lerp(d.b, p);
      pos.setXYZ(1, v.x, v.y, v.z); pos.needsUpdate = true;
      d.ext.visible = d.d0.visible = p > 0; d.d1.visible = p >= 1;
    }
    v.copy(d.mid); inner.localToWorld(v); v.project(camera);
    const hw = d.el.offsetWidth / 2 + 6;  // keep the label on screen
    const x = Math.min(r.width - hw, Math.max(hw, (v.x * .5 + .5) * r.width)), y = (-v.y * .5 + .5) * r.height;
    d.el.style.transform = `translate(${x}px,${y}px) translate(-50%,-50%) scale(${0.85 + 0.15 * seg(d.t0 + d.d - 200, 350)})`;
    d.el.style.opacity = fade * seg(d.t0 + d.d - 200, 350);
  });
  if (fade === 0) finishIntro();
}

// camera moves
const HOME = {pos: new THREE.Vector3(1.55, 1.05, 2.35), tgt: new THREE.Vector3(0, 0.12, 0)};
let anim = null, focused = -1;
const ease = t => t < .5 ? 4*t*t*t : 1 - Math.pow(-2*t + 2, 3) / 2;
function fly(pos, tgt, ms = 1500) {
  if (reduce) ms = 1;
  anim = {p0: camera.position.clone(), t0: controls.target.clone(), p1: pos.clone(), t1: tgt.clone(), s: performance.now(), ms};
}
function wide() { return box.clientWidth > 900; }
function homePos() {
  const p = HOME.pos.clone(), t = HOME.tgt.clone();
  if (!wide()) p.multiplyScalar(box.clientWidth < 600 ? 1.5 : 1.25);  // leave room for the size labels on phones
  return {p, t};
}
function goHome(animate = true) {
  focused = -1; if (!intro) cap.classList.remove('show'); HOT.forEach(h => h.el && h.el.classList.remove('on'));
  const {p, t} = homePos(); if (animate) fly(p, t); else { camera.position.copy(p); controls.target.copy(t); }
  controls.autoRotate = !reduce && !intro;
}
function focus(i) {
  const h = HOT[i]; if (!h || !h.cam) return;
  endIntro(); cap.classList.remove('intro');
  focused = i; controls.autoRotate = false;
  HOT.forEach((x, j) => x.el.classList.toggle('on', j === i));
  capK.textContent = h.k; capT.textContent = h.t; capP.textContent = h.p; cap.classList.add('show');
  fly(h.cam, h.w);
}

// cinematic tour: glide through every detail, then a slow orbit
let tourT = null, tourI = 0;
function step() {
  const i = tourI % (HOT.length + 1); tourI++;
  if (i === 0) { goHome(); playIntro(4400); } else focus(i - 1);
  tourT = setTimeout(step, reduce ? 4000 : i === 0 ? 5400 : 4600);
}
function startTour() { if (!ready) return; tourBtn.setAttribute('aria-pressed', 'true'); tourBtn.querySelector('span').textContent = 'Stop tour'; tourI = 0; step(); }
function stopTour() { clearTimeout(tourT); tourT = null; tourBtn.setAttribute('aria-pressed', 'false'); tourBtn.querySelector('span').textContent = 'Cinematic tour'; }
tourBtn.addEventListener('click', () => tourT ? (stopTour(), endIntro(), goHome()) : startTour());
resetBtn.addEventListener('click', () => { stopTour(); endIntro(); goHome(); });
exploreBtn.addEventListener('click', () => { if (!wide()) box.scrollIntoView({behavior: reduce ? 'auto' : 'smooth'}); stopTour(); startTour(); });
controls.addEventListener('start', () => { anim = null; stopTour(); endIntro(true); controls.autoRotate = false; });

// click the device: zoom to the display, click again to pull back
const ray = new THREE.Raycaster(), ndc = new THREE.Vector2(); let down = null;
canvas.addEventListener('pointerdown', e => { down = [e.clientX, e.clientY]; });
canvas.addEventListener('pointerup', e => {
  if (!ready || !down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 5) return;
  const r = canvas.getBoundingClientRect();
  ndc.set((e.clientX - r.left) / r.width * 2 - 1, -(e.clientY - r.top) / r.height * 2 + 1);
  ray.setFromCamera(ndc, camera);
  if (ray.intersectObject(pivot, true).length) { stopTour(); focused === 0 ? goHome() : focus(0); }
});

// resize
function size() {
  const w = box.clientWidth, h = box.clientHeight;
  renderer.setSize(w, h, false); camera.aspect = w / h;
  // on wide screens shift the device into the right part of the frame, clear of the copy
  if (w > 900) camera.setViewOffset(w, h, -w * 0.18, 0, w, h); else camera.clearViewOffset();
  camera.updateProjectionMatrix();
}
const ro = new ResizeObserver(() => { size(); if (ready && focused < 0 && !anim) goHome(false); }); ro.observe(box); size();

// loop
const v = new THREE.Vector3(), clock = new THREE.Clock();
renderer.setAnimationLoop(() => {
  const dt = clock.getDelta(), t = clock.elapsedTime;
  if (anim) {
    const k = Math.min(1, (performance.now() - anim.s) / anim.ms), e = ease(k);
    camera.position.lerpVectors(anim.p0, anim.p1, e); controls.target.lerpVectors(anim.t0, anim.t1, e);
    if (k >= 1) anim = null;
  }
  controls.update();
  renderer.render(scene, camera);
  if (ready) {
    const r = canvas.getBoundingClientRect();
    if (intro) introFrame(r);
    // dim the markers while the device turns on its own; zoomed in, show only the current one
    hotsEl.classList.toggle('dim', controls.autoRotate && !anim && focused < 0);
    HOT.forEach((h, i) => {
      v.copy(h.m).project(camera);
      const x = (v.x * .5 + .5) * r.width, y = (-v.y * .5 + .5) * r.height;
      const facing = camera.position.clone().sub(h.m).dot(h.m.clone().sub(center)) > -0.02;
      h.el.style.transform = `translate(${x - 15}px,${y - 15}px)`;
      h.el.style.visibility = v.z < 1 ? 'visible' : 'hidden';
      h.el.classList.toggle('back', !facing && focused !== i);
      h.el.classList.toggle('hide', focused >= 0 && focused !== i);
    });
  }
});
}
}
