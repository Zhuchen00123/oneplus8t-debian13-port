#!/usr/bin/env python3
"""Phone UI: real PTY terminal, existing read-only dashboard, Phosh desktop.

Runs unprivileged. Reads Hall snapshot/backlight only; never writes hardware or
controls server units. Diagnostic state excludes typed text and terminal output.
"""
import argparse
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import time


def module(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    result = importlib.util.module_from_spec(spec)
    loader.exec_module(result)
    return result


state_lib = module('kebab_ui_state', Path(__file__).with_name('kebab-ui-state.py'))


def external_keyboards():
    result = []
    for device in Path('/sys/class/input').glob('event*/device'):
        try:
            bus = int((device / 'id/bustype').read_text().strip(), 16)
            if state_lib.keyboard_capable(bus, (device / 'capabilities/key').read_text()):
                result.append(device.parent.name)
        except (OSError, ValueError):
            pass
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--force-mode', choices=state_lib.MODES)
    parser.add_argument('--snapshot', type=Path,
                        default=Path('/sys/module/kebab_alert_slider/parameters/snapshot'))
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise SystemExit('The graphical UI must run as an ordinary user')
    import gi
    for name, version in (('Gtk', '3.0'), ('Vte', '2.91'), ('GtkLayerShell', '0.1')):
        gi.require_version(name, version)
    from gi.repository import Gtk, Gdk, GLib, Gio, Pango, Vte, GtkLayerShell
    panel = module('kebab_panel', Path('/usr/local/libexec/kebab-panel-renderer.py'))
    runtime = Path(os.environ['XDG_RUNTIME_DIR'])
    diag = runtime / 'kebab-three-mode.json'
    control = runtime / 'kebab-three-mode-request.json'
    gate = state_lib.ModeGate()
    sampler = panel.Sampler()
    sample = sampler.sample()
    sample['three_mode'] = True
    page, frames, samples = 2, 0, 1
    mode = None
    lit = None
    modifiers = {'ctrl': False, 'alt': False, 'shift': False}
    locked_modifiers = set()
    last_flip = -10.0
    last_sample = 0.0
    try:
        last_request = control.stat().st_mtime_ns
    except OSError:
        last_request = None
    known_keyboards = []
    keyboard_manual = False
    terminal_started = False
    repeat_timer = None
    osk_proxy = None
    osk_error = False
    keyboard_settings = Gio.Settings.new('org.gnome.desktop.a11y.applications')

    css = Gtk.CssProvider()
    css.load_from_data(b'''
      window { background: #07100d; color: #e1e7e5; }
      button { min-height: 40px; min-width: 30px; padding: 3px 4px;
               border-radius: 7px; background: #172a22; color: #e1e7e5; }
      button:checked { background: #337d60; }
      .heading { padding: 6px; font-size: 17px; }
      .keys button { font-size: 16px; min-height: 44px; }
    ''')
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css,
                                             Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    window = Gtk.Window(title='Kebab Console / Panel')
    window.set_decorated(False)
    window.set_default_size(540, 1200)
    GtkLayerShell.init_for_window(window)
    GtkLayerShell.set_namespace(window, 'kebab-console-panel')
    GtkLayerShell.set_layer(window, GtkLayerShell.Layer.OVERLAY)
    GtkLayerShell.set_keyboard_mode(window, GtkLayerShell.KeyboardMode.EXCLUSIVE)
    for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM,
                 GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
        GtkLayerShell.set_anchor(window, edge, True)
    GtkLayerShell.set_exclusive_zone(window, -1)
    stack = Gtk.Stack()
    stack.set_transition_type(Gtk.StackTransitionType.NONE)
    window.add(stack)
    area = Gtk.DrawingArea()
    area.add_events(Gdk.EventMask.BUTTON_RELEASE_MASK | Gdk.EventMask.TOUCH_MASK)
    stack.add_named(area, 'panel')
    terminal_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
    stack.add_named(terminal_page, 'terminal')
    toolbar = Gtk.Box(spacing=5)
    title = Gtk.Label(label='本机终端 · debian')
    title.set_ellipsize(Pango.EllipsizeMode.END)
    title.get_style_context().add_class('heading')
    toolbar.pack_start(title, True, True, 0)
    terminal_page.pack_start(toolbar, False, False, 0)
    terminal = Vte.Terminal()
    terminal.set_font(Pango.FontDescription('Monospace 13'))
    terminal.set_scrollback_lines(10000)
    terminal.set_mouse_autohide(True)
    terminal.set_color_background(Gdk.RGBA(0.025, 0.045, 0.035, 1))
    terminal.set_color_foreground(Gdk.RGBA(.88, .92, .89, 1))
    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroll.set_kinetic_scrolling(True)
    scroll.add(terminal)
    terminal_page.pack_start(scroll, True, True, 0)
    keybox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    keybox.get_style_context().add_class('keys')
    terminal_page.pack_end(keybox, False, False, 0)

    def connect_osk():
        nonlocal osk_proxy, osk_error
        if osk_proxy is not None:
            return osk_proxy
        try:
            osk_proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_AUTO_START,
                None, 'sm.puri.OSK0', '/sm/puri/OSK0', 'sm.puri.OSK0', None)
            def visibility_changed(proxy, changed, invalidated):
                nonlocal keyboard_manual
                values = changed.unpack()
                if 'Visible' in values:
                    keyboard_manual = bool(values['Visible']) and mode == 'desktop'
            osk_proxy.connect('g-properties-changed', visibility_changed)
            return osk_proxy
        except GLib.Error:
            osk_error = True
            return None

    def osk_visible(visible):
        nonlocal osk_error
        proxy = connect_osk()
        if proxy is None:
            return
        def done(proxy, result):
            nonlocal osk_error
            try:
                proxy.call_finish(result)
                osk_error = False
            except GLib.Error:
                osk_error = True
        proxy.call('SetVisible', GLib.Variant('(b)', (visible,)),
                   Gio.DBusCallFlags.NONE, 2000, None, done)

    def reset_modifiers(all_keys=True):
        for key, button in modifier_buttons.items():
            if not all_keys and key in locked_modifiers:
                continue
            locked_modifiers.discard(key)
            button.set_label(key.title())
            button.set_active(False)
            modifiers[key] = False

    def send_key(key):
        value = state_lib.key_bytes(key, **modifiers)
        if value:
            terminal.feed_child(value)
        reset_modifiers(all_keys=False)
        terminal.grab_focus()

    def stop_repeat(*_):
        nonlocal repeat_timer
        if repeat_timer:
            GLib.source_remove(repeat_timer)
            repeat_timer = None

    def repeating_delete():
        send_key('Backspace')
        return True

    def begin_repeat(*_):
        nonlocal repeat_timer
        stop_repeat()
        send_key('Backspace')
        def start():
            nonlocal repeat_timer
            repeat_timer = GLib.timeout_add(80, repeating_delete)
            return False
        repeat_timer = GLib.timeout_add(400, start)

    def button(text, callback, parent, expand=True):
        item = Gtk.Button(label=text)
        item.set_can_focus(False)
        item.connect('clicked', callback)
        parent.pack_start(item, expand, expand, 0)
        return item

    def toggle_terminal_keys(*_):
        keybox.set_visible(not keybox.get_visible())
        terminal.grab_focus()

    button('⌨', toggle_terminal_keys, toolbar, False)
    button('复制', lambda *_: terminal.copy_clipboard_format(Vte.Format.TEXT), toolbar, False)

    def paste(*_):
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        def receive(_clipboard, text):
            if not text:
                return
            if '\n' in text or '\r' in text:
                dialog = Gtk.MessageDialog(transient_for=window, modal=True,
                    message_type=Gtk.MessageType.QUESTION, buttons=Gtk.ButtonsType.OK_CANCEL,
                    text='粘贴多行内容到终端？')
                dialog.format_secondary_text(text[:1000])
                answer = dialog.run()
                dialog.destroy()
                if answer != Gtk.ResponseType.OK:
                    return
            # VTE's paste helper handles bracketed-paste for supporting applications.
            terminal.paste_text(text)
            terminal.grab_focus()
        clipboard.request_text(receive)
    button('粘贴', paste, toolbar, False)
    font_size = 13
    def resize_font(delta):
        nonlocal font_size
        font_size = max(9, min(24, font_size + delta))
        terminal.set_font(Pango.FontDescription(f'Monospace {font_size}'))
    button('A−', lambda *_: resize_font(-1), toolbar, False)
    button('A+', lambda *_: resize_font(1), toolbar, False)
    modifier_buttons = {}
    shortcut_row = Gtk.Box(spacing=4)
    keybox.pack_start(shortcut_row, False, False, 0)
    for key in ('Esc', 'Tab'):
        button(key, lambda _, key=key: send_key(key), shortcut_row)
    for key in ('ctrl', 'alt', 'shift'):
        item = Gtk.ToggleButton(label=key.title())
        item.set_can_focus(False)
        def toggle(item, key=key):
            modifiers[key] = item.get_active()
            if not item.get_active():
                locked_modifiers.discard(key)
                item.set_label(key.title())
        item.connect('toggled', toggle)
        gesture = Gtk.GestureLongPress.new(item)
        def lock_modifier(gesture, x, y, key=key, item=item):
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            locked_modifiers.add(key)
            item.set_active(True)
            item.set_label(key.title() + ' ●')
        gesture.connect('pressed', lock_modifier)
        item._long_press = gesture
        modifier_buttons[key] = item
        shortcut_row.pack_start(item, True, True, 0)
    for key in ('Left', 'Down', 'Up', 'Right'):
        label = {'Left': '←', 'Down': '↓', 'Up': '↑', 'Right': '→'}[key]
        button(label, lambda _, key=key: send_key(key), shortcut_row)
    for keys in ('1234567890', 'qwertyuiop', 'asdfghjkl', 'zxcvbnm', '`-=[];\'\\,./'):
        row = Gtk.Box(spacing=4)
        keybox.pack_start(row, False, False, 0)
        for key in keys:
            button(key, lambda _, key=key: send_key(key), row)
    bottom = Gtk.Box(spacing=4)
    keybox.pack_start(bottom, False, False, 0)
    delete = Gtk.Button(label='⌫')
    delete.set_can_focus(False)
    delete.connect('pressed', begin_repeat)
    delete.connect('released', stop_repeat)
    bottom.pack_start(delete, True, True, 0)
    button('Space', lambda *_: send_key('Space'), bottom)
    button('Enter', lambda *_: send_key('Enter'), bottom)

    selection = Gtk.Popover.new(terminal)
    selection_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    selection.add(selection_box)
    def selection_action(action):
        action()
        selection.hide()
    button('选择全部', lambda *_: selection_action(terminal.select_all), selection_box)
    button('复制选区', lambda *_: selection_action(
        lambda: terminal.copy_clipboard_format(Vte.Format.TEXT)), selection_box)
    long_press = Gtk.GestureLongPress.new(terminal)
    def select_context(gesture, x, y):
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        selection.set_pointing_to(rect)
        selection.show_all()
        selection.popup()
    long_press.connect('pressed', select_context)

    def start_terminal():
        nonlocal terminal_started
        if terminal_started:
            return
        terminal_started = True
        def spawned(widget, pid, error, data):
            nonlocal terminal_started
            if error:
                terminal_started = False
                title.set_text('终端连接失败 · 点击重连')
        terminal.spawn_async(Vte.PtyFlags.DEFAULT, str(Path.home()),
            ['/usr/bin/tmux', '-L', 'kebab-terminal', 'new-session', '-A', '-s', 'console'],
            None, GLib.SpawnFlags.DEFAULT, None, None, -1, None, spawned, None)

    def terminal_exit(*_):
        nonlocal terminal_started
        terminal_started = False
        title.set_text('终端已退出 · 点击重连')
    terminal.connect('child-exited', terminal_exit)
    def reconnect(*_):
        start_terminal()
        title.set_text('本机终端 · debian')
        terminal.grab_focus()
    button('重连', reconnect, toolbar, False)

    def switch(next_mode):
        nonlocal mode, keyboard_manual
        if next_mode == mode:
            return
        stop_repeat()
        reset_modifiers()
        keyboard_manual = False
        keyboard_settings.set_boolean('screen-keyboard-enabled',
                                      next_mode == 'desktop' and not known_keyboards)
        osk_visible(False)
        mode = next_mode
        if mode == 'desktop':
            window.hide()
        else:
            stack.set_visible_child_name(mode)
            window.show_all()
            if mode == 'terminal':
                start_terminal()
                keybox.set_visible(not known_keyboards)
                terminal.grab_focus()
            else:
                area.queue_draw()

    def record():
        data = {'pid': os.getpid(), 'mode': mode, 'screen_requested_on': lit,
                'panel_page': page, 'panel_frames': frames, 'panel_samples': samples,
                'terminal_started': terminal_started, 'external_keyboard_count': len(known_keyboards),
                'keyboard_manual': keyboard_manual, 'osk_error': osk_error,
                'monotonic_seconds': time.monotonic(), 'hardware_writes': False}
        try:
            temporary = diag.with_suffix('.tmp')
            temporary.write_text(json.dumps(data) + '\n')
            temporary.replace(diag)
        except OSError:
            pass

    def draw(widget, cr):
        nonlocal frames
        panel.paint(cr, widget.get_allocated_width(), widget.get_allocated_height(), sample,
                    page, (int(time.monotonic() // 60) % 5) - 2)
        frames += 1
        record()
        return False
    def flip(*_):
        nonlocal page, last_flip
        now = time.monotonic()
        if mode != 'panel' or lit is False or now - last_flip < .45:
            return True
        last_flip, page = now, (page + 1) % 3
        area.queue_draw()
        return True
    area.connect('draw', draw)
    area.connect('button-release-event', flip)
    area.connect('touch-event', lambda _, event: flip() if event.type == Gdk.EventType.TOUCH_END else False)

    def tick():
        nonlocal sample, samples, last_sample, lit, known_keyboards, keyboard_manual, last_request
        now = time.monotonic()
        brightness = panel.number('/sys/class/backlight/ae94000.dsi.0/brightness', high=4095)
        lit = None if brightness is None else brightness > 0
        try:
            snapshot = json.loads(args.snapshot.read_text())
        except (OSError, ValueError):
            snapshot = None
        selected = gate.accept(snapshot, now)
        if args.force_mode:
            selected = args.force_mode if mode != args.force_mode else None
        try:
            stamp = control.stat().st_mtime_ns
            if stamp != last_request:
                requested = json.loads(control.read_text())
                if requested.get('mode') in state_lib.MODES:
                    selected = requested['mode']
                last_request = stamp
        except (OSError, ValueError, AttributeError):
            pass
        if selected:
            switch(selected)
        if now - last_sample >= 3:
            current_keys = external_keyboards()
            if current_keys != known_keyboards:
                known_keyboards = current_keys
                reset_modifiers()
                keyboard_settings.set_boolean('screen-keyboard-enabled',
                                              mode == 'desktop' and not known_keyboards)
                if known_keyboards:
                    keybox.hide()
                    keyboard_manual = False
                    osk_visible(False)
            if lit is not False and mode == 'panel':
                sample = sampler.sample()
                sample['three_mode'] = True
                samples += 1
                area.queue_draw()
            else:
                sampler.cpu = sampler.net = sampler.stamp = None
            last_sample = now
        record()
        return True

    window.connect('delete-event', lambda *_: True)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, lambda: (Gtk.main_quit(), False)[1])
    # Panel is a safe initial surface until the measured physical position settles.
    switch(args.force_mode or 'panel')
    tick()
    GLib.timeout_add(200, tick)
    Gtk.main()
    stop_repeat()
    osk_visible(False)
    window.destroy()


if __name__ == '__main__':
    main()
