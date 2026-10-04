#!/usr/bin/env python3
"""Local, read-only GTK dashboard. No charger, backlight or service controls."""
import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path
import signal
import time


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ''


def number(path, scale=1, low=0, high=math.inf):
    try:
        value = float(read(path)) / scale
        return value if math.isfinite(value) and low <= value <= high else None
    except ValueError:
        return None


def disk(path, require_mount=False):
    if require_mount and not os.path.ismount(path):
        return None
    try:
        stat = os.statvfs(path)
        return {'total': stat.f_blocks * stat.f_frsize,
                'available': stat.f_bavail * stat.f_frsize,
                'used': (stat.f_blocks - stat.f_bfree) * stat.f_frsize}
    except OSError:
        return None


def cpu_ticks():
    try:
        # guest/guest_nice already belong to user/nice; don't double count.
        values = [int(x) for x in read('/proc/stat').splitlines()[0].split()[1:9]]
        return sum(values), values[3] + values[4]
    except (ValueError, IndexError):
        return None


def cpu_percent(before, after):
    if before is None or after is None:
        return None
    total, idle = after[0] - before[0], after[1] - before[1]
    if total <= 0 or not 0 <= idle <= total:
        return None
    return 100 * (total - idle) / total


def rates(before, after, elapsed):
    if before is None or after is None or elapsed <= 0:
        return None
    delta = [b - a for a, b in zip(before, after)]
    return [x / elapsed for x in delta] if all(x >= 0 for x in delta) else None


def health_snapshot(path=Path('/run/kebab-health/status.json')):
    try:
        age = time.time() - path.stat().st_mtime
        state = json.loads(path.read_text())
        if not isinstance(state, dict) or not 0 <= age <= 90:
            return None, age
        control = state.get('charge_control')
        if not isinstance(control, dict) or any(type(control.get(key)) is not bool for key in
                                               ('policy_active', 'inhibit_readback_verified')):
            return None, age
        if control['policy_active']:
            stop, resume = control.get('stop_percent'), control.get('resume_percent')
            if type(stop) is not int or type(resume) is not int or not 0 <= resume < stop <= 100:
                return None, age
        return state, age
    except (OSError, ValueError):
        return None, None


class Sampler:
    def __init__(self):
        self.cpu = None
        self.net = None
        self.stamp = None

    def sample(self):
        now = time.monotonic()
        ticks = cpu_ticks()
        usage = cpu_percent(self.cpu, ticks)
        self.cpu = ticks
        mem = {}
        for line in read('/proc/meminfo').splitlines():
            try:
                key, value = line.split(':', 1)
                mem[key] = int(value.split()[0]) * 1024
            except (ValueError, IndexError):
                pass
        temperatures = []
        for zone in Path('/sys/class/thermal').glob('thermal_zone*'):
            if read(zone / 'type').startswith(('cpu', 'cluster')):
                value = number(zone / 'temp', 1000, -50, 150)
                if value is not None:
                    temperatures.append(value)
        battery = {}
        for supply in sorted(Path('/sys/class/power_supply').glob('*')):
            if read(supply / 'type') == 'Battery':
                battery = {'capacity': number(supply / 'capacity', high=100),
                           'temp': number(supply / 'temp', 10, -50, 150),
                           'status': read(supply / 'status')}
                break
        net_path = Path('/sys/class/net/wlan0')
        net = [number(net_path / 'statistics' / name) for name in ('rx_bytes', 'tx_bytes')]
        if any(x is None for x in net):
            net = None
        speed = rates(self.net, net, now - self.stamp) if self.stamp else None
        self.net, self.stamp = net, now
        health, age = health_snapshot()
        try:
            uptime = float(read('/proc/uptime').split()[0])
        except (ValueError, IndexError):
            uptime = None
        return {'sampled_at': dt.datetime.now().astimezone().isoformat(), 'cpu_percent': usage,
                'cpu_temp': max(temperatures) if temperatures else None,
                'memory_total': mem.get('MemTotal'), 'memory_available': mem.get('MemAvailable'),
                'root': disk('/'), 'data': disk('/srv', True), 'battery': battery,
                'wifi_up': read(net_path / 'operstate') == 'up', 'net_rates': speed,
                'health': health, 'health_age': age, 'uptime': uptime,
                'kernel': os.uname().release, 'hardware_writes': False}


def fmt(value, suffix='', digits=0):
    return '—' if value is None else f'{value:.{digits}f}{suffix}'


def gib(value):
    return fmt(value / 1024**3 if value is not None else None, ' GiB', 1)


def human_rate(value):
    if value is None:
        return '—'
    return fmt(value / 1024**2, ' MiB/s', 2) if value >= 1024**2 else fmt(value / 1024, ' KiB/s', 1)


