"""Media Foundation video provider check.

Double-click the launcher on the PC under test. This process (the "driver")
shows a progress window and runs one child process per GL configuration and
clip, each with its own environment, because the GL backend is fixed when
Kivy's window is created:

    glew                     desktop GL over WGL
    sdl3                     desktop GL over WGL
    angle_sdl3               OpenGL ES; the driver's own ES profile over WGL
                             if it has one, otherwise ANGLE
    angle_sdl3_forced_angle  angle_sdl3 + SDL_OPENGL_ES_DRIVER=1 (always ANGLE)

Each child plays a clip in a Video widget, grabs the rendered widget twice
about a second apart after the first frame, and records the GL renderer,
whether delivery was zero-copy, and whether the picture changed between the
grabs. The Big Buck Bunny run also times five thumbnails. Everything --
summary.txt, per-run JSON, screenshots, each child's Kivy log, and the GPU
list in DXGI order (Media Foundation decodes on adapter 0) -- goes to a
folder on the desktop, which is also zipped for sending back.
"""
import json
import os
import sys

os.environ.setdefault('KIVY_NO_ARGS', '1')
os.environ.setdefault('KIVY_DESKTOP_PATH_ID', 'MF Video Check')
os.environ.setdefault('KIVY_NO_FILELOG', '1')

HERE = os.path.dirname(os.path.abspath(__file__))
CLIPS = {
    # Synthetic, 256x256; its blue channel counts frames.
    'anim': os.path.join(HERE, 'clips', 'mf_playback_test_clip.mp4'),
    # Real footage, 640x360; used for orientation and thumbnails.
    'bbb': os.path.join(HERE, 'clips', 'big_buck_bunny_360_10s.mp4'),
}
CONFIGS = [
    ('glew', {'KIVY_GL_BACKEND': 'glew'}),
    ('sdl3', {'KIVY_GL_BACKEND': 'sdl3'}),
    ('angle_sdl3', {'KIVY_GL_BACKEND': 'angle_sdl3'}),
    ('angle_sdl3_forced_angle', {'KIVY_GL_BACKEND': 'angle_sdl3',
                                 'SDL_OPENGL_ES_DRIVER': '1'}),
]
CHILD_TIMEOUT = 90


# ---------------------------------------------------------------------------
# Child: one configuration, one clip
# ---------------------------------------------------------------------------

