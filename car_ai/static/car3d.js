/* The car on the cockpit stage: a white 4 Series Gran Coupe, spinning slowly.
 *
 * Everything is local (static/vendor/three, static/models): no network in the
 * car means no CDN. If WebGL or the model is missing, a simple built-in coupe
 * stands in, and if WebGL itself is unavailable the stage just stays empty --
 * the dials never depend on this file.
 *
 * Kept light for the Pi 5: capped at 30 fps, pixel ratio capped, and no
 * rendering at all while the cockpit isn't on screen. The rim light takes the
 * colour of the car's worst status (app.js fires "car-mood"). */

import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";

const MODEL_URL = "/static/models/car.glb";
const SPIN_RAD_PER_S = (2 * Math.PI) / 24;   // one turn every 24 s: calm, not busy
const FRAME_MS = 1000 / 30;
const MOOD_COLOURS = { ok: 0x2fd8ff, warn: 0xffb020, crit: 0xff4d5e };

const canvas = document.getElementById("car-canvas");
const stage = document.getElementById("stage");

function start() {
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true,
                                         powerPreference: "low-power" });
  } catch (err) {
    console.warn("car3d: no WebGL, leaving the stage empty", err);
    return;
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 0.85;   // white paint clips above this

  const scene = new THREE.Scene();
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;

  const camera = new THREE.PerspectiveCamera(28, 1, 0.1, 100);
  camera.position.set(0, 2.1, 10.5);
  camera.lookAt(0, 0.55, 0);

  scene.add(new THREE.HemisphereLight(0xdfe9ff, 0x0b1018, 0.6));
  const key = new THREE.DirectionalLight(0xffffff, 1.6);
  key.position.set(4, 6, 5);
  scene.add(key);
  const rim = new THREE.PointLight(MOOD_COLOURS.ok, 30, 14, 1.6);
  rim.position.set(0, 0.4, -3.2);
  scene.add(rim);

  // A soft contact shadow under the car, so it sits on the glowing floor.
  const shadow = new THREE.Mesh(
    new THREE.CircleGeometry(2.6, 48),
    new THREE.MeshBasicMaterial({ color: 0x000000, transparent: true, opacity: 0.55 }));
  shadow.rotation.x = -Math.PI / 2;
  shadow.scale.set(1, 0.48, 1);
  shadow.position.y = 0.01;
  scene.add(shadow);

  const turntable = new THREE.Group();
  scene.add(turntable);

  // Fit any model: ~4.6 units long, centred, wheels on y=0.
  function place(object) {
    const box = new THREE.Box3().setFromObject(object);
    const size = box.getSize(new THREE.Vector3());
    const scale = 4.6 / Math.max(size.x, size.z);
    object.scale.setScalar(scale);
    box.setFromObject(object);
    const centre = box.getCenter(new THREE.Vector3());
    object.position.sub(new THREE.Vector3(centre.x, box.min.y, centre.z));
    turntable.clear();
    turntable.add(object);
  }

  place(standInCar());
  new GLTFLoader().load(MODEL_URL, (gltf) => {
    // The generated model ships plain PBR textures. Re-wrap them in a
    // clearcoat material so the white paint catches the studio reflections
    // like real lacquer does.
    gltf.scene.traverse((o) => {
      if (!o.isMesh || !o.material) return;
      const m = o.material;
      o.material = new THREE.MeshPhysicalMaterial({
        map: m.map, normalMap: m.normalMap, roughnessMap: m.roughnessMap,
        metalnessMap: m.metalnessMap, roughness: m.roughness, metalness: m.metalness,
        clearcoat: 0.8, clearcoatRoughness: 0.12, envMapIntensity: 1.2,
      });
      m.dispose();
    });
    place(gltf.scene);
  }, undefined, () => console.info("car3d: no model at", MODEL_URL, "- using the stand-in"));

  window.addEventListener("car-mood", (e) => rim.color.setHex(MOOD_COLOURS[e.detail] ?? MOOD_COLOURS.ok));

  function resize() {
    const w = stage.clientWidth, h = stage.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    // Keep the whole car in frame on narrow stages (phones, the 7" screen).
    camera.position.z = 7.8 * Math.max(1, 1.3 / camera.aspect);
    camera.updateProjectionMatrix();
  }
  new ResizeObserver(resize).observe(stage);
  resize();

  let last = performance.now(), lastFrame = 0;
  function frame(now) {
    requestAnimationFrame(frame);
    const visible = document.body.dataset.view === "cockpit" && !document.hidden;
    const dt = Math.min((now - last) / 1000, 0.1);
    last = now;
    if (!visible || now - lastFrame < FRAME_MS) return;
    lastFrame = now;
    turntable.rotation.y += SPIN_RAD_PER_S * Math.max(dt, FRAME_MS / 1000);
    renderer.render(scene, camera);
  }
  requestAnimationFrame(frame);
}

/* A simple white coupe from primitives: the stand-in until (or if) the real
   model loads. Proportions roughly a 4 Series Gran Coupe. */
function standInCar() {
  const car = new THREE.Group();
  const paint = new THREE.MeshPhysicalMaterial({ color: 0xf4f6f8, metalness: 0.15,
    roughness: 0.32, clearcoat: 1, clearcoatRoughness: 0.08 });
  const glass = new THREE.MeshPhysicalMaterial({ color: 0x0a0f18, metalness: 0.2,
    roughness: 0.05, clearcoat: 1 });
  const tyre = new THREE.MeshStandardMaterial({ color: 0x111317, roughness: 0.85 });
  const rimMat = new THREE.MeshStandardMaterial({ color: 0x9aa3ad, metalness: 0.9, roughness: 0.3 });
  const light = new THREE.MeshBasicMaterial({ color: 0xdff6ff });
  const tail = new THREE.MeshBasicMaterial({ color: 0xff2a3a });

  const body = new THREE.Mesh(new THREE.BoxGeometry(4.6, 0.62, 1.85), paint);
  body.position.y = 0.62;
  const nose = new THREE.Mesh(new THREE.BoxGeometry(0.9, 0.42, 1.8), paint);
  nose.position.set(2.05, 0.55, 0);
  const cabin = new THREE.Mesh(new THREE.BoxGeometry(2.3, 0.55, 1.6), glass);
  cabin.position.set(-0.25, 1.18, 0);
  const roof = new THREE.Mesh(new THREE.BoxGeometry(1.7, 0.08, 1.5), paint);
  roof.position.set(-0.35, 1.48, 0);
  car.add(body, nose, cabin, roof);

  for (const z of [-0.62, 0.62]) {
    const head = new THREE.Mesh(new THREE.BoxGeometry(0.06, 0.1, 0.4), light);
    head.position.set(2.5, 0.68, z);
    const back = new THREE.Mesh(new THREE.BoxGeometry(0.06, 0.1, 0.45), tail);
    back.position.set(-2.31, 0.75, z);
    car.add(head, back);
  }
  for (const x of [1.45, -1.45]) for (const z of [-0.93, 0.93]) {
    const wheel = new THREE.Mesh(new THREE.CylinderGeometry(0.36, 0.36, 0.26, 28), tyre);
    wheel.rotation.x = Math.PI / 2;
    wheel.position.set(x, 0.36, z);
    const hub = new THREE.Mesh(new THREE.CylinderGeometry(0.24, 0.24, 0.27, 20), rimMat);
    hub.rotation.x = Math.PI / 2;
    hub.position.copy(wheel.position);
    car.add(wheel, hub);
  }
  return car;
}

start();