def load_graphics():
    import gi
    gi.require_version('Gtk', '3.0')
    gi.require_version('PangoCairo', '1.0')
    from gi.repository import Gtk, Gdk, GLib, Pango, PangoCairo
    import cairo
    return Gtk, Gdk, GLib, Pango, PangoCairo, cairo


def paint(cr, width, height, state, page=0, shift=0):
    _, _, _, Pango, PangoCairo, _ = load_graphics()
    cr.set_source_rgb(0, 0, 0)
    cr.paint()
    scale = min(width / 540, height / 1200)
    cr.translate((width - 540 * scale) / 2 + shift * scale,
                 (height - 1200 * scale) / 2 - shift * scale)
    cr.scale(scale, scale)
    white, muted, accent = '#e1e7e5', '#8e9e99', '#69c9a5'

    def color(value):
        cr.set_source_rgb(*(int(value[i:i + 2], 16) / 255 for i in (1, 3, 5)))

    def label(value, x, y, size=18, ink=white, bold=False, width=450):
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(Pango.FontDescription(f'Noto Sans CJK SC {"Bold " if bold else ""}{size}'))
        layout.set_text(str(value), -1)
        layout.set_width(int(width * Pango.SCALE))
        layout.set_height(-1)  # One line; long unexpected values must not overlap rows.
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        color(ink)
        cr.move_to(x, y)
        PangoCairo.show_layout(cr, layout)

    def card(y, title, height=152):
        color('#0c1411')
        cr.rectangle(24, y, 492, height)
        cr.fill()
        label(title, 42, y + 14, 15, muted)

    def bar(x, y, fraction, width=448):
        color('#1b2b24')
        cr.rectangle(x, y, width, 6)
        cr.fill()
        if fraction is not None:
            color(accent)
            cr.rectangle(x, y, width * min(1, max(0, fraction)), 6)
            cr.fill()

    current = dt.datetime.now()
    label('ONEPLUS 8T', 28, 42, 17, accent, True)
    label('服务器状态' if page == 0 else '状态与说明', 28, 82, 29, white, True)
    label(current.strftime('%m月%d日  %H:%M'), 28, 139, 17, muted)
    label('只读  ·  本机采样', 294, 143, 14, muted, width=220)

    if page == 0:
        card(202, '处理器', 172)
        label(fmt(state.get('cpu_percent'), '%'), 42, 231, 43, white, True)
        label('CPU温度  ' + fmt(state.get('cpu_temp'), '°C', 1), 256, 250, 19, accent, width=240)
        label('整机CPU时间占比', 42, 310, 13, muted)
        bar(42, 349, state['cpu_percent'] / 100 if state.get('cpu_percent') is not None else None)

        card(390, '内存  ·  已用 / 总量', 147)
        total, available = state.get('memory_total'), state.get('memory_available')
        used = total - available if total is not None and available is not None else None
        label(gib(used) + ' / ' + gib(total), 42, 433, 25, white, True)
        label('可用 ' + gib(available), 42, 491, 14, muted)

        card(553, '存储  ·  可用空间', 181)
        for index, (key, title) in enumerate((('root', '系统盘'), ('data', '数据盘'))):
            item = state.get(key)
            label(title, 42, 598 + index * 56, 18, muted)
            value = gib(item['available']) + ' / ' + gib(item['total']) if item else '未挂载 / 不可读'
            label(value, 162, 595 + index * 56, 21, white, width=335)

        card(750, '无线网络  ·  wlan0', 163)
        label('接口在线' if state.get('wifi_up') else '接口未连接', 42, 790, 21, accent)
        speeds = state.get('net_rates') or (None, None)
        label('↓ ' + human_rate(speeds[0]), 42, 839, 17)
        label('↑ ' + human_rate(speeds[1]), 270, 839, 17, width=235)
        label('外网可用性未在此检测', 42, 884, 11, muted)

        card(929, '电池与限充', 160)
        battery = state.get('battery', {})
        label(fmt(battery.get('capacity'), '%'), 42, 963, 33, white, True)
        label(fmt(battery.get('temp'), '°C', 1), 225, 974, 21, accent)
        health = state.get('health')
        charge = health.get('charge_control', {}) if health else {}
        verified = charge.get('policy_active') and charge.get('inhibit_readback_verified')
        stop, resume = charge.get('stop_percent'), charge.get('resume_percent')
        message = f'{stop}/{resume} · 停充读回已验证（快照）' if verified else '限充读回未确认 / 快照不可用'
        label(message, 42, 1033, 13, muted)
    else:
        card(202, '运行状态', 205)
        uptime = state.get('uptime')
        label('运行 ' + (f'{int(uptime // 3600)}小时 {int(uptime % 3600 // 60)}分钟' if uptime is not None else '—'), 42, 252, 23, white, True)
        label('Debian 13 · arm64', 42, 310, 18, accent)
        label(state.get('kernel', '—'), 42, 354, 11, muted)
        card(425, '充电策略  ·  只读健康快照', 230)
        health = state.get('health')
        control = health.get('charge_control', {}) if health else {}
        label('策略有效' if control.get('policy_active') else '策略状态未确认', 42, 475, 24, accent)
        label('快照年龄 ' + fmt(state.get('health_age'), '秒'), 42, 533, 17)
        label('完整80/75物理周期：尚未验收', 42, 576, 16, muted)
        label('电池读数不代表USB附近局部温度', 42, 615, 13, muted)
        card(674, '使用方式', 345)
        for index, line in enumerate(('短按电源键：亮屏 / 熄屏', '轻触屏幕：切换两页', '亮屏每3秒采样，无动画', '熄屏暂停指标采样与重绘', 'XFCE维护桌面通过SSH按需启动', '屏幕扫描仍为120Hz')):
            label(line, 42, 724 + index * 45, 16, muted if index >= 3 else white)
    label('轻触切页   ● ○' if page == 0 else '轻触切页   ○ ●', 28, 1124, 15, accent)
    label('短按电源键亮灭屏 · 服务器继续运行', 28, 1164, 12, muted)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sample', action='store_true')
    parser.add_argument('--preview', type=Path)
    parser.add_argument('--page', type=int, choices=(0, 1), default=0)
    args = parser.parse_args()
    sampler = Sampler()
    if args.sample:
        sampler.sample()
        time.sleep(0.1)
        print(json.dumps(sampler.sample(), ensure_ascii=False, indent=2))
        return
    Gtk, Gdk, GLib, _, _, cairo = load_graphics()
    state = sampler.sample()
    if args.preview:
        surface = cairo.ImageSurface(cairo.FORMAT_RGB24, 1080, 2400)
        paint(cairo.Context(surface), 1080, 2400, state, args.page)
        surface.write_to_png(str(args.preview))
        return
    window = Gtk.Window(title='Kebab Status Panel')
    window.set_decorated(False)
    display = Gdk.Display.get_default()
    selected = display.get_primary_monitor() or display.get_monitor(0)
    monitor = selected.get_geometry()
    window.set_default_size(monitor.width, monitor.height)
    window.move(monitor.x, monitor.y)
    window.fullscreen()
    area = Gtk.DrawingArea()
    window.add(area)
    area.add_events(Gdk.EventMask.BUTTON_RELEASE_MASK | Gdk.EventMask.TOUCH_MASK)
    page, frame_count, sample_count = args.page, 0, 1
    last_touch = -10.0
    lit = None
    backlight = Path('/sys/class/backlight/ae94000.dsi.0/brightness')
    runtime = Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')) / 'kebab-status-panel.json'

    def record():
        value = {'monotonic_seconds': time.monotonic(), 'screen_requested_on': lit,
                 'page': page, 'frames': frame_count, 'samples': sample_count,
                 'hardware_writes': False, 'refresh_seconds': 3, 'pid': os.getpid()}
        try:
            tmp = runtime.with_suffix('.tmp')
            tmp.write_text(json.dumps(value) + '\n')
            tmp.replace(runtime)
        except OSError:
            pass

    def draw(widget, cr):
        nonlocal frame_count
        # Keep the last frame on expose while dark; never request a redraw ourselves.
        paint(cr, widget.get_allocated_width(), widget.get_allocated_height(), state,
              page, (int(time.monotonic() // 60) % 5) - 2)
        frame_count += 1
        record()
        return False

    def flip(*_):
        nonlocal page, last_touch
        now = time.monotonic()
        if lit is False or now - last_touch < 0.45:
            return True
        last_touch, page = now, 1 - page
        area.queue_draw()
        record()
        return True

    def touch(_widget, event):
        if event.type == Gdk.EventType.TOUCH_END:
            return flip()
        return False

    def tick():
        nonlocal state, lit, sample_count
        requested = number(backlight, high=4095)
        # Unknown requests are explicitly unknown, and don't suspend monitoring.
        lit = None if requested is None else requested > 0
        if lit is not False:
            state = sampler.sample()
            sample_count += 1
            area.queue_draw()
        else:
            # Reset rate baselines: waking must not label a dark-period average as current.
            sampler.cpu = sampler.net = sampler.stamp = None
        record()
        return True

    area.connect('draw', draw)
    area.connect('button-release-event', flip)
    area.connect('touch-event', touch)
    window.connect('destroy', Gtk.main_quit)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, lambda: (Gtk.main_quit(), False)[1])
    tick()
    GLib.timeout_add_seconds(3, tick)
    window.show_all()
    # Focus and dimensions also work without a window manager.
    window.resize(monitor.width, monitor.height)
    window.present()
    Gtk.main()


if __name__ == '__main__':
    main()
