#!/usr/bin/env python3
"""让 shell（uid 2000）的 su 强制使用 default root profile。

FinaSU release 构建 allow_shell=false（仅 CONFIG_KSU_DEBUG 时 true），
ksu_get_root_profile() 对 shell 会走 allowlist 条目 profile；当前条目
profile 非 root，导致普通 adb shell 的 `su -c id` 返回 uid=2000。

改成 shell 总是 use_default -> default_root_profile(uid=0) -> escape 到 root。

实机证据（venus-test-55，SUSFS Inline 后端）：
- app（Scene）su -c id -> uid=0(root)
- adb shell（uid 2000）su -c id -> uid=2000(shell)
- dmesg: "sucompat: direct exec su -> ksud (fd)" + "session armed (fd=4)" 均正常
"""
import sys

path = sys.argv[1] if len(sys.argv) > 1 else 'kernel/KernelSU/kernel/policy/allowlist.c'
s = open(path, encoding='utf-8').read()

if 'ksu54_shell_profile_fix' in s:
    print('already patched, skip')
    sys.exit(0)

old = """    if (unlikely(allow_shell && uid == SHELL_UID)) {
        goto use_default;
    }"""
new = """    /* ksu54_shell_profile_fix: shell always uses the default root profile.
     * allow_shell 默认 false（release 构建），否则 shell 会命中 allowlist
     * 条目 profile，导致普通 adb shell 的 su 拿不到 root（uid 保持 2000）。 */
    if (uid == SHELL_UID) {
        goto use_default;
    }"""
assert s.count(old) == 1, 'shell profile anchor not found'
s = s.replace(old, new)
open(path, 'w', encoding='utf-8').write(s)
print('patched shell profile -> default root profile')
