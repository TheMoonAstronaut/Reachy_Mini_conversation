// test_three_rig.mjs — buildRobotRig 的 node 侧对拍测试(V1.3)
//
// 验证:JS 的运动学装配(四元数后乘、head_pose z+0.177 还原、天线取负)
// 与 Python 侧已验证到 1e-16 的 MuJoCo FK 链一致(golden fixture 由
// tools 注释里的生成器产出,见 tests/golden_rig_fixture.json)。
//
// 运行:node tests/test_three_rig.mjs   (无需浏览器/WebGL —— 纯场景图数学)

import * as THREE from '../static/js/vendor/three.module.js';
import { buildRobotRig } from '../static/js/three_viewer.js';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const manifest = JSON.parse(
  readFileSync(join(here, '../static/meshes/manifest.json'), 'utf-8'),
);
const fixture = JSON.parse(
  readFileSync(join(here, 'golden_rig_fixture.json'), 'utf-8'),
);

let failures = 0;
function check(name, actual, expected, tol = 1e-9) {
  const err = Math.max(...actual.map((v, i) => Math.abs(v - expected[i])));
  const ok = err < tol;
  if (!ok) failures++;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}  max_err=${err.toExponential(2)}`);
}

// stub:不真的加载 STL(纯场景图测试)
const stubGeo = () => new THREE.BufferGeometry();
const stubMat = () => new THREE.MeshBasicMaterial();

const rig = buildRobotRig(manifest, { loadGeometry: async () => stubGeo(), materialFor: stubMat });
await rig.ready;

// 结构断言:分组网格数 == manifest geom 数
function countMeshes(obj) {
  let n = 0;
  obj.traverse((o) => { if (o.isMesh) n++; });
  return n;
}
const totalGeoms =
  manifest.groups.base.geoms.length +
  manifest.groups.body.geoms.length +
  manifest.groups.stewart_horns.reduce((s, h) => s + h.geoms.length, 0) +
  (manifest.groups.stewart_rods?.rods.length ?? 0) + // 连杆:每根 1 个 mesh
  manifest.groups.head.geoms.length +
  manifest.groups.antenna_right.geoms.length +
  manifest.groups.antenna_left.geoms.length;
const meshCount = countMeshes(rig.root);
if (meshCount !== totalGeoms) failures++;
console.log(`${meshCount === totalGeoms ? 'PASS' : 'FAIL'}  网格总数 ${meshCount} == ${totalGeoms}`);

// 应用 golden 状态(模拟 /ws/state 一帧)
rig.applyState(fixture.input_state);

// 对拍:各组原点世界坐标
// 容差说明:fixture 的 input_state 是 MuJoCo 软约束**收敛后**的关节值,
// 纯运动学链 vs MuJoCo 内部状态有 ~1e-8 量级差异(运动学精确性已在
// tests/test_visual_manifest.py 用未收敛值证明到 1e-16),这里放宽到 1e-6。
const exp = fixture.expected_world_pos;
const v = new THREE.Vector3();
check('body 原点', rig.bodyGroup.getWorldPosition(v.clone()).toArray(), exp.body, 1e-6);
check('horn[0] 原点', rig.hornPivots[0].getWorldPosition(v.clone()).toArray(), exp.horn_0, 1e-6);
check('horn[5] 原点', rig.hornPivots[5].getWorldPosition(v.clone()).toArray(), exp.horn_5, 1e-6);
check('antenna_right 原点', rig.antennaPivots[0].getWorldPosition(v.clone()).toArray(), exp.antenna_right, 1e-6);
check('antenna_left 原点', rig.antennaPivots[1].getWorldPosition(v.clone()).toArray(), exp.antenna_left, 1e-6);

// head:matrix 应 == site_world(z+0.177 还原后),row-major 展开逐元素对拍
check('head matrix', [...rig.headGroup.matrix.elements], transposeColMajor(fixture.expected_head_matrix_rowmajor));

// Stewart 连杆:每根的 A(下端铰)/ B(上端铰)世界坐标对拍(容差 2mm,软约束回弹)
if (fixture.expected_rods) {
  const A = new THREE.Vector3();
  const B = new THREE.Vector3();
  const hornOf = (i) => rig.hornPivots[i];
  fixture.expected_rods.forEach((rod, i) => {
    const info = manifest.groups.stewart_rods.rods[i];
    A.fromArray(info.bottom_in_horn);
    hornOf(info.horn_index).localToWorld(A);
    B.fromArray(info.top_in_head_site);
    rig.headGroup.localToWorld(B);
    check(`rod${i + 1} A(下端铰)`, A.toArray(), rod.A, fixture.tolerance?.rod ?? 2e-3);
    check(`rod${i + 1} B(上端铰)`, B.toArray(), rod.B, fixture.tolerance?.rod ?? 2e-3);
  });
  // 杆长物理自洽:|A-B| ≈ 0.085
  const L = new THREE.Vector3().subVectors(B, A).length();
  console.log(`  (杆6 |A-B| = ${L.toFixed(4)}m,期望 ≈0.085)`);
}

function transposeColMajor(rowMajor16) {
  // fixture 存 row-major;three elements 是列主序 → 转置
  const m = rowMajor16;
  return [
    m[0], m[4], m[8], m[12],
    m[1], m[5], m[9], m[13],
    m[2], m[6], m[10], m[14],
    m[3], m[7], m[11], m[15],
  ];
}

console.log(failures === 0 ? '\n全部通过 ✅' : `\n${failures} 项失败 ❌`);
process.exit(failures === 0 ? 0 : 1);
