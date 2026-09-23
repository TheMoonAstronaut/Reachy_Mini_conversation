"""three.js 可视化 manifest 的守护测试(V1.2)。

保护的事:
  1. tools/export_visual_manifest.py 能对 SDK 的 MJCF XML 跑通,分组齐全
     (base / body / stewart_horns×6 / head / antenna_right / antenna_left)。
  2. **运动学正确性**(核心):manifest 的分组 frame + 前端组合规则
     `body_world(θ) = parent·T(pos,quat)·R(axis,θ)` 在随机关节角下,
     与 MuJoCo 真实 FK(xpos/xmat/site_xpos)逐组一致(误差 < 1e-9)。
     这条挂了说明 SDK 模型结构变了或换算规则被破坏,前端必然渲染错位。
  3. head 组 geom 已预换算到 head-site 系(inv(S)@G),验证端到端:
     site_world @ local == MuJoCo geom 世界位姿。
  4. 7861 静态伺服:static_dir 挂载后 /static/meshes/manifest.json 可 GET。

需要 mujoco + scipy(SDK 依赖,reachy 环境必有);SDK XML 缺失时 skip。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

mujoco = pytest.importorskip("mujoco")  # noqa: E402
from scipy.spatial.transform import Rotation as R  # noqa: E402

from tools.export_visual_manifest import SDK_XML, build_manifest, export  # noqa: E402

pytestmark = pytest.mark.skipif(not SDK_XML.exists(), reason="SDK MJCF XML 不存在")


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _T(pos, quat_xyzw):
    T = np.eye(4)
    T[:3, :3] = R.from_quat(quat_xyzw).as_matrix()
    T[:3, 3] = pos
    return T


def _rz(theta):
    return _T([0, 0, 0], [0, 0, np.sin(theta / 2), np.cos(theta / 2)])


def _body_T(data, model, name):
    bid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, name)
    T = np.eye(4)
    T[:3, :3] = data.xmat[bid].reshape(3, 3)
    T[:3, 3] = data.xpos[bid]
    return T


@pytest.fixture(scope="module")
def model_and_random_state():
    """加载空场景模型,设一组确定性的非零关节角,返回 (model, data, qpos_dict)。"""
    xml = SDK_XML.parent / "scenes" / "empty.xml"
    m = mujoco.MjModel.from_xml_path(str(xml))
    d = mujoco.MjData(m)
    # qpos 地址(与 XML 声明序一致;yaw=0, stewart_i 穿插被动关节, 天线在最后)
    q = {"yaw": 0.37, "stewart": [0.11, -0.23, 0.31, -0.17, 0.27, -0.13], "ant": [0.42, -0.35]}
    d.qpos[0] = q["yaw"]
    for addr, v in zip([1, 6, 11, 16, 21, 26], q["stewart"], strict=False):
        d.qpos[addr] = v
    d.qpos[35], d.qpos[36] = q["ant"]
    mujoco.mj_forward(m, d)
    return m, d, q


# ---------------------------------------------------------------------------
# 1. 生成与分组完整性
# ---------------------------------------------------------------------------
def test_manifest_groups_complete():
    manifest, used = build_manifest(SDK_XML)
    groups = manifest["groups"]
    assert set(groups) == {
        "base", "body", "stewart_horns", "stewart_rods",
        "head", "antenna_right", "antenna_left",
    }
    assert len(groups["base"]["geoms"]) >= 1
    assert len(groups["body"]["geoms"]) >= 20  # 身体细碎零件很多
    assert len(groups["stewart_horns"]) == 6
    assert [h["joint_index"] for h in groups["stewart_horns"]] == [1, 2, 3, 4, 5, 6]
    assert len(groups["head"]["geoms"]) >= 10
    assert len(groups["antenna_right"]["geoms"]) >= 1
    assert len(groups["antenna_left"]["geoms"]) >= 1
    # 连杆组(V1.6):6 根,horn_index 0..5,两端点齐全
    rods = groups["stewart_rods"]["rods"]
    assert len(rods) == 6
    assert [r["horn_index"] for r in rods] == [0, 1, 2, 3, 4, 5]
    for r in rods:
        assert len(r["bottom_in_horn"]) == 3
        assert len(r["top_in_head_site"]) == 3
    assert groups["stewart_rods"]["mesh"] == "stewart_link_rod"
    # 所有 geom 都引用了 mesh 且文件存在
    assert used, "manifest 没有引用任何 mesh"
    assets = SDK_XML.parent / "assets"
    for mesh in used:
        assert (assets / f"{mesh}.stl").exists(), f"缺 mesh 文件: {mesh}.stl"
    # quat 全部归一化
    def _check_quats(entries):
        for e in entries:
            n = np.linalg.norm(e["quat_xyzw"])
            assert n == pytest.approx(1.0, abs=1e-6)
    for key in ("base", "body", "head", "antenna_right", "antenna_left"):
        _check_quats(groups[key]["geoms"])
    for h in groups["stewart_horns"]:
        _check_quats(h["geoms"])


# ---------------------------------------------------------------------------
# 2. 运动学正确性(随机关节角 vs MuJoCo FK)
# ---------------------------------------------------------------------------
def test_body_group_kinematics(model_and_random_state):
    m, d, q = model_and_random_state
    manifest, _ = build_manifest(SDK_XML)
    fr = manifest["groups"]["body"]["frame"]
    pred = _T(fr["pos"], fr["quat_xyzw"]) @ _rz(q["yaw"])
    gt = _body_T(d, m, "body_down_3dprint")
    assert np.abs(pred - gt).max() < 1e-9


def test_head_site_kinematics(model_and_random_state):
    """head 组原点(head site)的世界位姿 == MuJoCo site_xpos/xmat。"""
    m, d, q = model_and_random_state
    manifest, _ = build_manifest(SDK_XML)
    sid = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_SITE, "head")
    S_gt = np.eye(4)
    S_gt[:3, :3] = d.site_xmat[sid].reshape(3, 3)
    S_gt[:3, 3] = d.site_xpos[sid]
    # manifest 规则:S_world = X_world @ S_local
    X = _body_T(d, m, "xl_330")
    sinfo = manifest["meta"]["head_site_in_xl330"]
    pred = X @ _T(sinfo["pos"], sinfo["quat_xyzw"])
    assert np.abs(pred - S_gt).max() < 1e-9


def test_head_geom_locals_end_to_end(model_and_random_state):
    """顶点级端到端证明:raw STL 顶点 × manifest 链 == MuJoCo 世界渲染。

    背景坑(已踩过):MuJoCo 编译期会把 mesh 顶点居中+主轴对齐
    (mjModel.mesh_pos/mesh_quat 非零时),并反向补偿 geom_pos/quat 保持世界
    外观不变 —— 所以 geom_xpos 是"处理后 mesh 系"位姿,**不能**作为
    raw STL 摆放的 ground truth。正确比法:
      W_mine = body_chain(manifest) @ raw_stl_vertices
      W_mj   = geom_xmat @ mesh_vert[mesh_vertadr: +vertnum] + geom_xpos
    两点集豪斯多夫距离必须 ~0(顶点顺序会被编译器重排,只能比集合)。
    另注意:mesh_vertadr 单位是**顶点行数**(mesh_vert 是 (N,3) 二维)。
    """
    import struct

    m, d, q = model_and_random_state

    def read_stl_unique(path: Path) -> np.ndarray:
        with open(path, "rb") as f:
            f.read(80)
            count = struct.unpack("<I", f.read(4))[0]
            verts = np.zeros((count * 3, 3))
            for t in range(count):
                f.read(12)
                for k in range(3):
                    verts[t * 3 + k] = struct.unpack("<fff", f.read(12))
                f.read(2)
        return np.unique(np.round(verts, 9), axis=0)

    def hausdorff(A: np.ndarray, B: np.ndarray) -> float:
        def md(P, Q):
            out = np.zeros(len(P))
            for i in range(0, len(P), 512):
                d2 = ((P[i : i + 512, None, :] - Q[None, :, :]) ** 2).sum(-1)
                out[i : i + 512] = np.sqrt(d2.min(1))
            return float(out.max())

        return max(md(A, B), md(B, A))

    # manifest 的 head 组原点 = head site 世界位姿(SDK head_pose z+0.177 还原)
    sid = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_SITE, "head")
    S_world = np.eye(4)
    S_world[:3, :3] = d.site_xmat[sid].reshape(3, 3)
    S_world[:3, 3] = d.site_xpos[sid]

    # 抽一个有代表性的 mesh:m12_fisheye_lens_1_8mm —— 它的 mesh_pos/mesh_quat
    # 非零(编译器做了居中+旋转),最容易暴露"错用 geom_xpos 当真值"的回归
    manifest, _ = build_manifest(SDK_XML)
    entries = [e for e in manifest["groups"]["head"]["geoms"] if e["mesh"] == "m12_fisheye_lens_1_8mm"]
    assert entries, "head 组缺 m12_fisheye_lens_1_8mm"

    mid = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_MESH, "m12_fisheye_lens_1_8mm")
    v0, vn = m.mesh_vertadr[mid], m.mesh_vertnum[mid]
    v_proc = m.mesh_vert[v0 : v0 + vn]  # 行索引切片
    v_raw = read_stl_unique(SDK_XML.parent / "assets" / "m12_fisheye_lens_1_8mm.stl")
    xl_id = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, "xl_330")

    # manifest 链:S_world @ local
    assert len(entries) == 2  # 该 mesh 在头部用了两次
    gis = [
        i for i in range(m.ngeom)
        if m.geom_bodyid[i] == xl_id and m.geom_group[i] == 2 and m.geom_dataid[i] == mid
    ]
    assert len(gis) == 2

    for e, gi in zip(entries, gis, strict=False):
        W_mine = (S_world @ _T(e["pos"], e["quat_xyzw"]) @
                  np.hstack([v_raw, np.ones((len(v_raw), 1))]).T).T[:, :3]
        W_mj = (d.geom_xmat[gi].reshape(3, 3) @ v_proc.T).T + d.geom_xpos[gi]
        dist = hausdorff(W_mine, W_mj)
        assert dist < 1e-6, f"顶点级不一致(豪斯多夫 {dist} m)— manifest 运动学链被破坏"


def test_antenna_kinematics(model_and_random_state):
    m, d, q = model_and_random_state
    manifest, _ = build_manifest(SDK_XML)
    sid = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_SITE, "head")
    S_world = np.eye(4)
    S_world[:3, :3] = d.site_xmat[sid].reshape(3, 3)
    S_world[:3, 3] = d.site_xpos[sid]
    for group_key, body_name, qi in [
        ("antenna_right", "dc15_a01_horn_dummy_7", 0),
        ("antenna_left", "dc15_a01_horn_dummy_8", 1),
    ]:
        finfo = manifest["groups"][group_key]["frame_in_head_site"]
        pred = S_world @ _T(finfo["pos"], finfo["quat_xyzw"]) @ _rz(q["ant"][qi])
        gt = _body_T(d, m, body_name)
        assert np.abs(pred - gt).max() < 1e-9, f"{group_key} 运动学不一致"


def test_stewart_horn_kinematics(model_and_random_state):
    m, d, q = model_and_random_state
    manifest, _ = build_manifest(SDK_XML)
    fr = manifest["groups"]["body"]["frame"]
    body_world = _T(fr["pos"], fr["quat_xyzw"]) @ _rz(q["yaw"])
    horn_bodies = [
        "dc15_a01_horn_dummy", "dc15_a01_horn_dummy_2", "dc15_a01_horn_dummy_3",
        "dc15_a01_horn_dummy_4", "dc15_a01_horn_dummy_5", "dc15_a01_horn_dummy_6",
    ]
    for horn, body_name, theta in zip(manifest["groups"]["stewart_horns"], horn_bodies, q["stewart"], strict=False):
        f = horn["frame_in_body"]
        pred = body_world @ _T(f["pos"], f["quat_xyzw"]) @ _rz(theta)
        gt = _body_T(d, m, body_name)
        assert np.abs(pred - gt).max() < 1e-9, f"{body_name} 运动学不一致"


def test_stewart_rod_endpoints(model_and_random_state):
    """连杆两端球铰位置(manifest 公式)vs MuJoCo 真值(mj_step 收敛后)。

    方法:锁主动关节 ctrl,mj_step 200 步让 equality 软约束收敛,然后:
      - A(下端铰)= body_world @ horn_frame @ Rz(θ) @ bottom_in_horn
        vs MuJoCo rod body xpos
      - B(上端铰)= site_world @ top_in_head_site
        vs MuJoCo closing_i_1 site xpos(i=1..5)/ xl_330 体原点(i=6)
      - |A-B| ≈ 杆长 0.085m(物理自洽)
    容差 2mm:equality 是软约束(solref=0.002),收敛后主动关节有亚毫米回弹。
    """
    m, d, q = model_and_random_state
    # 锁主动关节,跑 200 步让被动关节/约束收敛
    d.ctrl[:] = np.concatenate([[q["yaw"]], q["stewart"], [0.0, 0.0]])
    for _ in range(200):
        mujoco.mj_step(m, d)

    manifest, _ = build_manifest(SDK_XML)
    fr = manifest["groups"]["body"]["frame"]
    body_world = _T(fr["pos"], fr["quat_xyzw"]) @ _rz(q["yaw"])
    sid = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_SITE, "head")
    S_world = np.eye(4)
    S_world[:3, :3] = d.site_xmat[sid].reshape(3, 3)
    S_world[:3, 3] = d.site_xpos[sid]

    for i, rod in enumerate(manifest["groups"]["stewart_rods"]["rods"]):
        horn = manifest["groups"]["stewart_horns"][i]
        horn_world = (
            body_world
            @ _T(horn["frame_in_body"]["pos"], horn["frame_in_body"]["quat_xyzw"])
            @ _rz(q["stewart"][i])
        )
        A = (horn_world @ np.append(rod["bottom_in_horn"], 1.0))[:3]
        B = (S_world @ np.append(rod["top_in_head_site"], 1.0))[:3]

        rod_body = "stewart_link_rod" if i == 0 else f"stewart_link_rod_{i+1}"
        A_gt = d.xpos[mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, rod_body)]
        if i < 5:
            B_gt = d.site_xpos[mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_SITE, f"closing_{i+1}_1")]
        else:
            B_gt = d.xpos[mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, "xl_330")]

        assert np.linalg.norm(A - A_gt) < 2e-3, f"杆{i+1} 下端铰偏差 {np.linalg.norm(A-A_gt)*1000:.2f}mm"
        assert np.linalg.norm(B - B_gt) < 2e-3, f"杆{i+1} 上端铰偏差 {np.linalg.norm(B-B_gt)*1000:.2f}mm"
        assert abs(np.linalg.norm(B - A) - 0.085) < 2e-3, f"杆{i+1} 长度 {np.linalg.norm(B-A):.4f}m != 0.085m"


# ---------------------------------------------------------------------------
# 3. 静态伺服(7861)
# ---------------------------------------------------------------------------
def test_static_dir_mounted_and_served():
    with tempfile.TemporaryDirectory() as td:
        meshes = Path(td) / "meshes"
        meshes.mkdir()
        (meshes / "manifest.json").write_text("{}", encoding="utf-8")
        (meshes / "dummy.stl").write_bytes(b"solid dummy\nendsolid dummy\n")

        from starlette.testclient import TestClient

        from reachymini_conversation.utils.camera_stream import create_camera_stream_app

        app = create_camera_stream_app(sim_mini=None, static_dir=td)
        client = TestClient(app)
        r = client.get("/static/meshes/manifest.json")
        assert r.status_code == 200
        r2 = client.get("/static/meshes/dummy.stl")
        assert r2.status_code == 200
        assert r2.content.startswith(b"solid")


def test_static_dir_missing_is_tolerated():
    """static_dir 不存在时不挂载、不 crash(测试环境可能没跑 export)。"""
    from reachymini_conversation.utils.camera_stream import create_camera_stream_app

    app = create_camera_stream_app(sim_mini=None, static_dir="/nonexistent/path/xyz")
    assert app is not None


# ---------------------------------------------------------------------------
# 4. 真实产物(如果已 export)与 export 冒烟
# ---------------------------------------------------------------------------
def test_export_smoke(tmp_path):
    stats = export(SDK_XML, tmp_path)
    assert stats["meshes_copied"] > 30
    assert not stats["meshes_missing"]
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert "groups" in manifest and "meta" in manifest


@pytest.mark.skipif(
    not (REPO_ROOT / "static" / "meshes" / "manifest.json").exists(),
    reason="尚未运行 tools/export_visual_manifest.py",
)
def test_committed_meshes_are_fresh():
    """仓库里的 static/meshes/manifest.json 必须能由当前 SDK XML 重新生成且一致。"""
    on_disk = json.loads((REPO_ROOT / "static" / "meshes" / "manifest.json").read_text("utf-8"))
    regenerated, _ = build_manifest(SDK_XML)
    assert on_disk["groups"].keys() == regenerated["groups"].keys()
    for key in on_disk["groups"]:
        assert json.dumps(on_disk["groups"][key], sort_keys=True) == json.dumps(
            regenerated["groups"][key], sort_keys=True
        ), f"组 {key} 与 SDK XML 不一致 — 请重跑 tools/export_visual_manifest.py"
