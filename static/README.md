# static/ — 静态资源目录

> 原用途:HF Space 静态资源接口占位(预留,当前不发布)。
> **现扩展(2025-09-14,任务 V1)**:同时承载 three.js 3D 视图的本地资源,
> 由 7861 端口的 FastAPI 通过 `StaticFiles` 挂载到 `/static/` 伺服,
> 运行时零外网依赖(CDN 白名单不变)。

## meshes/ — three.js 3D 视图资源(V1.2)

由 `tools/export_visual_manifest.py` 从 SDK 的 MJCF XML 生成:

- `manifest.json` — 6 个运动学分组(base/body/stewart_horns/head/antenna_×2)
  的 geom→STL 映射 + 局部位姿(pos + quat_xyzw)+ 材质 rgba
- `*.stl` — visual mesh(从 SDK `descriptions/reachy_mini/mjcf/assets/` 复制,
  跳过 collision/)

**SDK 升级后必须重跑** `python tools/export_visual_manifest.py`,
否则模型与渲染不一致(有 `tests/test_visual_manifest.py` 守护)。

## js/ — three.js 前端(V1.3)

- `vendor/` — three.js / OrbitControls / STLLoader 的本地化副本
  (一次性开发期下载,vendored 入 git;运行时不访问 CDN,网络白名单不变)
- `three_viewer.js` — 3D 视图主模块(OrbitControls 交互 + /ws/state 订阅)

## 约定

- 放入的文件必须可开源发布(LICENSE 兼容:three.js 为 MIT,Reachy STL 随 SDK Apache 2.0),
  且**不得包含 API Key / 凭据**。
- 决策 8 语义不变(本期仍不发布 HF Space);目录用途扩展已同步本文件。
