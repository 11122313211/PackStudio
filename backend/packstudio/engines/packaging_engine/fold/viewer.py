"""Self-contained HTML preview of a dieline with an interactive 3D fold.

The generated page has two panes:

* left  – the deterministic 2D dieline SVG (reused verbatim from
  ``exporters.svg``);
* right – a Three.js scene where every material face is a ``ShapeGeometry``
  mesh and every hinge applies a Rodrigues rotation about its axis.

A 0–100% slider animates the fold progress ``t`` (fold angle = ``t * angle``).
"""

from __future__ import annotations

import json

from packstudio.engines.packaging_engine.exporters.svg import dieline_to_svg
from packstudio.engines.packaging_engine.fold.model import build_fold_model
from packstudio.engines.packaging_engine.models.ir import Dieline, StructureSpec

THREE_CDN = "https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"

_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>包装折叠预览 - __TITLE__</title>
<style>
  * { box-sizing: border-box; }
  body {
    margin: 0; font-family: "Segoe UI", "Microsoft YaHei", system-ui, sans-serif;
    background: #10141c; color: #e8edf5; height: 100vh; overflow: hidden;
  }
  header {
    padding: 10px 18px; background: #161c26; border-bottom: 1px solid #26303f;
    display: flex; align-items: baseline; gap: 14px;
  }
  header h1 { font-size: 15px; margin: 0; font-weight: 600; }
  header .sub { font-size: 12px; color: #8b98ab; }
  .app { display: flex; height: calc(100vh - 47px); }
  .pane { flex: 1 1 50%; display: flex; flex-direction: column; min-width: 0; }
  .pane.left { border-right: 1px solid #26303f; background: #141a24; }
  .pane h2 {
    font-size: 12px; margin: 0; padding: 8px 14px; color: #93a1b8;
    text-transform: uppercase; letter-spacing: .08em;
    border-bottom: 1px solid #222b38; background: #161c26;
  }
  .svg-wrap { flex: 1; overflow: auto; padding: 14px; }
  .svg-wrap svg { width: 100%; height: auto; background: #fff; border-radius: 4px; }
  #canvas-container { flex: 1; position: relative; min-height: 260px; }
  #fold-canvas { display: block; width: 100%; height: 100%; }
  .controls { padding: 10px 14px; background: #161c26; border-top: 1px solid #222b38; }
  .controls label { font-size: 12px; color: #93a1b8; display: block; margin-bottom: 6px; }
  .controls .row { display: flex; align-items: center; gap: 12px; }
  input[type=range] { flex: 1; accent-color: #4c8dff; }
  #fold-value { font-variant-numeric: tabular-nums; min-width: 44px; text-align: right; }
  .status { padding: 8px 14px; font-size: 12px; color: #ffb454; background: #1c2130; }
  .legend span { display: inline-block; margin-right: 12px; font-size: 11px; color: #93a1b8; }
  .dot { display: inline-block; width: 9px; height: 9px; border-radius: 2px; margin-right: 5px; vertical-align: middle; }
</style>
</head>
<body>
<header>
  <h1>包装折叠预览 · __TITLE__</h1>
  <span class="sub">3D fold preview · Kawasaki/Maekawa validated</span>
</header>
<div class="app">
  <div class="pane left">
    <h2>2D 刀模图 (Dieline)</h2>
    <div class="svg-wrap">__SVG__</div>
  </div>
  <div class="pane right">
    <h2>3D 折叠预览 (Fold Preview)</h2>
    <div id="canvas-container">
      <canvas id="fold-canvas"></canvas>
    </div>
    <div class="controls">
      <label>折叠进度 Fold progress
        <span id="fold-value">0%</span>
      </label>
      <div class="row">
        <input id="fold-slider" type="range" min="0" max="100" value="0" step="1">
      </div>
      <div class="legend" style="margin-top:8px">
        <span><i class="dot" style="background:#4c8dff"></i>panel fold 90°</span>
        <span><i class="dot" style="background:#ff8a3d"></i>flap fold 180°</span>
      </div>
    </div>
    <div id="fold-status" class="status"></div>
  </div>
</div>
<script src="__THREE__"></script>
<script>
(function () {
  var MODEL = __MODEL__;
  var slider = document.getElementById('fold-slider');
  var valueLabel = document.getElementById('fold-value');
  var statusBox = document.getElementById('fold-status');
  var canvas = document.getElementById('fold-canvas');

  statusBox.textContent = MODEL.available ? '' : ('折叠模型不可用：' + (MODEL.status || '未知原因'));

  var renderer, scene, camera, group;
  var faceMeshes = [];
  var pivot = { x: 0, y: 0 };

  function centre() {
    var xs = 0, ys = 0, n = 0;
    MODEL.faces.forEach(function (f) {
      f.vertices.forEach(function (v) { xs += v[0]; ys += v[1]; n += 1; });
    });
    if (n) { pivot = { x: xs / n, y: ys / n }; }
  }

  function initThree() {
    renderer = new THREE.WebGLRenderer({ canvas: canvas, antialias: true });
    renderer.setPixelRatio(window.devicePixelRatio || 1);
    renderer.setSize(canvas.clientWidth, canvas.clientHeight, false);
    renderer.setClearColor(0x10141c, 1);

    scene = new THREE.Scene();
    scene.add(new THREE.AmbientLight(0xffffff, 0.65));
    var dir = new THREE.DirectionalLight(0xffffff, 0.8);
    dir.position.set(120, 220, 260);
    scene.add(dir);
    var dir2 = new THREE.DirectionalLight(0x88aaff, 0.35);
    dir2.position.set(-200, -120, -160);
    scene.add(dir2);

    group = new THREE.Group();
    scene.add(group);

    centre();
    group.position.set(-pivot.x, -pivot.y, 0);

    var palette = [0x4c8dff, 0xff8a3d, 0x48b58b, 0xd9534f, 0xb06bd6, 0xf2c14e, 0x5ad1e0];
    MODEL.faces.forEach(function (f, i) {
      var shape = new THREE.Shape();
      var v = f.vertices;
      shape.moveTo(v[0][0], v[0][1]);
      for (var k = 1; k < v.length; k++) shape.lineTo(v[k][0], v[k][1]);
      shape.closePath();
      (f.holes || []).forEach(function (hole) {
        var path = new THREE.Path();
        path.moveTo(hole[0][0], hole[0][1]);
        for (var h = 1; h < hole.length; h++) path.lineTo(hole[h][0], hole[h][1]);
        path.closePath();
        shape.holes.push(path);
      });
      var geo = new THREE.ShapeGeometry(shape);
      var mat = new THREE.MeshStandardMaterial({
        color: palette[i % palette.length],
        side: THREE.DoubleSide,
        metalness: 0.05,
        roughness: 0.55,
        transparent: true,
        opacity: 0.92
      });
      var mesh = new THREE.Mesh(geo, mat);
      mesh.userData.faceId = f.id;
      group.add(mesh);
      faceMeshes.push(mesh);
    });

    camera = new THREE.PerspectiveCamera(45, canvas.clientWidth / canvas.clientHeight, 1, 20000);
    updateCamera();
  }

  function identityMatrix() {
    return new THREE.Matrix4().set(1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1);
  }

  function hingeMatrix(axis, angleDeg) {
    var p1 = axis[0], p2 = axis[1];
    var px = p1[0] || 0, py = p1[1] || 0, pz = 0;
    var dx = p2[0] - px, dy = p2[1] - py;
    var len = Math.sqrt(dx * dx + dy * dy) || 1;
    var x = dx / len, y = dy / len, z = 0;
    var a = angleDeg * Math.PI / 180;
    var c = Math.cos(a), s = Math.sin(a), v = 1 - c;
    var R = [
      [c + x * x * v, x * y * v - z * s, x * z * v + y * s],
      [y * x * v + z * s, c + y * y * v, y * z * v - x * s],
      [z * x * v - y * s, z * y * v + x * s, c + z * z * v]
    ];
    var tx = px - (R[0][0] * px + R[0][1] * py + R[0][2] * pz);
    var ty = py - (R[1][0] * px + R[1][1] * py + R[1][2] * pz);
    var tz = pz - (R[2][0] * px + R[2][1] * py + R[2][2] * pz);
    return new THREE.Matrix4().set(
      R[0][0], R[0][1], R[0][2], tx,
      R[1][0], R[1][1], R[1][2], ty,
      R[2][0], R[2][1], R[2][2], tz,
      0, 0, 0, 1
    );
  }

  var worldMatrices = {};

  function applyFold(t) {
    worldMatrices = {};
    if (!MODEL.available) return;
    var ids = {};
    MODEL.faces.forEach(function (f) { ids[f.id] = true; });
    worldMatrices[MODEL.root] = identityMatrix();

    var depth = {};
    depth[MODEL.root] = 0;
    for (var pass = 0; pass < MODEL.hinges.length + 1; pass++) {
      MODEL.hinges.forEach(function (h) {
        if (depth[h.parent] === undefined) return;
        var d = depth[h.parent] + 1;
        if (depth[h.child] === undefined || depth[h.child] > d) depth[h.child] = d;
      });
    }
    MODEL.hinges.forEach(function (h) {
      if (worldMatrices[h.parent] === undefined) return;
      if (!ids[h.child]) return;
      if (worldMatrices[h.child] !== undefined) return;
      var sign = h.fold_direction === 'mountain' ? -1 : 1;
      var local = hingeMatrix(h.axis, t * h.angle_deg * sign);
      worldMatrices[h.child] = worldMatrices[h.parent].clone().multiply(local);
    });
    faceMeshes.forEach(function (mesh) {
      var m = worldMatrices[mesh.userData.faceId];
      if (m) mesh.matrix.copy(m); else mesh.matrix.identity();
      mesh.matrixAutoUpdate = false;
    });
  }

  var theta = 0.9, phi = 0.85;
  function updateCamera() {
    if (!camera) return;
    var scale = Math.max(1, Math.max(Math.abs(pivot.x) * 2, Math.abs(pivot.y) * 2));
    var radius = scale * 2.6;
    camera.position.x = radius * Math.sin(phi) * Math.cos(theta);
    camera.position.y = radius * Math.cos(phi);
    camera.position.z = radius * Math.sin(phi) * Math.sin(theta);
    camera.lookAt(0, 0, 0);
  }

  function resize() {
    if (!renderer) return;
    var w = canvas.clientWidth || 400, h = canvas.clientHeight || 300;
    renderer.setSize(w, h, false);
    if (camera) { camera.aspect = w / h; camera.updateProjectionMatrix(); }
  }

  function render() {
    if (renderer) renderer.render(scene, camera);
  }

  function loop() {
    requestAnimationFrame(loop);
    render();
  }

  function setup() {
    if (!window.THREE) {
      statusBox.textContent = 'Three.js 未能从 CDN 加载，请检查网络连接。';
      return;
    }
    if (!MODEL.available) return;
    initThree();
    resize();
    applyFold(0);
    loop();
  }

  slider.addEventListener('input', function () {
    var t = parseFloat(slider.value) / 100.0;
    valueLabel.textContent = Math.round(t * 100) + '%';
    if (renderer) applyFold(t);
  });

  var dragging = false, lastX = 0, lastY = 0;
  canvas.addEventListener('mousedown', function (e) { dragging = true; lastX = e.clientX; lastY = e.clientY; });
  window.addEventListener('mouseup', function () { dragging = false; });
  window.addEventListener('mousemove', function (e) {
    if (!dragging) return;
    theta += (e.clientX - lastX) * 0.01;
    phi = Math.max(0.15, Math.min(Math.PI - 0.15, phi - (e.clientY - lastY) * 0.01));
    lastX = e.clientX; lastY = e.clientY;
    updateCamera();
  });
  window.addEventListener('resize', resize);

  setup();
})();
</script>
</body>
</html>
"""


def dieline_to_preview_html(dieline: Dieline, spec: StructureSpec | None = None) -> str:
    """Return a self-contained HTML string with 2D dieline + 3D fold preview."""
    svg = dieline_to_svg(dieline, spec)
    model = build_fold_model(dieline)
    model_json = json.dumps(model.to_dict(), ensure_ascii=False)
    # Prevent a literal </script> inside the JSON from closing the tag.
    model_json = model_json.replace("</", "<\\/")
    html = (
        _HTML.replace("__TITLE__", dieline.model_code)
        .replace("__THREE__", THREE_CDN)
        .replace("__SVG__", svg)
        .replace("__MODEL__", model_json)
    )
    return html
