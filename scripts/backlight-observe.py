#!/usr/bin/env python3
"""Add an opt-in, behavior-preserving trace at the panel0 brightness sysfs ingress."""
import argparse
from pathlib import Path

REL = 'drivers/video/backlight/backlight.c'
MARK = 'NEKO_BL_OBSERVE'
ANCHOR = 'static ssize_t brightness_store(struct device *dev,\n'
SET = '\trc = backlight_device_set_brightness(bd, brightness);\n'
DECL = '''/* NEKO_BL_OBSERVE: diagnostic-only; never alters a brightness request */
static bool neko_bl_observe;
module_param_named(neko_bl_observe, neko_bl_observe, bool, 0600);
MODULE_PARM_DESC(neko_bl_observe,
\t\t "Trace panel0 sysfs brightness requests; does not filter writes");

'''
LOG = '''\tif (unlikely(READ_ONCE(neko_bl_observe)) &&
\t    !strcmp(dev_name(dev), "panel0-backlight")) {
\t\tchar comm[TASK_COMM_LEN];

\t\tget_task_comm(comm, current);
\t\tdev_info_ratelimited(dev,
\t\t\t"brightness request=%lu pid=%d comm=%s\\n",
\t\t\tbrightness, task_pid_nr(current), comm);
\t}
'''


def transform(src):
    if MARK in src:
        if src.count(DECL) != 1 or src.count(LOG + SET) != 1:
            raise ValueError('existing observation patch is incomplete or differs')
        return src
    if src.count(ANCHOR) != 1:
        raise ValueError('unexpected brightness_store signature')
    start = src.index(ANCHOR)
    end = src.index('static DEVICE_ATTR_RW(brightness);', start)
    body = src[start:end]
    if body.count(SET) != 1:
        raise ValueError('unexpected brightness_store implementation')
    out = src[:start] + DECL + body.replace(SET, LOG + SET) + src[end:]
    for header in ('linux/sched.h', 'linux/string.h'):
        directive = f'#include <{header}>\n'
        if directive not in out:
            lines = out.splitlines(keepends=True)
            last = max(i for i, line in enumerate(lines) if line.startswith('#include '))
            lines.insert(last + 1, directive)
            out = ''.join(lines)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kernel_root', type=Path)
    args = parser.parse_args()
    path = args.kernel_root / REL
    try:
        old = path.read_bytes().decode('utf-8', 'surrogateescape')
        new = transform(old)
        if old != new:
            path.write_bytes(new.encode('utf-8', 'surrogateescape'))
    except (OSError, ValueError) as exc:
        parser.exit(1, f'{path}: {exc}\n')


if __name__ == '__main__':
    main()