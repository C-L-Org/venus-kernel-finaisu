#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backlight-guard.py —— 在 techpack 显示驱动的背光更新路径注入「写入者守卫」

背景（venus / 小米11，类原生 ROM）：
  厂商守护进程 vendor.sensor-notifier（SysfsNotifier）会直接写
  /sys/class/backlight/panel0-backlight/brightness，把亮度压到极低再拉回，
  与 framework 的自动亮度抢写，表现为「打开/系统调用自动亮度时瞬间变暗再回升」。
  原厂内核里同样有该守护写节点，但升级后的 mi_disp 显示栈让它变得可见。

做法（内核侧自动判定，不写死机型/ROM）：
  在 sde_backlight_device_update_status() 里读取 current->comm：
    - 来自 surfaceflinger / system_server / composer / systemui / init 视为「受信」，
      记录其最近一次目标亮度；
    - 其余来源（sensor-notifier、mi_thermald、其它 root 脚本等）在
      mode=1（默认，自动）下，只要写入值低于受信值的 50% 就拦截；
      mode=2 严格模式则拦截全部非受信写入；
      mode=0 完全关闭。
  运行期可调：/sys/module/<显示模块名>/parameters/neko_bl_guard
  （builtin 时可用 `ls /sys/module/*/parameters/neko_bl_guard` 找到）。

幂等：已注入则跳过。
用法：python3 scripts/backlight-guard.py <kernel_root>
"""

import os
import sys

MARK = "NEKO_BL_GUARD"

TARGET = os.path.join("techpack", "display", "msm", "sde", "sde_connector.c")

INCLUDES_ANCHOR = "#include <linux/string.h>\n"
INCLUDES_ADD = (
    "#include <linux/string.h>\n"
    "#include <linux/sched.h>\n"          # current->comm
    "#include <linux/jiffies.h>\n"        # time_after / HZ
    "#include <linux/moduleparam.h>\n"   # module_param_named
)

GLOBALS = """/* NEKO_BL_GUARD begin */
/*
 * 背光写入守卫：拦截非受信进程对面板背光的「深度压暗」写入。
 * 0 = 关闭
 * 1 = 自动（默认）：仅拦截低于最近一次受信亮度 50% 的写入
 * 2 = 严格：拦截全部非受信写入
 */
static int neko_bl_guard = 1;
module_param_named(neko_bl_guard, neko_bl_guard, int, 0644);
MODULE_PARM_DESC(neko_bl_guard,
		 "0=off, 1=auto(block untrusted deep drops), 2=strict");

static int neko_bl_trusted_bl = -1;
static int neko_bl_trusted_ui = -1;

static bool neko_bl_is_trusted(const char *comm)
{
	if (!comm || !comm[0])
		return true;
	if (strstr(comm, "surfaceflinger") || strstr(comm, "system_server") ||
			strstr(comm, "composer") || strstr(comm, "systemui") ||
			strstr(comm, "init"))
		return true;
	return false;
}
/* NEKO_BL_GUARD end */

"""

FUNC_ANCHOR = (
    "static int sde_backlight_device_update_status(struct backlight_device *bd)\n"
    "{\n"
)
FUNC_ADD = GLOBALS + FUNC_ANCHOR

HOOK_ANCHOR = (
    "\tif (bl_lvl && bl_lvl < display->panel->bl_config.bl_min_level)\n"
    "\t\tbl_lvl = display->panel->bl_config.bl_min_level;\n"
)
HOOK_ADD = HOOK_ANCHOR + """\t/* NEKO_BL_GUARD begin: 拦截非受信进程的越权压暗写入 */
\tif (neko_bl_guard) {
\t\tconst char *neko_comm = current->comm;

\t\tif (neko_bl_is_trusted(neko_comm)) {
\t\t\tneko_bl_trusted_bl = bl_lvl;
\t\t\tneko_bl_trusted_ui = brightness;
\t\t} else if (neko_bl_trusted_bl > 0 &&
\t\t\t\t(neko_bl_guard == 2 ||
\t\t\t\t bl_lvl * 2 < neko_bl_trusted_bl)) {
\t\t\tstatic unsigned long neko_bl_log_ts;

\t\t\tif (time_after(jiffies, neko_bl_log_ts + 5 * HZ)) {
\t\t\t\tneko_bl_log_ts = jiffies;
\t\t\t\tpr_info("neko_bl_guard: block lvl %d from '%s' (trusted %d, mode %d)\\n",
\t\t\t\t\tbl_lvl, neko_comm, neko_bl_trusted_bl,
\t\t\t\t\tneko_bl_guard);
\t\t\t}
\t\t\tbd->props.brightness = neko_bl_trusted_ui;
\t\t\treturn 0;
\t\t}
\t}
\t/* NEKO_BL_GUARD end */
"""


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: backlight-guard.py <kernel_root>", file=sys.stderr)
        return 2

    path = os.path.join(sys.argv[1], TARGET)
    if not os.path.isfile(path):
        print("::error::target not found: %s" % path, file=sys.stderr)
        return 1

    with open(path, "r", encoding="utf-8", errors="surrogateescape") as f:
        src = f.read()

    if MARK in src:
        print("backlight-guard: already applied, skip")
        return 0

    for name, anchor in (("includes", INCLUDES_ANCHOR),
                         ("function", FUNC_ANCHOR),
                         ("hook", HOOK_ANCHOR)):
        if src.count(anchor) != 1:
            print("::error::anchor '%s' matched %d times" %
                  (name, src.count(anchor)), file=sys.stderr)
            return 1

    out = src.replace(INCLUDES_ANCHOR, INCLUDES_ADD, 1)
    out = out.replace(FUNC_ANCHOR, FUNC_ADD, 1)
    out = out.replace(HOOK_ANCHOR, HOOK_ADD, 1)

    with open(path, "w", encoding="utf-8", errors="surrogateescape") as f:
        f.write(out)

    print("backlight-guard: applied to %s" % TARGET)
    return 0


if __name__ == "__main__":
    sys.exit(main())
