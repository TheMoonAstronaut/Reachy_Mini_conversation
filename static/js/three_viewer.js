/**
 * three_viewer.js — Reachy Mini 交互式 3D 视图(V1.3,方案 B:three.js 重渲染)
 *
 * 功能(HANDOVER_SESSION_5 §六 P0 V1 验收):
 *   - 鼠标左键拖拽 = 旋转视角,右键拖拽 = 平移,滚轮 = 缩放(OrbitControls 默认映射,
 *     与 MuJoCo 原生 viewer 一致)
 *   - 实时跟随 daemon 状态:WebSocket /ws/state @25Hz 推送
 *     {head_joints[7], antennas[2], head_pose[16]}
 *
 * 运动学(与 tools/export_visual_manifest.py 的 manifest 一一对应,已数值验证):
 *   - body 组:      quaternion = q0 * Rz(head_joints[0])          (yaw_body)
 *   - horn 组 i:    quaternion = q_hi * Rz(head_joints[i+1])      (stewart_1..6)
 *   - head 组:      matrix = head_pose_4x4,且 z 平移 += 0.177
 *                   (SDK get_current_head_pose 把 site z 减了 0.177,这里还原)
 *   - antenna 组:   quaternion = q_f * Rz(-antennas[i])
 *                   (SDK 对天线 qpos 取负,故前端再取负还原)
 *   - Stewart 连杆(被动球关节)V1 不渲染 —— 视觉近似,头体间细杆留空。
 *
 * 坐标系:全程 MuJoCo 世界系(右手系,Z-up,米),camera.up = (0,0,1)。
 * STL 顶点坐标系 == XML mesh 原始系(已顶点级验证 == MuJoCo 世界渲染)。
 */

import * as THREE from './vendor/three.module.js';
import { OrbitControls } from './vendor/OrbitControls.js';
import { STLLoader } from './vendor/STLLoader.js';

const HEAD_POSE_Z_OFFSET = 0.177; // SDK head_pose 的 z 修正(见模块注释)
const WS_RECONNECT_MS = 2000;     // 断线重连间隔(与 MJPEG onerror 自愈同节奏)

// rest 姿态兜底(WS 首帧到达前显示):site rest 世界 = (0,0,0.14957) identity,
// SDK 值 = z-0.177 → -0.02743
const REST_HEAD_POSE = [
  1, 0, 0, 0,
  0, 1, 0, 0,
  0, 0, 1, -0.02743,
  0, 0, 0, 1,
];

