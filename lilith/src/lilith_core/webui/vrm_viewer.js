/* LILITH-CORE · VRM-вьювер лица (этап 5 v2)
 * Ванilla-JS компонент панели: three.js + three-vrm из локального vendor/
 * (офлайн-требование панели). Без vendor или без model.vrm — фолбэк-аватар.
 *
 * Публичный API: window.LilithViewer = { init, load, setFallback, applyViseme,
 *   applyEmotion, state }
 * Кадры приходят с сервера: виземы A/I/U/E/O (корзины) и имена эмоций из
 * face/emotions.py; здесь — сглаживание (low-pass), idle-моргание, дыхание,
 * взгляд за курсором.
 */
(function () {
  "use strict";

  const VISEME_TO_VRM = { A: "aa", I: "ih", U: "ou", E: "ee", O: "oh", rest: "aa" };
  const EMOTION_TO_VRM = {
    joy: ["happy"], love: ["happy"], smug: ["happy", "relaxed"],
    anger: ["angry"], sadness: ["sad"], surprise: ["surprised"],
    embarrassment: ["sad", "happy"], sleepy: ["relaxed"], evil: ["angry", "happy"],
    neutral: [],
  };

  const viewer = {
    mode: "init",           // init | vrm | fallback | error
    viseme: "rest",
    visemeIntensity: 0,
    emotion: "neutral",
    fps: 0,
    error: null,
  };

  let THREE = null, VRM = null, vrm = null;
  let renderer = null, scene = null, camera = null, lookTarget = null;
  let canvas = null, overlay = null, stage = null, fallbackImg = null;
  const targets = {}, current = {};
  let blinkAt = 2 + Math.random() * 3, blinkPhase = -1;
  let emotionHoldUntil = 0;
  let lastT = performance.now(), fpsAcc = 0, fpsN = 0;

  function setOverlay() {
    if (!overlay) return;
    overlay.textContent =
      "лицо: " + viewer.mode +
      " | визема: " + viewer.viseme + " " + viewer.visemeIntensity.toFixed(2) +
      " | эмоция: " + viewer.emotion +
      " | fps: " + viewer.fps;
  }

  function bump(key, value) { targets[key] = value; }

  viewer.applyViseme = function (basket, intensity) {
    viewer.viseme = basket;
    viewer.visemeIntensity = intensity || 0;
    const vrmName = VISEME_TO_VRM[basket] || "aa";
    for (const k of ["aa", "ih", "ou", "ee", "oh"]) targets[k] = 0;
    targets[vrmName] = viewer.visemeIntensity;
    if (viewer.mode === "fallback" && fallbackImg) {
      fallbackImg.style.transform = "scale(" + (1 + 0.02 * viewer.visemeIntensity).toFixed(3) + ")";
    }
  };

  viewer.applyEmotion = function (name) {
    viewer.emotion = name;
    emotionHoldUntil = performance.now() + 2600;
    for (const preset of ["happy", "angry", "sad", "relaxed", "surprised"]) targets[preset] = 0;
    for (const preset of (EMOTION_TO_VRM[name] || [])) targets[preset] = 1;
    if (viewer.mode === "fallback" && fallbackImg) {
      fallbackImg.style.filter = name === "anger" ? "saturate(1.3) hue-rotate(-12deg)"
        : name === "sadness" ? "saturate(.8) brightness(.92)"
        : name === "joy" || name === "love" ? "saturate(1.15) brightness(1.05)" : "none";
    }
  };

  viewer.setFallback = function (url) {
    viewer.mode = "fallback";
    if (fallbackImg) {
      if (url) {
        fallbackImg.onerror = function () {
          viewer.error = "fallback не загрузился: " + url;
          setOverlay();
        };
        fallbackImg.src = url;
        fallbackImg.hidden = false;
        fallbackImg.style.display = "block";
      } else {
        viewer.error = viewer.error || "нет ни model.vrm, ни fallback-картинки";
      }
    }
    if (canvas) canvas.hidden = true;
    setOverlay();
  };

  viewer.load = async function (url) {
    if (!url) { viewer.error = "нет model.vrm"; return false; }
    try {
      const [{ GLTFLoader }, vrmMod] = await Promise.all([
        import("./vendor/GLTFLoader.js"),
        import("./vendor/three-vrm.module.js"),
      ]);
      const loader = new GLTFLoader();
      loader.register((parser) => new vrmMod.VRMLoaderPlugin(parser));
      const gltf = await loader.loadAsync(url);
      vrm = gltf.userData.vrm;
      VRM = vrmMod;
      vrm.scene.rotation.y = Math.PI;
      scene.add(vrm.scene);
      if (vrm.lookAt) vrm.lookAt.target = lookTarget;
      viewer.mode = "vrm";
      if (fallbackImg) fallbackImg.hidden = true;
      if (canvas) canvas.hidden = false;
      setOverlay();
      return true;
    } catch (e) {
      viewer.error = String(e);
      viewer.mode = "error";
      setOverlay();
      return false;
    }
  };

  viewer.init = async function (stageEl, canvasEl, overlayEl, imgEl) {
    stage = stageEl; canvas = canvasEl; overlay = overlayEl; fallbackImg = imgEl;
    try {
      THREE = await import("./vendor/three.module.js");
      renderer = new THREE.WebGLRenderer({ canvas: canvas, alpha: true, antialias: true });
      renderer.setClearColor(0x000000, 0);
      scene = new THREE.Scene();
      camera = new THREE.PerspectiveCamera(24, 1, 0.1, 100);
      camera.position.set(0, 1.35, 1.15);
      lookTarget = new THREE.Object3D();
      lookTarget.position.set(0, 1.38, 0);
      scene.add(lookTarget);
      const key = new THREE.DirectionalLight(0xffffff, 1.1);
      key.position.set(0.5, 1.8, 1.0);
      scene.add(key);
      scene.add(new THREE.AmbientLight(0xb090c0, 0.7));
      stage.addEventListener("mousemove", (ev) => {
        const r = stage.getBoundingClientRect();
        const x = ((ev.clientX - r.left) / r.width) * 2 - 1;
        const y = ((ev.clientY - r.top) / r.height) * 2 - 1;
        lookTarget.position.set(x * 0.35, 1.38 - y * 0.22, 0.4);
      });
      window.addEventListener("resize", resize);
      resize();
      requestAnimationFrame(tick);
      return true;
    } catch (e) {
      viewer.error = String(e);
      viewer.mode = "error";
      setOverlay();
      return false;
    }
  };

  function resize() {
    if (!renderer || !stage) return;
    const w = stage.clientWidth, h = stage.clientHeight;
    renderer.setSize(w, h, false);
    camera.aspect = w / Math.max(1, h);
    camera.updateProjectionMatrix();
  }

  function tick(now) {
    requestAnimationFrame(tick);
    const dt = Math.min(0.1, (now - lastT) / 1000);
    lastT = now;
    fpsAcc += dt; fpsN++;
    if (fpsAcc > 0.5) { viewer.fps = Math.round(fpsN / fpsAcc); fpsAcc = 0; fpsN = 0; }

    // сглаживание визем и эмоций (low-pass)
    const k = Math.min(1, dt * 16);
    for (const key of Object.keys(targets)) {
      current[key] = (current[key] || 0) + (targets[key] - (current[key] || 0)) * k;
    }
    // эмоция держится ~2.6 c, затем возвращается к neutral
    if (performance.now() > emotionHoldUntil) {
      for (const preset of ["happy", "angry", "sad", "relaxed", "surprised"]) targets[preset] = 0;
    }

    if (vrm && viewer.mode === "vrm") {
      // моргание: случайный таймер, фаза 0..1
      if (blinkPhase < 0 && now / 1000 > blinkAt) { blinkPhase = 0; }
      let blink = 0;
      if (blinkPhase >= 0) {
        blinkPhase += dt / 0.16;
        blink = Math.sin(Math.min(1, blinkPhase) * Math.PI);
        if (blinkPhase >= 1) { blinkPhase = -1; blinkAt = now / 1000 + 2 + Math.random() * 3.5; }
      }
      const em = vrm.expressionManager;
      if (em) {
        for (const key of ["aa", "ih", "ou", "ee", "oh"]) em.setValue(key, current[key] || 0);
        for (const preset of ["happy", "angry", "sad", "relaxed", "surprised"]) {
          em.setValue(preset, current[preset] || 0);
        }
        em.setValue("blink", blink);
      }
      // дыхание и лёгкое покачивание
      vrm.scene.position.y = Math.sin(now / 1000 * 1.25) * 0.008;
      vrm.scene.rotation.z = Math.sin(now / 1000 * 0.6) * 0.008;
      vrm.update(dt);
    }

    if (renderer && scene && camera) renderer.render(scene, camera);
    setOverlay();
  }

  window.LilithViewer = viewer;
})();
