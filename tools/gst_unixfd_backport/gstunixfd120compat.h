/* gstunixfd120compat.h — 把 gst-plugins-bad 1.24 的 unixfd 插件移植到 GStreamer 1.20 的兼容层
 *
 * 背景:Ubuntu 22.04 自带 GStreamer 1.20,而 unixfd 插件 1.24 才加入官方源码树,
 *       1.20 的 -bad 包不含它。reachy_mini SDK 的本地 IPC 视频链路
 *       (unixfdsink/unixfdsrc)依赖此插件。
 *
 * 移植内容:
 *  - gst/unixfd/*.c(1.24,-bad)+ gst-libs/gst/allocators/gstshmallocator.c(1.24,-base)
 *  - 本头文件补齐 1.20 缺失的符号:
 *      GST_LICENSE                  官方构建由 config.h 提供,这里直接定义 "LGPL"
 *      HAVE_MEMFD_CREATE/HAVE_MMAP  gstshmallocator.c 的配置宏(Linux 均有)
 *      memfd_create() 声明          jammy 的 glibc 2.35 有实现但 libc6-dev 未附
 *                                   <sys/memfd.h>,手动 extern 声明
 *      GST_ALLOCATOR_FLAG_NO_COPY   1.24 新增标志位,按 1.24 的定义补齐
 *      gst_meta_serialize_simple() / gst_meta_deserialize()
 *                                   1.24 的 meta 序列化 API。本机 IPC 链路上游是
 *                                   videoconvert 输出的紧凑 BGR 帧,无需传递 meta,
 *                                   直接跳过(sink 返回 FALSE → n_meta=0 → src 侧
 *                                   永远不会调用 deserialize)
 *
 * 用法:gcc -include gstunixfd120compat.h ...
 */
#ifndef GSTUNIXFD120COMPAT_H
#define GSTUNIXFD120COMPAT_H

#include <gst/gst.h>
#include <gst/allocators/allocators.h>

/* 1.24 中这两个函数由已安装的 allocators 头文件声明;1.20 没有,
 * 直接包含随插件一起编译的 1.24 版头文件 */
#include "gstshmallocator.h"

#if !GST_CHECK_VERSION(1, 24, 0) /* 仅老版本 GStreamer 需要本兼容层 */

#ifndef GST_LICENSE
#define GST_LICENSE "LGPL"
#endif

/* gstshmallocator.c 的功能开关(官方由 meson config.h 提供) */
#ifndef HAVE_MEMFD_CREATE
#define HAVE_MEMFD_CREATE 1
#endif
#ifndef HAVE_MMAP
#define HAVE_MMAP 1
#endif

/* glibc >= 2.27 提供 memfd_create;但 jammy 的 libc6-dev 没有 <sys/memfd.h>,
 * 这里手动声明(链接时解析 glibc 内的符号) */
#include <sys/mman.h>
#ifndef MFD_CLOEXEC
#define MFD_CLOEXEC 0x0001U
#define MFD_ALLOW_SEALING 0x0002U
#endif
extern int memfd_create (const char *__name, unsigned int __flags);

/* 1.24 新增的 allocator 标志(语义:分配的内存不可复制),与 1.24 定义一致 */
#ifndef GST_ALLOCATOR_FLAG_NO_COPY
#define GST_ALLOCATOR_FLAG_NO_COPY (GST_OBJECT_FLAG_LAST << 1)
#endif

G_GNUC_UNUSED static gboolean
gst_meta_serialize_simple (const GstMeta * meta, GByteArray * payload)
{
  (void) meta;
  (void) payload;
  return FALSE; /* 不序列化任何 meta */
}

G_GNUC_UNUSED static gboolean
gst_meta_deserialize (GstBuffer * buffer, const guint8 * data, gsize size,
    guint32 * consumed)
{
  (void) buffer;
  (void) data;
  (void) size;
  if (consumed)
    *consumed = 0;
  return FALSE;
}

#endif /* !GST_CHECK_VERSION(1, 24, 0) */
#endif /* GSTUNIXFD120COMPAT_H */
