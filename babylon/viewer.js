function virealFloats(b64) {
  var bin = atob(b64);
  var bytes = new Uint8Array(bin.length);
  for (var i = 0; i < bin.length; i++) { bytes[i] = bin.charCodeAt(i); }
  return new Float32Array(bytes.buffer);
}

window.virealLoad = function (payload) {
  var hud = document.getElementById('hud');
  if (!window.BABYLON) {
    hud.textContent = 'No se pudo cargar Babylon.js desde la CDN (requiere internet).';
    return;
  }
  if (window.virealEngine) {
    window.virealEngine.stopRenderLoop();
    window.virealEngine.dispose();
  }
  var canvas = document.getElementById('renderCanvas');
  var engine = new BABYLON.Engine(canvas, true, { preserveDrawingBuffer: false });
  window.virealEngine = engine;
  var scene = new BABYLON.Scene(engine);
  scene.useRightHandedSystem = true;
  var sky = payload.skyColor || [0.32, 0.48, 0.70];
  var gnd = payload.groundColor || [0.32, 0.27, 0.22];
  var ambCol = new BABYLON.Color3(
    sky[0] * 0.65 + gnd[0] * 0.35,
    sky[1] * 0.65 + gnd[1] * 0.35,
    sky[2] * 0.65 + gnd[2] * 0.35);
  scene.clearColor = new BABYLON.Color4(sky[0], sky[1], sky[2], 1.0);
  scene.ambientColor = ambCol;
  scene.environmentIntensity = 1.05;
  try {
    scene.environmentTexture = BABYLON.CubeTexture.CreateFromPrefilteredData(
      'https://assets.babylonjs.com/environments/environmentSpecular.env', scene);
  } catch (e) {
    scene.environmentTexture = null;
  }

  var c = payload.camera.center;
  var r = Math.max(2, payload.camera.radius || 10);
  var target = new BABYLON.Vector3(c[0], c[1], c[2]);
  var camera = new BABYLON.UniversalCamera('cam',
    new BABYLON.Vector3(c[0] + r, c[1] + r * 0.65, c[2] + r), scene);
  camera.setTarget(target);
  camera.attachControl(canvas, true);
  camera.speed = Math.max(0.5, r * 0.08);
  camera.angularSensibility = 6000;
  camera.minZ = 0.05;
  camera.keysUp = [87];
  camera.keysDown = [83];
  camera.keysLeft = [65];
  camera.keysRight = [68];

  var ambient = payload.ambient == null ? 0.55 : payload.ambient;
  var hemi = new BABYLON.HemisphericLight('hemi', new BABYLON.Vector3(0, 1, 0), scene);
  hemi.intensity = Math.max(1.25, ambient * 2.0);
  hemi.diffuse = new BABYLON.Color3(sky[0], sky[1], sky[2]);
  hemi.groundColor = new BABYLON.Color3(gnd[0], gnd[1], gnd[2]);
  var bounce = new BABYLON.HemisphericLight('bounce', new BABYLON.Vector3(0, -1, 0), scene);
  bounce.intensity = 0.70;
  bounce.diffuse = new BABYLON.Color3(gnd[0], gnd[1], gnd[2]);
  bounce.groundColor = new BABYLON.Color3(sky[0], sky[1], sky[2]);

  var s = payload.sun || [0, 1, 0];
  var sunDir = new BABYLON.Vector3(s[0], s[1], s[2]).normalize();
  var sc = payload.sunColor || [1, 1, 1];
  var sun = new BABYLON.DirectionalLight('sun', sunDir.scale(-1), scene);
  sun.position = target.subtract(sunDir.scale(r * 2.5));
  sun.intensity = 1.25;
  sun.diffuse = new BABYLON.Color3(sc[0], sc[1], sc[2]);

  var shadows = new BABYLON.ShadowGenerator(1024, sun);
  shadows.useBlurExponentialShadowMap = true;
  shadows.blurKernel = 8;
  shadows.darkness = 0.18;

  var created = 0;
  var materialCount = payload.runs.length;
  payload.runs.forEach(function (run, index) {
    var n = run.count || 0;
    if (!n || !run.data) { return; }
    var packed = virealFloats(run.data);
    var positions = new Float32Array(n * 3);
    var normals = new Float32Array(n * 3);
    var uvs = new Float32Array(n * 2);
    var colors = new Float32Array(n * 4);
    var indices = new Uint32Array(n);
    for (var i = 0; i < n; i++) {
      var o = i * 12;
      positions[i * 3] = packed[o];
      positions[i * 3 + 1] = packed[o + 1];
      positions[i * 3 + 2] = packed[o + 2];
      normals[i * 3] = packed[o + 3];
      normals[i * 3 + 1] = packed[o + 4];
      normals[i * 3 + 2] = packed[o + 5];
      uvs[i * 2] = packed[o + 6];
      uvs[i * 2 + 1] = packed[o + 7];
      colors[i * 4] = packed[o + 8];
      colors[i * 4 + 1] = packed[o + 9];
      colors[i * 4 + 2] = packed[o + 10];
      colors[i * 4 + 3] = packed[o + 11];
      indices[i] = i;
    }
    packed = null;

    var mesh = new BABYLON.Mesh(run.name || ('mat_' + index), scene);
    var data = new BABYLON.VertexData();
    data.positions = positions;
    data.normals = normals;
    data.uvs = uvs;
    data.colors = colors;
    data.indices = indices;
    data.applyToMesh(mesh, false);

    var mat = new BABYLON.PBRMaterial(run.name + '_mat', scene);
    mat.albedoColor = new BABYLON.Color3(
      colors[0] || 1, colors[1] || 1, colors[2] || 1);
    mat.metallic = Math.max(0, Math.min(1, run.metal || 0));
    mat.roughness = Math.max(0.04, Math.min(1, run.rough == null ? 0.8 : run.rough));
    mat.alpha = run.alpha == null ? 1 : run.alpha;
    mat.backFaceCulling = false;
    mat.useAmbientColor = true;
    mat.ambientColor = ambCol;
    mat.environmentIntensity = 1.05;
    if (run.texture) {
      var tex = new BABYLON.Texture(run.texture, scene, false, false);
      tex.hasAlpha = mat.alpha < 1;
      mat.albedoTexture = tex;
    }
    mesh.material = mat;
    mesh.receiveShadows = true;
    shadows.addShadowCaster(mesh);
    created += n / 3;
    run.data = null;
  });
  payload.runs.length = 0;

  hud.innerHTML = '<b>Babylon.js beta</b><br>' + created.toLocaleString() +
    ' triángulos · ' + materialCount + ' materiales<br>' +
    (payload.stats || '') + '<br>WASD mover · arrastrar mirar · rueda acercar';
  payload.stats = null;
  engine.runRenderLoop(function () { scene.render(); });
  window.addEventListener('resize', function () { engine.resize(); });
  engine.resize();
};