def run_child(config, clip_key, outdir):
    import time

    from kivy.app import App
    from kivy.clock import Clock
    from kivy.core.window import Window
    from kivy.graphics.opengl import GL_RENDERER, GL_VERSION, glGetString
    from kivy.uix.video import Video

    result = {'config': config, 'clip': clip_key, 'env': {
        k: os.environ.get(k) for k in ('KIVY_GL_BACKEND',
                                       'SDL_OPENGL_ES_DRIVER')}}
    prefix = os.path.join(outdir, f'{config}_{clip_key}')

    def mean_rgb(img):
        tex = img.texture
        w, h = tex.size
        px = tex.pixels
        tot = [0, 0, 0]
        n = 0
        for y in range(h // 4, 3 * h // 4, 4):
            row = y * w * 4
            for x in range(w // 4, 3 * w // 4, 4):
                i = row + x * 4
                tot[0] += px[i]
                tot[1] += px[i + 1]
                tot[2] += px[i + 2]
                n += 1
        return [round(t / n, 1) for t in tot]

    class CheckApp(App):
        def build(self):
            Window.size = (640, 360)
            self.title = f'MF check: {config} / {clip_key}'
            self.grabs = []
            self.video = Video(source=CLIPS[clip_key], state='play')
            self.video.bind(loaded=self.on_loaded)
            self.giveup = Clock.schedule_once(self.not_loaded, 15)
            return self.video

        def on_loaded(self, video, loaded):
            if loaded and not self.grabs and self.giveup:
                self.giveup.cancel()
                self.giveup = None
                Clock.schedule_once(self.grab, 0.3)
                Clock.schedule_once(self.grab, 1.3)
                Clock.schedule_once(self.report, 1.5)

        def grab(self, dt):
            img = self.video.export_as_image()
            path = f'{prefix}_grab{len(self.grabs)}.png'
            img.save(path, flipped=False)
            self.grabs.append({'position': round(self.video.position, 2),
                               'mean_rgb': mean_rgb(img),
                               'png': os.path.basename(path)})

        def gl_info(self):
            result['gl_renderer'] = glGetString(GL_RENDERER).decode(
                'utf-8', 'replace')
            result['gl_version'] = glGetString(GL_VERSION).decode(
                'utf-8', 'replace')

        def not_loaded(self, dt):
            self.gl_info()
            result['provider'] = type(self.video._video).__name__ \
                if self.video._video else None
            result['error'] = 'no frame within 15 s'
            self.finish()

        def report(self, dt):
            self.gl_info()
            v = self.video
            tex = v.texture
            result['provider'] = type(v._video).__name__
            result['texture_size'] = list(tex.size) if tex else None
            result['zero_copy'] = bool(tex and tex.colorfmt == 'bgra')
            result['grabs'] = self.grabs
            a, b = self.grabs[0]['mean_rgb'], self.grabs[1]['mean_rgb']
            result['frames_updating'] = a != b
            result['black'] = all(c < 3 for c in a + b)
            if clip_key == 'bbb':
                self.thumbnails()
            self.finish()

        def thumbnails(self):
            from kivy.core.image import Image as CoreImage
            from kivy.core.video import Video as CoreVideo
            times = []
            ok = 0
            tex = None
            for _ in range(5):
                t0 = time.perf_counter()
                tex = CoreVideo.generate_thumbnail(CLIPS['bbb'], 2,
                                                   size=(480, 270))
                times.append(round(time.perf_counter() - t0, 2))
                ok += tex is not None
            if tex is not None:
                CoreImage(tex).save(f'{prefix}_thumbnail.png')
            result['thumbnails'] = {'ok': ok, 'of': 5, 'seconds': times}

        def finish(self):
            with open(f'{prefix}.json', 'w') as f:
                json.dump(result, f, indent=2)
            self.stop()

    CheckApp().run()


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def dxgi_adapters():
    """GPUs in DXGI enumeration order; Media Foundation decodes on #0."""
    import ctypes
    from ctypes import wintypes

    class LUID(ctypes.Structure):
        _fields_ = [('LowPart', wintypes.DWORD), ('HighPart', wintypes.LONG)]

    class DESC1(ctypes.Structure):
        _fields_ = [('Description', wintypes.WCHAR * 128),
                    ('VendorId', wintypes.UINT), ('DeviceId', wintypes.UINT),
                    ('SubSysId', wintypes.UINT), ('Revision', wintypes.UINT),
                    ('DedicatedVideoMemory', ctypes.c_size_t),
                    ('DedicatedSystemMemory', ctypes.c_size_t),
                    ('SharedSystemMemory', ctypes.c_size_t),
                    ('AdapterLuid', LUID), ('Flags', wintypes.UINT)]

    class GUID(ctypes.Structure):
        _fields_ = [('a', wintypes.DWORD), ('b', wintypes.WORD),
                    ('c', wintypes.WORD), ('d', ctypes.c_ubyte * 8)]

    def method(obj, index, restype, *argtypes):
        vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(
            ctypes.c_void_p)))[0]
        proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
        return proto(vtbl[index])

    iid = GUID(0x770aae78, 0xf26f, 0x4dba,
               (ctypes.c_ubyte * 8)(0xa8, 0x29, 0x25, 0x3c,
                                    0x83, 0xd1, 0xb3, 0x87))
    factory = ctypes.c_void_p()
    hr = ctypes.windll.dxgi.CreateDXGIFactory1(ctypes.byref(iid),
                                               ctypes.byref(factory))
    if hr != 0:
        return [f'CreateDXGIFactory1 failed: 0x{hr & 0xffffffff:08X}']
    out = []
    i = 0
    enum1 = method(factory, 12, ctypes.c_long, wintypes.UINT,
                   ctypes.POINTER(ctypes.c_void_p))
    while True:
        adapter = ctypes.c_void_p()
        if enum1(factory, i, ctypes.byref(adapter)) != 0:
            break
        desc = DESC1()
        method(adapter, 10, ctypes.c_long, ctypes.POINTER(DESC1))(
            adapter, ctypes.byref(desc))
        out.append({'index': i, 'name': desc.Description,
                    'vendor_id': f'0x{desc.VendorId:04X}',
                    'dedicated_vram_mb': desc.DedicatedVideoMemory >> 20,
                    'software': bool(desc.Flags & 2)})
        method(adapter, 2, ctypes.c_ulong)(adapter)
        i += 1
    method(factory, 2, ctypes.c_ulong)(factory)
    return out


