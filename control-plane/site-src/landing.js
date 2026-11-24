import * as THREE from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {RoomEnvironment} from 'three/addons/environments/RoomEnvironment.js';
{
const hero = document.querySelector('.hero'), box = document.querySelector('.stagebox'), canvas = document.getElementById('stage');
const loader = document.getElementById('loader'), hotsEl = document.getElementById('hots');
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
  {k: 'DISPLAY', t: '3.5″ touch display', p: 'An IPS touch screen in an armoured bezel. Tap to start a meeting, no keyboard needed.', at: [42.5, 34, -28], mark: [0, 35, -58], dir: [0, 1.25, 0.55], d: 0.85},
  {k: 'ARMOUR', t: 'Rugged corner bumpers', p: 'Raised 45° bumpers protect every corner on a busy boardroom table.', at: [-7, 20, -64], dir: null, d: 0.62},
  {k: 'AIRFLOW', t: 'Silent passive cooling', p: 'Cool air enters below and leaves through angled side vents. No fan, no noise.', at: [61, 22, -63], mark: [61, 6, -64.2], dir: null, d: 0.62},
  {k: 'PORTS', t: 'Only the ports you need', p: 'Gigabit LAN, USB-C power and dual micro-HDMI. Every other port is sealed.', at: [92, 14, -45.7], mark: [92.8, 28, -45.7], dir: null, d: 0.62}
];
let center = new THREE.Vector3(), ready = false;
const tex = new THREE.TextureLoader().load('/static/site/vipcon_screen.jpg', t => { t.colorSpace = THREE.SRGBColorSpace; t.flipY = false; t.anisotropy = 8; });
tex.flipY = false;
new GLTFLoader().load('/static/site/vipcon_device.glb', onModel, undefined, onFail);
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
  ready = true; loader.style.opacity = 0; goHome(false);
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
  if (!wide()) p.multiplyScalar(1.12);
  return {p, t};
}
function goHome(animate = true) {
  focused = -1; cap.classList.remove('show'); HOT.forEach(h => h.el && h.el.classList.remove('on'));
  const {p, t} = homePos(); if (animate) fly(p, t); else { camera.position.copy(p); controls.target.copy(t); }
  controls.autoRotate = !reduce;
}
function focus(i) {
  const h = HOT[i]; if (!h || !h.cam) return;
  focused = i; controls.autoRotate = false;
  HOT.forEach((x, j) => x.el.classList.toggle('on', j === i));
  capK.textContent = h.k; capT.textContent = h.t; capP.textContent = h.p; cap.classList.add('show');
  fly(h.cam, h.w);
}

// cinematic tour: glide through every detail, then a slow orbit
let tourT = null, tourI = 0;
function step() { focus(tourI % HOT.length); tourI++; tourT = setTimeout(step, reduce ? 4000 : 4600); }
function startTour() { if (!ready) return; tourBtn.setAttribute('aria-pressed', 'true'); tourBtn.querySelector('span').textContent = 'Stop tour'; tourI = 0; step(); }
function stopTour() { clearTimeout(tourT); tourT = null; tourBtn.setAttribute('aria-pressed', 'false'); tourBtn.querySelector('span').textContent = 'Cinematic tour'; }
tourBtn.addEventListener('click', () => tourT ? (stopTour(), goHome()) : startTour());
resetBtn.addEventListener('click', () => { stopTour(); goHome(); });
exploreBtn.addEventListener('click', () => { if (!wide()) box.scrollIntoView({behavior: reduce ? 'auto' : 'smooth'}); stopTour(); startTour(); });
controls.addEventListener('start', () => { anim = null; stopTour(); controls.autoRotate = false; });

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