// ---------- 运动学装配(纯函数,无渲染器依赖 → node 可测)----------
// 输入 manifest + 已加载的 geometry/material 工厂,输出 { root, applyState }。
// 与 tests/test_visual_manifest.py 的 Python 链同一套约定,node 侧有对拍测试。
export function buildRobotRig(manifest, { loadGeometry, materialFor }) {
  const groups = manifest.groups;
  const root = new THREE.Group();
  const Z_AXIS = new THREE.Vector3(0, 0, 1);

  const baseGroup = new THREE.Group();
  root.add(baseGroup);

  const bodyQ0 = new THREE.Quaternion().fromArray(groups.body.frame.quat_xyzw);
  const bodyGroup = new THREE.Group();
  bodyGroup.position.fromArray(groups.body.frame.pos);
  bodyGroup.quaternion.copy(bodyQ0);
  root.add(bodyGroup);

  const hornPivots = groups.stewart_horns.map((horn) => {
    const pivot = new THREE.Group();
    pivot.position.fromArray(horn.frame_in_body.pos);
    pivot.userData.q0 = new THREE.Quaternion().fromArray(horn.frame_in_body.quat_xyzw);
    pivot.quaternion.copy(pivot.userData.q0);
    bodyGroup.add(pivot);
    return pivot;
  });

  const headGroup = new THREE.Group();
  headGroup.matrixAutoUpdate = false;
  root.add(headGroup);

  const antennaPivots = ['antenna_right', 'antenna_left'].map((key) => {
    const info = groups[key];
    const pivot = new THREE.Group();
    pivot.position.fromArray(info.frame_in_head_site.pos);
    pivot.userData.q0 = new THREE.Quaternion().fromArray(info.frame_in_head_site.quat_xyzw);
    pivot.quaternion.copy(pivot.userData.q0);
    headGroup.add(pivot);
    return pivot;
  });

  // Stewart 连杆 ×6(被动球关节,两端球铰连线法):
  // 杆挂 root(世界系直摆),每帧 A=hornPivot.localToWorld(bottom)、
  // B=headGroup.localToWorld(top),position=midpoint,x 轴对齐 B→A。
  // mesh 几何:x 轴居中(±0.0485m),无需额外偏移。
  const rodGroups = (groups.stewart_rods?.rods ?? []).map((rod) => {
    const g = new THREE.Group();
    g.userData.bottom = new THREE.Vector3().fromArray(rod.bottom_in_horn);
    g.userData.top = new THREE.Vector3().fromArray(rod.top_in_head_site);
    g.userData.hornIndex = rod.horn_index;
    root.add(g);
    return g;
  });

  // 几何装配(异步,调用方 await ready)
  async function addGeoms(parent, geoms) {
    await Promise.all(
      geoms.map(async (g) => {
        const geo = await loadGeometry(g.mesh);
        const mesh = new THREE.Mesh(geo, materialFor(g.rgba));
        mesh.position.fromArray(g.pos);
        mesh.quaternion.fromArray(g.quat_xyzw);
        parent.add(mesh);
      }),
    );
  }
  const ready = (async () => {
    await addGeoms(baseGroup, groups.base.geoms);
    await addGeoms(bodyGroup, groups.body.geoms);
    for (let i = 0; i < hornPivots.length; i++) {
      await addGeoms(hornPivots[i], groups.stewart_horns[i].geoms);
    }
    await addGeoms(headGroup, groups.head.geoms);
    await addGeoms(antennaPivots[0], groups.antenna_right.geoms);
    await addGeoms(antennaPivots[1], groups.antenna_left.geoms);
    // 连杆 mesh(6 根共享同一 geometry,各自实例)
    if (groups.stewart_rods) {
      const rodGeo = await loadGeometry(groups.stewart_rods.mesh);
      const rodMat = materialFor(groups.stewart_rods.rgba);
      for (const g of rodGroups) {
        g.add(new THREE.Mesh(rodGeo, rodMat));
      }
    }
    root.updateMatrixWorld(true);
  })();

  const tmpQ = new THREE.Quaternion();
  const headMatrix = new THREE.Matrix4();

  function applyState(s) {
    const hj = s.head_joints;
    if (Array.isArray(hj) && hj.length === 7) {
      bodyGroup.quaternion.copy(bodyQ0).multiply(tmpQ.setFromAxisAngle(Z_AXIS, hj[0]));
      for (let i = 0; i < hornPivots.length; i++) {
        hornPivots[i].quaternion
          .copy(hornPivots[i].userData.q0)
          .multiply(tmpQ.setFromAxisAngle(Z_AXIS, hj[i + 1]));
      }
    }
    if (Array.isArray(s.antennas) && s.antennas.length === 2) {
      for (let i = 0; i < 2; i++) {
        antennaPivots[i].quaternion
          .copy(antennaPivots[i].userData.q0)
          .multiply(tmpQ.setFromAxisAngle(Z_AXIS, -s.antennas[i])); // SDK 取负,前端还原
      }
    }
    if (Array.isArray(s.head_pose) && s.head_pose.length === 16) {
      headMatrix.set(...s.head_pose); // row-major 入参,与 payload 一致
      headMatrix.elements[14] += HEAD_POSE_Z_OFFSET; // elements 列主序,[14]=tz
      headGroup.matrix.copy(headMatrix);
      headGroup.matrixWorldNeedsUpdate = true;
    }
    // 连杆:依赖 horn/head 的最新世界矩阵,必须在 updateMatrixWorld 之后算
    if (rodGroups.length) {
      root.updateMatrixWorld(true);
      const A = new THREE.Vector3();
      const B = new THREE.Vector3();
      const X = new THREE.Vector3(1, 0, 0);
      const dir = new THREE.Vector3();
      for (const g of rodGroups) {
        A.copy(g.userData.bottom);
        hornPivots[g.userData.hornIndex].localToWorld(A);
        B.copy(g.userData.top);
        headGroup.localToWorld(B);
        g.position.addVectors(A, B).multiplyScalar(0.5);
        dir.subVectors(B, A);
        if (dir.lengthSq() > 1e-12) {
          g.quaternion.setFromUnitVectors(X, dir.normalize());
        }
      }
    }
    root.updateMatrixWorld(true);
  }

  return { root, ready, applyState, bodyGroup, headGroup, hornPivots, antennaPivots, rodGroups };
}