def gpu_drivers():
    import subprocess
    cmd = ['powershell', '-NoProfile', '-Command',
           'Get-CimInstance Win32_VideoController | '
           'Select-Object Name, DriverVersion, DriverDate | ConvertTo-Json']
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=30, creationflags=0x08000000).stdout
        data = json.loads(out)
        return data if isinstance(data, list) else [data]
    except Exception as e:
        return [f'unavailable: {e!r}']


def results_dir():
    import datetime
    import platform
    # MF_CHECK_OUT overrides the location (used when testing the bundle).
    base = os.environ.get('MF_CHECK_OUT') or os.path.join(
        os.environ.get('USERPROFILE', HERE), 'Desktop')
    if not os.path.isdir(base):
        base = os.environ.get('USERPROFILE', HERE)
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    path = os.path.join(base, f'mf-check-{platform.node()}-{stamp}')
    os.makedirs(path, exist_ok=True)
    return path


def summarize(outdir, system, runs):
    lines = ['Media Foundation video check', '',
             f"Windows: {system['windows']}",
             'GPUs in DXGI order (Media Foundation decodes on #0):']
    for a in system['dxgi_adapters']:
        lines.append(f'  {a}' if isinstance(a, str) else
                     f"  #{a['index']} {a['name']} ({a['vendor_id']}, "
                     f"{a['dedicated_vram_mb']} MB)"
                     + (' [software]' if a['software'] else ''))
    lines.append('GPU drivers:')
    for d in system['gpu_drivers']:
        lines.append(f'  {d}' if isinstance(d, str) else
                     f"  {d.get('Name')}: {d.get('DriverVersion')}")
    lines += ['', f"{'config':26}{'clip':6}{'result':10}{'path':11}"
              f"{'updating':10}{'thumbs':8}renderer"]
    for r in runs:
        if 'error' in r:
            status = 'FAIL'
        elif r.get('black') or not r.get('frames_updating'):
            status = 'FAIL'
        elif r.get('provider') != 'VideoMediaFoundation':
            status = 'FAIL'
        else:
            status = 'ok'
        t = r.get('thumbnails')
        thumbs = f"{t['ok']}/{t['of']}" if t else '-'
        path = ('zero-copy' if r.get('zero_copy') else 'cpu-copy') \
            if 'zero_copy' in r else '-'
        upd = str(r.get('frames_updating', '-'))
        lines.append(f"{r['config']:26}{r['clip']:6}{status:10}{path:11}"
                     f"{upd:10}{thumbs:8}{r.get('gl_renderer', '-')}")
        if 'error' in r:
            lines.append(f"    error: {r['error']}")
        for note in r.get('provider_log', []):
            lines.append(f'    log: {note}')
    text = '\n'.join(lines) + '\n'
    with open(os.path.join(outdir, 'summary.txt'), 'w',
              encoding='utf-8') as f:
        f.write(text)
    with open(os.path.join(outdir, 'summary.json'), 'w') as f:
        json.dump({'system': system, 'runs': runs}, f, indent=2)
    return text


