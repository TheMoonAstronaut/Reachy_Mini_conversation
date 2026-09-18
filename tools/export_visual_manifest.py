"""export_visual_manifest — 从 SDK 的 MJCF XML 导出 three.js 可视化清单(V1.2)。

用途(方案 B:three.js 重渲染 Mujoco 画面,前端 three.js 交互视图):
  浏览器端 three.js 需要"哪些 STL 属于哪个刚体组、各自局部位姿是什么"。
  本脚本解析 reachy_mini.xml,把 visual geom 按运动学分组成 6 组,输出
  `static/meshes/manifest.json` + 复制引用到的 STL 到 `static/meshes/`。

分组(与 MuJoCo 运动学一一对应;驱动数据源 = /ws/state 25Hz 推送):
  base          body_foot_3dprint 的直接 visual geom —— 静止(世界系原点)
  body          body_down_3dprint 的直接 visual geom —— 绕局部 z 转 head_joints[0]
                (frame = body 的 pos/quat;three.js: group.quaternion = q0 * Rz(yaw))
  stewart_horns dc15_a01_horn_dummy{,_2.._6} 的直接 visual geom —— 各绕局部 z
                转 head_joints[1..6](frame 在 body 系;注意: horn 的 stewart_link_rod
                子树是被动球关节并联杆,V1 跳过不渲染,头部会留细杆空档)
  head          xl_330 的直接 visual geom,局部位姿已预换算到 head-site 系:
                geom_in_site = inv(S) @ G(S = head site 在 xl_330 系的固定位姿)。
                three.js: headGroup.matrix = head_pose_4x4(并把 z += 0.177,
                因为 SDK get_current_head_pose() 把 site z 减了 0.177,见
                daemon/backend/mujoco/backend.py get_mj_present_head_pose)
  antenna_right dc15_a01_horn_dummy_7 的直接 visual geom。
                frame_in_head_site = inv(S) @ T(p_rel, q_rel) 已预算;
                three.js: pivot.rotation.z = -antennas[0](SDK 对天线取负,
                见 mujoco backend get_present_antenna_joint_positions)
  antenna_left  同上,antennas[1],body dc15_a01_horn_dummy_8

坐标系约定(manifest meta 里也写了):
  - 全部右手系,Z-up(MuJoCo 世界系),位置单位米
  - 四元数统一输出 xyzw 序(three.js Quaternion 构造序;XML 里是 wxyz)
  - 前端组合规则(MuJoCo 约定,已数值验证):
      body_world(θ) = parent_world · T(pos, quat) · R(axis, θ)

运行:
  python tools/export_visual_manifest.py            # 生成 + 打印统计
  python tools/export_visual_manifest.py --check    # 只校验已有产物是否最新

SDK 升级模型后重跑本脚本即可;manifest.json 与 STL 均入 git(static/ 决策 8
的目录用途从"HF Space 占位"扩为"three.js 本地资源",运行时零外网)。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# 分组白名单(body 名;刻意硬编码,XML 结构变了就报错让人来对齐,不静默抓错)
# ---------------------------------------------------------------------------
BASE_BODIES = ["body_foot_3dprint"]
BODY_BODIES = ["body_down_3dprint"]
HORN_BODIES = [  # 顺序 = stewart_1..6 = head_joints[1..6]
    "dc15_a01_horn_dummy",
    "dc15_a01_horn_dummy_2",
    "dc15_a01_horn_dummy_3",
    "dc15_a01_horn_dummy_4",
    "dc15_a01_horn_dummy_5",
    "dc15_a01_horn_dummy_6",
]
HEAD_BODY = "xl_330"
ANTENNA_RIGHT_BODY = "dc15_a01_horn_dummy_7"  # joint: right_antenna
ANTENNA_LEFT_BODY = "dc15_a01_horn_dummy_8"  # joint: left_antenna
HEAD_SITE_NAME = "head"
# Stewart 连杆(被动球关节):V1.6 起用"两端球铰连线法"渲染 ——
# 杆 i 下端铰 = rod_i body 原点在 horn_i 系的 pos(全是 (0.04,0,0.007));
# 上端铰:i=1..5 为 closing_{i}_2 site 在 xl_330 系的 pos(equality connect 的
# 头板侧锚点);i=6 为 xl_330 原点(passive_7 的锚 = xl_330 体原点)。
# 真实运行中 MuJoCo 解算被动关节使 |A-B| 恒 = 杆长 0.085m,浏览器端无需解 FK。
ROD_BODIES = [
    "stewart_link_rod",
    "stewart_link_rod_2",
    "stewart_link_rod_3",
    "stewart_link_rod_4",
    "stewart_link_rod_5",
    "stewart_link_rod_6",
]
ROD_MESH = "stewart_link_rod"

SDK_XML = (
    Path.home()
    / "miniforge3/envs/reachy/lib/python3.12/site-packages/reachy_mini"
    / "descriptions/reachy_mini/mjcf/reachy_mini.xml"
)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = PROJECT_ROOT / "static" / "meshes"


# ---------------------------------------------------------------------------
# 四元数/齐次变换小工具(零 scipy 依赖;MuJoCo quat 是 wxyz,输出统一 xyzw)
# ---------------------------------------------------------------------------
def _quat_wxyz_to_R(q: np.ndarray) -> np.ndarray:
    w, x, y, z = q
    n = w * w + x * x + y * y + z * z
    s = 2.0 / n if n > 0 else 0.0
    xx, yy, zz = x * x * s, y * y * s, z * z * s
    xy, xz, yz = x * y * s, x * z * s, y * z * s
    wx, wy, wz = w * x * s, w * y * s, w * z * s
    return np.array(
        [
            [1 - (yy + zz), xy - wz, xz + wy],
            [xy + wz, 1 - (xx + zz), yz - wx],
            [xz - wy, yz + wx, 1 - (xx + yy)],
        ]
    )


def _R_to_quat_xyzw(R: np.ndarray) -> list[float]:
    """标准 Shepperd 法;返回 xyzw(three.js 序)。"""
    t = np.trace(R)
    if t > 0:
        s = np.sqrt(t + 1.0) * 2
        w, x, y, z = 0.25 * s, (R[2, 1] - R[1, 2]) / s, (R[0, 2] - R[2, 0]) / s, (R[1, 0] - R[0, 1]) / s
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = np.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2
        w, x, y, z = (R[2, 1] - R[1, 2]) / s, 0.25 * s, (R[0, 1] + R[1, 0]) / s, (R[0, 2] + R[2, 0]) / s
    elif R[1, 1] > R[2, 2]:
        s = np.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2
        w, x, y, z = (R[0, 2] - R[2, 0]) / s, (R[0, 1] + R[1, 0]) / s, 0.25 * s, (R[1, 2] + R[2, 1]) / s
    else:
        s = np.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2
        w, x, y, z = (R[1, 0] - R[0, 1]) / s, (R[0, 2] + R[2, 0]) / s, (R[1, 2] + R[2, 1]) / s, 0.25 * s
    return [float(x), float(y), float(z), float(w)]


def _make_T(pos: np.ndarray, quat_wxyz: np.ndarray) -> np.ndarray:
    T = np.eye(4)
    T[:3, :3] = _quat_wxyz_to_R(quat_wxyz)
    T[:3, 3] = pos
    return T


def _T_to_pos_quat(T: np.ndarray) -> tuple[list[float], list[float]]:
    return T[:3, 3].tolist(), _R_to_quat_xyzw(T[:3, :3])


def _parse_vec(text: str | None, default: list[float]) -> np.ndarray:
    if text is None:
        return np.array(default, dtype=float)
    return np.array([float(v) for v in text.split()], dtype=float)


# ---------------------------------------------------------------------------
# XML 解析
# ---------------------------------------------------------------------------
def _load_model(xml_path: Path):
    tree = ET.parse(xml_path)
    root = tree.getroot()
    worldbody = root.find("worldbody")
    assert worldbody is not None, "MJCF 缺 worldbody"
    bodies = {b.get("name"): b for b in worldbody.iter("body") if b.get("name")}
    # material name → rgba
    materials: dict[str, list[float]] = {}
    asset = root.find("asset")
    if asset is not None:
        for m in asset.findall("material"):
            name = m.get("name")
            rgba = m.get("rgba")
            if name and rgba:
                materials[name] = [float(v) for v in rgba.split()]
    # head site 在 xl_330 系的位姿
    head_el = bodies.get(HEAD_BODY)
    assert head_el is not None, f"缺 body {HEAD_BODY}"
    site_el = None
    for s in head_el.iter("site"):
        if s.get("name") == HEAD_SITE_NAME:
            site_el = s
            break
    assert site_el is not None, f"{HEAD_BODY} 下缺 site {HEAD_SITE_NAME}"
    S = _make_T(
        _parse_vec(site_el.get("pos"), [0, 0, 0]),
        _parse_vec(site_el.get("quat"), [1, 0, 0, 0]),
    )
    return bodies, materials, S


def _direct_visual_geoms(body_el: ET.Element) -> list[ET.Element]:
    """只取 body 的**直接** geom 子节点里 class='visual' 的(跳过 collision)。"""
    return [g for g in body_el.findall("geom") if g.get("class") == "visual"]


def _geom_entry(g: ET.Element, materials: dict[str, list[float]]) -> dict:
    return {
        "mesh": g.get("mesh"),
        "pos": _parse_vec(g.get("pos"), [0, 0, 0]).tolist(),
        "quat_xyzw": _R_to_quat_xyzw(
            _quat_wxyz_to_R(_parse_vec(g.get("quat"), [1, 0, 0, 0]))
        ),
        "rgba": materials.get(g.get("material", ""), [0.8, 0.8, 0.8, 1.0]),
    }


def _body_frame(body_el: ET.Element) -> np.ndarray:
    return _make_T(
        _parse_vec(body_el.get("pos"), [0, 0, 0]),
        _parse_vec(body_el.get("quat"), [1, 0, 0, 0]),
    )


def build_manifest(xml_path: Path = SDK_XML) -> tuple[dict, set[str]]:
    """解析 XML → (manifest dict, 引用到的 mesh 文件名集合)。"""
    bodies, materials, S = _load_model(xml_path)
    inv_S = np.linalg.inv(S)
    used_meshes: set[str] = set()

    def collect(body_names: list[str], pre_T: np.ndarray | None = None) -> list[dict]:
        entries = []
        for name in body_names:
            el = bodies.get(name)
            assert el is not None, f"XML 里找不到 body '{name}'(SDK 模型变了?)"
            for g in _direct_visual_geoms(el):
                e = _geom_entry(g, materials)
                if pre_T is not None:
                    G = _make_T(
                        np.array(e["pos"]),
                        np.array([e["quat_xyzw"][3], *e["quat_xyzw"][:3]]),  # xyzw→wxyz
                    )
                    pos, quat = _T_to_pos_quat(pre_T @ G)
                    e["pos"], e["quat_xyzw"] = pos, quat
                used_meshes.add(e["mesh"])
                entries.append(e)
        return entries

    # --- 各组 ---
    base_geoms = collect(BASE_BODIES)

    body_el = bodies[BODY_BODIES[0]]
    body_pos, body_quat = _T_to_pos_quat(_body_frame(body_el))
    body_geoms = collect(BODY_BODIES)

    horns = []
    for i, name in enumerate(HORN_BODIES):
        el = bodies[name]
        f_pos, f_quat = _T_to_pos_quat(_body_frame(el))
        horns.append(
            {
                "joint_index": i + 1,  # head_joints[1..6]
                "frame_in_body": {"pos": f_pos, "quat_xyzw": f_quat},
                "geoms": collect([name]),
            }
        )

    head_geoms = collect([HEAD_BODY], pre_T=inv_S)  # 预换算到 head-site 系

    # --- Stewart 连杆(两端球铰连线法,V1.6)---
    # 下端铰:rod_i body 在 horn_i 系的 pos(ball joint 锚 = body 原点)
    # 上端铰:closing_{i}_2 site 在 xl_330 系的 pos(i=1..5);i=6 为 xl_330 原点
    # 两端统一预换算到各自主参考系(horn 系 / head-site 系),前端只做:
    #   A = hornPivot_i.localToWorld(bottom); B = headGroup.localToWorld(top)
    #   rod 摆 midpoint(A,B),x 轴对齐 B-A(rod mesh 沿 x 居中,±0.0485m)
    head_el = bodies[HEAD_BODY]
    closing_sites: dict[str, np.ndarray] = {}
    for s in head_el.findall("site"):
        nm = s.get("name", "")
        if nm.startswith("closing_") and nm.endswith("_2"):
            closing_sites[nm] = _parse_vec(s.get("pos"), [0, 0, 0])
    rods = []
    for i, rod_body_name in enumerate(ROD_BODIES):
        rod_el = bodies.get(rod_body_name)
        assert rod_el is not None, f"XML 里找不到 body '{rod_body_name}'"
        bottom = _parse_vec(rod_el.get("pos"), [0, 0, 0])  # horn_i 系
        if i < 5:
            key = f"closing_{i+1}_2"
            assert key in closing_sites, f"xl_330 下缺 site {key}"
            top_xl = closing_sites[key]
        else:
            top_xl = np.zeros(3)  # passive_7 锚 = xl_330 体原点
        top_in_head_site = (inv_S @ np.append(top_xl, 1.0))[:3]
        rods.append(
            {
                "horn_index": i,  # 对应 stewart_horns[i] / head_joints[i+1]
                "bottom_in_horn": bottom.tolist(),
                "top_in_head_site": top_in_head_site.tolist(),
            }
        )
    used_meshes.add(ROD_MESH)
    rod_rgba = materials.get("stewart_link_rod_material", [0.8, 0.85, 0.9, 1.0])

    def antenna_group(body_name: str) -> dict:
        el = bodies[body_name]
        F = inv_S @ _body_frame(el)  # antenna joint frame 换算到 head-site 系
        f_pos, f_quat = _T_to_pos_quat(F)
        return {
            "frame_in_head_site": {"pos": f_pos, "quat_xyzw": f_quat},
            "joint_axis": [0, 0, 1],
            "geoms": collect([body_name]),
        }

    site_pos, site_quat = _T_to_pos_quat(S)
    manifest = {
        "meta": {
            "source_xml": str(xml_path),
            "generated_by": "tools/export_visual_manifest.py",
            "frame_convention": {
                "world": "mujoco world, right-handed, Z-up, meters",
                "quat_order": "xyzw",
                "kinematics_rule": "body_world(θ) = parent_world · T(pos,quat) · R(axis,θ)",
                "head_pose_fix": "three.js 侧把 SDK head_pose 的 z 平移 += 0.177 还原 site z",
                "antenna_sign": "antennaPivot.rotation.z = -antennas[i](SDK 已取负)",
                "stewart_rods": "被动球关节连杆:两端球铰连线法渲染(V1.6 起),见 stewart_rods.note",
            },
            "head_site_in_xl330": {"pos": site_pos, "quat_xyzw": site_quat},
        },
        "groups": {
            "base": {"geoms": base_geoms},
            "body": {
                "frame": {"pos": body_pos, "quat_xyzw": body_quat},
                "joint_axis": [0, 0, 1],
                "geoms": body_geoms,
            },
            "stewart_horns": horns,
            "stewart_rods": {
                "mesh": ROD_MESH,
                "rgba": rod_rgba,
                "rods": rods,
                "note": (
                    "被动球关节连杆:两端球铰连线法渲染,不依赖被动关节 qpos。"
                    "rod mesh 沿 x 轴居中(顶点 x∈[-0.0485,0.0485]);前端把 mesh 摆在"
                    " midpoint(A,B) 并 setFromUnitVectors(xAxis, normalize(B-A))。"
                    "A = hornPivot[horn_index].localToWorld(bottom_in_horn),"
                    "B = headGroup.localToWorld(top_in_head_site)。"
                ),
            },
            "head": {"geoms": head_geoms},
            "antenna_right": antenna_group(ANTENNA_RIGHT_BODY),
            "antenna_left": antenna_group(ANTENNA_LEFT_BODY),
        },
    }
    return manifest, used_meshes


def export(xml_path: Path = SDK_XML, out_dir: Path = OUT_DIR) -> dict:
    manifest, used_meshes = build_manifest(xml_path)
    assets_dir = xml_path.parent / "assets"
    out_dir.mkdir(parents=True, exist_ok=True)

    copied, missing = [], []
    for mesh in sorted(used_meshes):
        src = assets_dir / f"{mesh}.stl"
        if src.exists():
            shutil.copy2(src, out_dir / f"{mesh}.stl")
            copied.append(mesh)
        else:
            missing.append(mesh)

    # manifest 里的 mesh 引用 → 本地 URL 路径由前端拼(/static/meshes/{mesh}.stl)
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    stats = {
        "groups": {k: len(v.get("geoms", v if isinstance(v, list) else []))
                   if not isinstance(v, list) else len(v)
                   for k, v in manifest["groups"].items()},
        "meshes_copied": len(copied),
        "meshes_missing": missing,
        "out_dir": str(out_dir),
    }
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description="导出 three.js 可视化 manifest + STL")
    ap.add_argument("--check", action="store_true", help="只校验产物是否存在")
    ap.add_argument("--xml", type=Path, default=SDK_XML, help="MJCF XML 路径")
    args = ap.parse_args()

    if args.check:
        ok = (OUT_DIR / "manifest.json").exists() and any(OUT_DIR.glob("*.stl"))
        print("check:", "OK" if ok else "MISSING — 请运行 export")
        return 0 if ok else 1

    stats = export(args.xml)
    print("[export] 分组 geom 数:", stats["groups"])
    print(f"[export] STL 复制 {stats['meshes_copied']} 个 → {stats['out_dir']}")
    if stats["meshes_missing"]:
        print(f"[export] ⚠️ 缺 STL: {stats['meshes_missing']}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
