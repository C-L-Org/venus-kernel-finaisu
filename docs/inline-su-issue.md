# Inline 后端 su 失效（uid 2000）分析与修复方案

> 状态：**待验证**（Inline 已临时回退 Tracepoint，见 related PR/commit）
> 触发版本：C-L-Org venus-kernel-finaisu run55+/内核 `5.4.302-Neko-CL-20261115-54950`

## 现象

- 切换 SUSFS Inline 后端（`CONFIG_KSU_HOOK_SUSFS_INLINE=y`）后，`adb shell` 内 `su -c id` 仍返回 `uid=2000(shell)`
- 同一设备用 Tracepoint 后端（ak3-susfs-7 / run43）时 `su` 正常
- 内核能正常开机、SUSFS 其他功能正常，仅 su 失效

## 已确认事实（dmesg）

```
[36544.984992] sucompat: direct exec su -> ksud (fd)      ← Inline sucompat 执行了
[36544.985054] ksu54: sucompat session armed (fd=4)       ← sulog 会话建立
[36544.987615] KernelSU: ksu fd installed: 3 for pid 4535 ← ksud 进程启动
```

结论：**不是 umounted 跳过、不是 basename 匹配、不是 ksud 缺失** —— sucompat 走到了、ksud 启动了，但最终 uid 仍 2000。

## Profile 配置（设备实测）

Shell（UID 2000）已授权超级用户；App Profile = 自定义：

| 项 | 值 |
|---|---|
| UID / GID | 0 / 0 |
| 组 | root |
| SELinux | `u:r:ksu:s0` |
| **标签** | **NO_NEW_PRIVS** |

## 分析（候选根因）

1. **NO_NEW_PRIVS → TIF_KSU_DISABLE_ESCAPE_WITH_ROOT**：`escape_with_root_profile()` 结尾会
   `set_thread_flag(TIF_KSU_DISABLE_ESCAPE_WITH_ROOT)`，该 TIF 在 Inline 的 fd-exec 路径上可能影响后续 escape/exec 的 creds 处理（Tracepoint 走 syscall 入口，不受影响）。
2. **fd 执行语义差异**：Inline 修复用 `filp_open + fd_install + AT_EMPTY_PATH`，与 Tracepoint 的路径 exec 在 bprm/creds 时机上不同；escape（commit_creds + setup_selinux）发生在 do_execveat_common 内部，可能被 `__do_execve_file` 的 prep 覆盖/重置。
3. **sulog session**：`sucompat session armed` 后 ksud 的 su 会话处理路径需要确认（是否依赖 creds 已为 root）。

## 修复方向（按验证成本排序）

| 方案 | 改动 | 说明 |
|---|---|---|
| **A. 默认 profile（去 NO_NEW_PRIVS）** | 管理器配置 | 最小改动，先验证是否 profile 标签导致 |
| **B. fs/exec.c：sucompat 移到 umounted 检查前** | 内核 | 排除 umounted 跳过（dmesg 已证明未跳过，优先级低） |
| **C. Inline sucompat 的 escape/exec 顺序对齐 Tracepoint** | 内核/FinaSU | 把 escape 放在 fd-exec 的 bprm prep 之后（或复用 Tracepoint 的 sucompat 函数） |
| **D. 检查 `__do_execve_file` 是否重置 creds** | 内核 | 确认 fd-exec 后 ksud 的实际 uid |

## 验证方法（下一次 Inline 尝试时）

```sh
# 管理器中把 Shell 的 App Profile 改为「默认」
su -c id; echo exit=$?
# 期望：uid=0 exit=0

# 若失败，抓内核日志
dmesg | grep -iE "sucompat|escape|TIF|ksu fd installed"
```

## 相关代码位置（FinaSU v1.6.1）

- `kernel/policy/app_profile.c`：`escape_with_root_profile()`（commit_creds / setup_selinux / NO_NEW_PRIVS 设 TIF）
- `kernel/extension/susfs.c`：`inline_handle_execveat`（Inline sucompat，fd-exec）
- 内核 `fs/exec.c`：`do_execveat_common` 的直调点（1c9bda194）