def run_all(outdir, progress):
    import platform
    import shutil
    import subprocess

    system = {'windows': platform.platform(), 'dxgi_adapters': [],
              'gpu_drivers': []}
    try:
        system['dxgi_adapters'] = dxgi_adapters()
    except Exception as e:
        system['dxgi_adapters'] = [f'unavailable: {e!r}']
    system['gpu_drivers'] = gpu_drivers()

    runs = []
    total = len(CONFIGS) * len(CLIPS)
    for name, env_extra in CONFIGS:
        for clip_key in CLIPS:
            progress(f'Running {len(runs) + 1} of {total}: '
                     f'{name} / {clip_key}')
            env = dict(os.environ)
            env.pop('KIVY_GL_BACKEND', None)
            env.pop('SDL_OPENGL_ES_DRIVER', None)
            env.update(env_extra)
            env['PYTHONPATH'] = HERE + os.pathsep + env.get('PYTHONPATH', '')
            log_path = os.path.join(outdir, f'{name}_{clip_key}.log')
            # alter_sys so the module is sys.modules['__main__'] with a
            # __file__: App.load_kv looks the App class's file up there.
            cmd = [sys.executable, '-c',
                   'import runpy; runpy.run_module("main", '
                   'run_name="__main__", alter_sys=True)',
                   '--child', name, clip_key, outdir]
            with open(log_path, 'w', encoding='utf-8',
                      errors='replace') as log:
                try:
                    proc = subprocess.run(
                        cmd, env=env, cwd=HERE, stdout=log,
                        stderr=subprocess.STDOUT, timeout=CHILD_TIMEOUT,
                        creationflags=0x08000000)  # CREATE_NO_WINDOW
                    exit_code = proc.returncode
                except subprocess.TimeoutExpired:
                    exit_code = 'timeout'
            json_path = os.path.join(outdir, f'{name}_{clip_key}.json')
            if os.path.exists(json_path):
                with open(json_path) as f:
                    r = json.load(f)
            else:
                r = {'config': name, 'clip': clip_key,
                     'error': f'child exited without a result '
                              f'(exit {exit_code}); see {name}_{clip_key}.log'}
            with open(log_path, encoding='utf-8', errors='replace') as f:
                r['provider_log'] = [
                    line.strip() for line in f
                    if 'VideoMediaFoundation' in line or
                    'Traceback' in line]
            runs.append(r)

    text = summarize(outdir, system, runs)
    archive = shutil.make_archive(outdir, 'zip', outdir)
    return text, archive


def run_driver():
    import threading

    from kivy.app import App
    from kivy.clock import mainthread
    from kivy.core.window import Window
    from kivy.uix.boxlayout import BoxLayout
    from kivy.uix.button import Button
    from kivy.uix.label import Label
    from kivy.uix.scrollview import ScrollView

    outdir = results_dir()

    class DriverApp(App):
        title = 'MF Video Check'

        def build(self):
            Window.size = (1000, 600)
            root = BoxLayout(orientation='vertical', padding=12, spacing=8)
            self.status = Label(
                text='Starting... (test windows will open and close; '
                     'leave them alone)',
                size_hint_y=None, height=40)
            self.body = Label(text=f'Results folder:\n{outdir}',
                              font_name='RobotoMono-Regular',
                              font_size='13sp', halign='left', valign='top',
                              size_hint_y=None)
            self.body.bind(
                width=lambda w, v: setattr(w, 'text_size', (v, None)),
                texture_size=lambda w, v: setattr(w, 'height', v[1]))
            scroll = ScrollView()
            scroll.add_widget(self.body)
            self.open_btn = Button(text='Open results folder',
                                   size_hint_y=None, height=44,
                                   disabled=True)
            self.open_btn.bind(on_release=lambda *a: os.startfile(outdir))
            root.add_widget(self.status)
            root.add_widget(scroll)
            root.add_widget(self.open_btn)
            return root

        def on_start(self):
            threading.Thread(target=self.work, daemon=True).start()

        def work(self):
            try:
                text, archive = run_all(outdir, self.set_status)
                self.done(text, archive)
            except Exception:
                import traceback
                self.done(traceback.format_exc(), None)

        @mainthread
        def set_status(self, text):
            self.status.text = text

        @mainthread
        def done(self, text, archive):
            self.status.text = 'Done. Send back the zip file:' \
                if archive else 'The check itself failed:'
            self.body.text = (f'{archive}\n\n' if archive else '') + text
            self.open_btn.disabled = False

    DriverApp().run()


if __name__ == '__main__':
    if '--child' in sys.argv:
        i = sys.argv.index('--child')
        run_child(sys.argv[i + 1], sys.argv[i + 2], sys.argv[i + 3])
    else:
        run_driver()