/**
 * 创建并挂载一个 Reachy 3D 视图。
 * @param {HTMLElement} container 挂载点(建议 min-height ≥ 320px)
 * @param {{baseUrl?: string}} opts baseUrl = 7861 FastAPI origin(静态资源 + WS)
 * @returns {Promise<{dispose: () => void}>}
 */
export async function createReachyViewer(container, opts = {}) {
  // LAN 访问(2026-09-18):默认 baseUrl 从当前页面主机推导 —— 同一 WiFi 下
  // 其他设备用 http://<PC局域网IP>:7860 打开时,7861 资源必须跟着走局域网 IP,
  // 不能写死 localhost(那是设备自己)。调用方(web_ui js_on_load)会显式传
  // baseUrl,这里只兜默认值。
  const baseUrl = (
    opts.baseUrl ?? `${location.protocol}//${location.hostname}:7861`
  ).replace(/\/$/, '');
  const wsUrl = baseUrl.replace(/^http/, 'ws') + '/ws/state';

  // ---------- renderer / scene / camera(Z-up)----------
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.domElement.style.display = 'block';
  renderer.domElement.style.borderRadius = '8px';
  container.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0d1117); // 与 Gradio 深色主题一致

  const camera = new THREE.PerspectiveCamera(35, 1, 0.01, 20);
  camera.up.set(0, 0, 1); // Z-up(MuJoCo 系)
  // 初始机位对齐 MJCF 的 studio_close(pos 0.8 -0.2 0.4, 看向头部附近)
  camera.position.set(0.55, -0.35, 0.4);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.target.set(0, 0, 0.15);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.minDistance = 0.15;
  controls.maxDistance = 3.0;
  // 默认映射即需求:LEFT 旋转 / RIGHT 平移 / WHEEL 缩放;显式钉住防漂移
  controls.mouseButtons = {
    LEFT: THREE.MOUSE.ROTATE,
    MIDDLE: THREE.MOUSE.DOLLY,
    RIGHT: THREE.MOUSE.PAN,
  };

  // ---------- 灯光(对齐 scene.xml:顶部 directional + 环境)----------
  scene.add(new THREE.HemisphereLight(0xffffff, 0x22303f, 0.9));
  const dirLight = new THREE.DirectionalLight(0xffffff, 1.2);
  dirLight.position.set(-0.5, 0, 3.5);
  scene.add(dirLight);

  // ---------- 地面(scene.xml 的 checker floor 简化为网格)----------
  const grid = new THREE.GridHelper(1.0, 20, 0x2a3442, 0x1a2230);
  grid.rotation.x = Math.PI / 2; // GridHelper 默认 XZ 平面 → 转到 XY(Z-up)
  scene.add(grid);

  // ---------- 状态角标 ----------
  const badge = document.createElement('div');
  badge.className = 'rm-3d-badge';
  badge.style.cssText =
    'position:absolute;top:8px;left:10px;font:600 12px/1.5 Inter,system-ui,sans-serif;' +
    'color:#8b98ab;background:rgba(21,27,36,.85);border:1px solid #2a3442;' +
    'border-radius:999px;padding:3px 10px;pointer-events:none;';
  container.style.position = 'relative';
  container.appendChild(badge);
  const setBadge = (text, color) => {
    badge.textContent = text;
    badge.style.color = color;
  };
  setBadge('⏳ 加载 3D 模型…', '#8b98ab');

  // ---------- 加载 manifest + 全部 STL(带缓存)----------
  // no-cache:manifest 重新生成后 URL 不变,强制再验证防拿旧清单
  const manifest = await (
    await fetch(`${baseUrl}/static/meshes/manifest.json`, { cache: 'no-cache' })
  ).json();
  const loader = new STLLoader();
  const geoCache = new Map();
  async function loadGeometry(meshName) {
    if (!geoCache.has(meshName)) {
      geoCache.set(
        meshName,
        loader.loadAsync(`${baseUrl}/static/meshes/${meshName}.stl`),
      );
    }
    return geoCache.get(meshName);
  }
  const matCache = new Map();
  function materialFor(rgba) {
    const key = rgba.join(',');
    if (!matCache.has(key)) {
      const [r, g, b, a] = rgba;
      matCache.set(
        key,
        new THREE.MeshStandardMaterial({
          color: new THREE.Color(r, g, b),
          roughness: 0.75,
          metalness: 0.08,
          transparent: a < 0.999,
          opacity: a,
        }),
      );
    }
    return matCache.get(key);
  }

  // ---------- 按 manifest 组装运动学分组(纯函数,node 可测)----------
  const rig = buildRobotRig(manifest, { loadGeometry, materialFor });
  scene.add(rig.root);
  await rig.ready;

  // 兜底:WS 首帧前的 rest 姿态
  rig.applyState({
    head_joints: [0, 0, 0, 0, 0, 0, 0],
    antennas: [0, 0],
    head_pose: REST_HEAD_POSE,
  });
  setBadge('⏳ 等待实时状态(ws/state)…', '#8b98ab');

  // ---------- WebSocket 订阅(断线自愈,与 MJPEG onerror 同策略)----------
  let ws = null;
  let wsAlive = false;
  let latestState = null;
  let reconnectTimer = null;

  function connect() {
    ws = new WebSocket(wsUrl);
    ws.onopen = () => {
      wsAlive = true;
      setBadge('🟢 实时状态已连接(25 Hz)', '#3fb950');
    };
    ws.onmessage = (ev) => {
      try {
        latestState = JSON.parse(ev.data);
      } catch {
        /* 单帧解析失败忽略,等下一帧 */
      }
    };
    ws.onclose = () => {
      wsAlive = false;
      setBadge('🔴 状态通道断开,2s 后重连…', '#f85149');
      reconnectTimer = setTimeout(connect, WS_RECONNECT_MS);
    };
    ws.onerror = () => {
      ws.close();
    };
  }
  connect();

  // ---------- 渲染循环(rAF 驱动,WS 帧与渲染解耦)----------
  let rafId = 0;
  function tick() {
    rafId = requestAnimationFrame(tick);
    if (latestState) {
      rig.applyState(latestState);
      latestState = null;
    }
    controls.update();
    renderer.render(scene, camera);
  }

  // ---------- 自适应尺寸 ----------
  function resize() {
    const w = container.clientWidth || 640;
    const h = container.clientHeight || 480;
    renderer.setSize(w, h);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  const observer = new ResizeObserver(resize);
  observer.observe(container);
  resize();
  tick();

  // ---------- 清理 ----------
  return {
    rig, // 暴露给 E2E 测试/调试(只读使用)
    dispose() {
      cancelAnimationFrame(rafId);
      observer.disconnect();
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (ws) ws.close();
      controls.dispose();
      renderer.dispose();
      renderer.domElement.remove();
      badge.remove();
    },
  };
}

// Gradio 侧入口:js_on_load 里调 window.ReachyViewer.mount(elem)
// (typeof 守卫:让 node 能做模块解析冒烟检查,浏览器行为不变)
if (typeof window !== 'undefined') {
  window.ReachyViewer = {
    _instance: null,
    async mount(container, opts) {
      if (this._instance) {
        this._instance.dispose(); // Gradio 重渲染时防重复挂载/泄漏
        this._instance = null;
      }
      this._instance = await createReachyViewer(container, opts);
      return this._instance;
    },
  };
}
