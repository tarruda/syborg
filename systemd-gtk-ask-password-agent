#!/usr/bin/python3
# Port of "gnome-ask-password-agent.vala" from the old systemd-ui package
import os
import time
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Notify", "0.7")

from gi.repository import Gio, GLib, Gtk, Notify


class AppState(object):
    def __init__(self):
        self.status_icon = None
        self.directory = None
        self.socket = None
        self.password_dialog = None
        self.notification = None
        self.current = None


def log(*msg):
    print(msg[0].format(*list(str(m) for m in msg[1:])), file=sys.stderr)


def create_password_dialog(message, icon):
    pd = Gtk.Dialog(title='System Password')

    pd.set_border_width(8)
    pd.set_default_response(Gtk.ResponseType.OK)
    pd.set_icon_name(icon)

    pd.add_button(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL)
    pd.add_button(Gtk.STOCK_OK, Gtk.ResponseType.OK)

    content = pd.get_content_area()

    hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
    hbox.set_border_width(8)
    content.add(hbox)

    image = Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.DIALOG)
    hbox.pack_start(image, False, False, 0)

    vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    hbox.pack_start(vbox, True, True, 0)

    label = Gtk.Label(label=message)
    vbox.pack_start(label, False, False, 0)

    pd.entry = Gtk.Entry()
    pd.entry.set_visibility(False)
    pd.entry.set_width_chars(30)
    pd.entry.set_activates_default(True)
    vbox.pack_start(pd.entry, False, False, 0)

    pd.entry.connect('activate', lambda *a: pd.response(Gtk.ResponseType.OK))
    pd.show_all()
    return pd


def file_monitor_changed(monitor, file, other_file, event_type, app):
    if not file.get_basename().startswith('ask.'):
        return

    if event_type in [Gio.FileMonitorEvent.CREATED,
                      Gio.FileMonitorEvent.DELETED]:
        try:
            look_for_password(app)
        except Exception as e:
            log('error in look_for_password: {}', e)
        

def look_for_password(app):
    if app.current:
        if not app.current.query_exists():
            app.current = None
            if app.password_dialog:
                app.password_dialog.response(Gtk.ResponseType.REJECT)

    if not app.current:
        enumerator = app.directory.enumerate_children('standard::name',
                Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS)
        for fi in enumerator:
            if not fi.get_name().startswith('ask.'):
                continue
            app.current = app.directory.get_child(fi.get_name())
            if load_password(app):
                break
            app.current = None

    if not app.current:
        app.status_icon.set_property('visible', False)


def load_password(app):
    key_file = GLib.KeyFile()

    try:
        key_file.load_from_file(app.current.get_path(),
                GLib.KeyFileFlags.NONE)
    except Exception as e:
        log('error loading key_file: {}', e)
        return False

    try:
        not_after = int(key_file.get_string('Ask', 'NotAfter'))
    except Exception as e:
        log('error loading Ask.NotAfter: {}', e)
        return False

    now = time.clock_gettime(1)
    if not_after > 0 and not_after < now:
        return False

    try:
        app.socket = key_file.get_string('Ask', 'Socket')
    except Exception as e:
        log('error loading Ask.Socket: {}', e)
        return False

    try:
        message = key_file.get_string('Ask',
                'Message').encode('utf-8').decode('unicode-escape')
    except Exception as e:
        message = 'Please Enter System Password!'
    app.status_icon.set_property('tooltip_text', message)

    try:
        icon = key_file.get_string('Ask', 'Icon')
    except Exception as e:
        icon = 'dialog-password'
    app.status_icon.set_property('icon_name', icon)

    n = Notify.Notification.new(app.status_icon.get_property('title'),
            message, icon)
    n.set_timeout(5000)
    n.connect('closed',
            lambda *_: app.status_icon.set_property('visible', True))
    n.add_action('enter_pw', 'Enter password', status_icon_activate, app)
    n.show()
    app.notification = n
    return True


def status_icon_activate(sender, *args):
    app = args[-1]

    if not app.current:
        return

    if app.password_dialog:
        app.password_dialog.present()
        return

    app.password_dialog = create_password_dialog(
            app.status_icon.get_property('tooltip_text'),
            app.status_icon.get_property('icon_name'))

    result = app.password_dialog.run()
    password = app.password_dialog.entry.get_text()

    app.password_dialog.destroy()
    app.password_dialog = None

    if result in [Gtk.ResponseType.REJECT, Gtk.ResponseType.DELETE_EVENT,
                  Gtk.ResponseType.CANCEL]:
        return

    reply_argv = ['/usr/bin/pkexec', '/lib/systemd/systemd-reply-password',
            '1' if result == Gtk.ResponseType.OK else '0', app.socket]

    try:
        child = GLib.spawn_async_with_pipes(None, reply_argv, None,
                GLib.SpawnFlags.DO_NOT_REAP_CHILD)
        GLib.child_watch_add(child.child_pid,
                lambda pid, status: GLib.spawn_close_pid(pid))
        with os.fdopen(child.standard_input, 'w') as child_stdin:
            child_stdin.write(password)
    except Exception as e:
        log('error replying password: {}', e)


def main():
    Gtk.init_with_args(sys.argv, '[OPTION ...]', [],
            'systemd-ask-password-agent')
    Notify.init('Password Agent')
    # create AppState which will hold state shared by most callbacks 
    app = AppState()
    # setup a watch on ask-password directory
    app.directory = Gio.File.new_for_path('/run/systemd/ask-password')
    monitor = app.directory.monitor_directory(0)
    monitor.connect('changed', file_monitor_changed, app)
    # create a StatusIcon instance
    app.status_icon = Gtk.StatusIcon(icon_name='dialog-password',
            title='System Password Request', visible=False)
    app.status_icon.connect('activate', status_icon_activate, app)
    # check if a password is currently being requested
    look_for_password(app)
    # start gtk main loop
    Gtk.main()

if __name__ == '__main__':
    main()
