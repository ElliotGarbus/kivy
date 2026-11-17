// Prototype 4: playback over time + seeking, not just one transferred
// frame. Prototypes 3a/3b each called TransferVideoFrame exactly once;
// a real provider calls it every on_frame tick for the life of the
// video, and seeking was never exercised at all. This closes that gap.
//
// Uses the WGL_NV_DX_interop2 bridge only (Prototype 3 already showed
// the two bridges behave identically once a frame reaches them -- the
// questions this prototype answers are about Media Foundation's own
// playback/seek behavior and about reusing one WGL registration across
// many frames, both of which are bridge-agnostic).
//
// Decodes mf_playback_test_clip.mp4 (mf_encode_animated_clip.cpp),
// whose blue channel carries a per-frame time marker (unlike
// mf_test_clip.mp4, where every frame is identical) -- so each
// delivered frame's recovered marker value tells us which frame it
// actually was, catching stuck/repeated/skipped frames that a
// single-frame test can't.
//
// Sequence:
//   1. Play the whole ~2s clip, calling TransferVideoFrame into ONE
//      destination texture registered with WGL ONCE up front, re-locking
//      it around each GL use -- tests whether one registration survives
//      many real decoded frames (not re-registered per frame).
//   2. After playback ends, seek back to ~1s and confirm the next
//      delivered frame's marker lands near the seek target, not wherever
//      playback happened to stop.

#define _CRT_SECURE_NO_WARNINGS
#define WIN32_LEAN_AND_MEAN

#include <windows.h>
#include <d3d11.h>
#include <dxgi.h>
#include <gl/gl.h>
#include <mfapi.h>
#include <mfidl.h>
#include <mfmediaengine.h>
#include <cstdio>
#include <cstdint>
#include <cmath>
#include <cstdlib>
#include <vector>
#include <string>
#include <atomic>

#pragma comment(lib, "d3d11.lib")
#pragma comment(lib, "dxgi.lib")
#pragma comment(lib, "opengl32.lib")
#pragma comment(lib, "gdi32.lib")
#pragma comment(lib, "user32.lib")
#pragma comment(lib, "mfplat.lib")
#pragma comment(lib, "mf.lib")
#pragma comment(lib, "mfuuid.lib")
#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "oleaut32.lib")

typedef HANDLE(WINAPI* PFNWGLDXOPENDEVICENVPROC)(void* dxDevice);
typedef BOOL(WINAPI* PFNWGLDXCLOSEDEVICENVPROC)(HANDLE hDevice);
typedef HANDLE(WINAPI* PFNWGLDXREGISTEROBJECTNVPROC)(
    HANDLE hDevice, void* dxObject, GLuint name, GLenum type, GLenum access);
typedef BOOL(WINAPI* PFNWGLDXUNREGISTEROBJECTNVPROC)(HANDLE hDevice, HANDLE hObject);
typedef BOOL(WINAPI* PFNWGLDXLOCKOBJECTSNVPROC)(HANDLE hDevice, GLint count, HANDLE* hObjects);
typedef BOOL(WINAPI* PFNWGLDXUNLOCKOBJECTSNVPROC)(HANDLE hDevice, GLint count, HANDLE* hObjects);
#define WGL_ACCESS_READ_ONLY_NV 0x0000
#define GL_CLAMP_TO_EDGE 0x812F

static PFNWGLDXOPENDEVICENVPROC pwglDXOpenDeviceNV;
static PFNWGLDXCLOSEDEVICENVPROC pwglDXCloseDeviceNV;
static PFNWGLDXREGISTEROBJECTNVPROC pwglDXRegisterObjectNV;
static PFNWGLDXUNREGISTEROBJECTNVPROC pwglDXUnregisterObjectNV;
static PFNWGLDXLOCKOBJECTSNVPROC pwglDXLockObjectsNV;
static PFNWGLDXUNLOCKOBJECTSNVPROC pwglDXUnlockObjectsNV;

static FILE* g_log = nullptr;
#define LOG(...) do { \
    fprintf(stdout, __VA_ARGS__); fprintf(stdout, "\n"); \
    if (g_log) { fprintf(g_log, __VA_ARGS__); fprintf(g_log, "\n"); fflush(g_log); } \
} while (0)

static bool load_wgl_dx_interop() {
    pwglDXOpenDeviceNV = (PFNWGLDXOPENDEVICENVPROC)wglGetProcAddress("wglDXOpenDeviceNV");
    pwglDXCloseDeviceNV = (PFNWGLDXCLOSEDEVICENVPROC)wglGetProcAddress("wglDXCloseDeviceNV");
    pwglDXRegisterObjectNV = (PFNWGLDXREGISTEROBJECTNVPROC)wglGetProcAddress("wglDXRegisterObjectNV");
    pwglDXUnregisterObjectNV = (PFNWGLDXUNREGISTEROBJECTNVPROC)wglGetProcAddress("wglDXUnregisterObjectNV");
    pwglDXLockObjectsNV = (PFNWGLDXLOCKOBJECTSNVPROC)wglGetProcAddress("wglDXLockObjectsNV");
    pwglDXUnlockObjectsNV = (PFNWGLDXUNLOCKOBJECTSNVPROC)wglGetProcAddress("wglDXUnlockObjectsNV");
    return pwglDXOpenDeviceNV && pwglDXCloseDeviceNV && pwglDXRegisterObjectNV &&
           pwglDXUnregisterObjectNV && pwglDXLockObjectsNV && pwglDXUnlockObjectsNV;
}

static void save_bmp(const char* path, int width, int height, const std::vector<uint8_t>& rgba) {
    int row_bytes = width * 3;
    int row_pad = (4 - (row_bytes % 4)) % 4;
    uint32_t data_size = (uint32_t)((row_bytes + row_pad) * height);
    uint32_t file_size = 54 + data_size;
    FILE* f = fopen(path, "wb");
    if (!f) { LOG("  save_bmp: could not open %s", path); return; }
    uint8_t header[54] = {0};
    header[0] = 'B'; header[1] = 'M';
    memcpy(&header[2], &file_size, 4);
    uint32_t off_bits = 54; memcpy(&header[10], &off_bits, 4);
    uint32_t dib_size = 40; memcpy(&header[14], &dib_size, 4);
    int32_t w = width; memcpy(&header[18], &w, 4);
    int32_t h = height; memcpy(&header[22], &h, 4);
    uint16_t planes = 1; memcpy(&header[26], &planes, 2);
    uint16_t bpp = 24; memcpy(&header[28], &bpp, 2);
    memcpy(&header[34], &data_size, 4);
    fwrite(header, 1, 54, f);
    std::vector<uint8_t> row(row_bytes + row_pad, 0);
    for (int y = 0; y < height; y++) {
        const uint8_t* src = &rgba[(size_t)y * width * 4];
        for (int x = 0; x < width; x++) {
            row[x * 3 + 0] = src[x * 4 + 2];
            row[x * 3 + 1] = src[x * 4 + 1];
            row[x * 3 + 2] = src[x * 4 + 0];
        }
        fwrite(row.data(), 1, row.size(), f);
    }
    fclose(f);
}

static LRESULT CALLBACK WndProc(HWND h, UINT m, WPARAM w, LPARAM l) {
    if (m == WM_CLOSE) { DestroyWindow(h); return 0; }
    if (m == WM_DESTROY) { PostQuitMessage(0); return 0; }
    return DefWindowProc(h, m, w, l);
}

static HWND create_gl_window(HDC* out_hdc, HGLRC* out_hglrc) {
    WNDCLASSW wc = {};
    wc.lpfnWndProc = WndProc;
    wc.hInstance = GetModuleHandleW(nullptr);
    wc.lpszClassName = L"KivyMfPlaybackProbe";
    RegisterClassW(&wc);
    HWND hwnd = CreateWindowExW(0, wc.lpszClassName, L"Kivy MF playback probe",
        WS_OVERLAPPEDWINDOW, CW_USEDEFAULT, CW_USEDEFAULT, 300, 300,
        nullptr, nullptr, wc.hInstance, nullptr);
    ShowWindow(hwnd, SW_SHOWNOACTIVATE);
    HDC hdc = GetDC(hwnd);
    PIXELFORMATDESCRIPTOR pfd = {};
    pfd.nSize = sizeof(pfd); pfd.nVersion = 1;
    pfd.dwFlags = PFD_DRAW_TO_WINDOW | PFD_SUPPORT_OPENGL | PFD_DOUBLEBUFFER;
    pfd.iPixelType = PFD_TYPE_RGBA; pfd.cColorBits = 32; pfd.cDepthBits = 24;
    int pf = ChoosePixelFormat(hdc, &pfd);
    SetPixelFormat(hdc, pf, &pfd);
    HGLRC hglrc = wglCreateContext(hdc);
    wglMakeCurrent(hdc, hglrc);
    *out_hdc = hdc; *out_hglrc = hglrc;
    return hwnd;
}

static void check_gl(const char* where) {
    GLenum e = glGetError();
    if (e != GL_NO_ERROR) LOG("    [gl error] after %s: 0x%04X", where, e);
}

static void fail(const char* where, HRESULT hr) { LOG("FAIL at %s: 0x%08lX", where, hr); exit(1); }
#define CHECK(expr, where) do { HRESULT _hr = (expr); if (FAILED(_hr)) fail(where, _hr); } while (0)

class EngineNotify : public IMFMediaEngineNotify {
public:
    std::atomic<ULONG> ref{1};
    HANDLE event_;
    std::atomic<bool> ended{false};
    std::atomic<bool> seeked{false};
    EngineNotify() { event_ = CreateEventW(nullptr, TRUE, FALSE, nullptr); }
    ~EngineNotify() { CloseHandle(event_); }
    STDMETHODIMP QueryInterface(REFIID riid, void** ppv) override {
        if (riid == __uuidof(IMFMediaEngineNotify) || riid == IID_IUnknown) {
            *ppv = static_cast<IMFMediaEngineNotify*>(this); AddRef(); return S_OK;
        }
        *ppv = nullptr; return E_NOINTERFACE;
    }
    STDMETHODIMP_(ULONG) AddRef() override { return ++ref; }
    STDMETHODIMP_(ULONG) Release() override { ULONG r = --ref; if (r == 0) delete this; return r; }
    STDMETHODIMP EventNotify(DWORD event, DWORD_PTR param1, DWORD param2) override {
        if (event == MF_MEDIA_ENGINE_EVENT_ENDED) ended = true;
        if (event == MF_MEDIA_ENGINE_EVENT_SEEKED) seeked = true;
        LOG("  [MF event] code=%lu param1=%llu param2=%lu", event, (unsigned long long)param1, param2);
        SetEvent(event_);
        return S_OK;
    }
};

// Draws destTex (already locked for GL access) as a quad, reads back the
// center pixel, and returns the frame index recovered from its blue
// channel (see mf_encode_animated_clip.cpp for the encoding).
static int draw_and_recover_frame_index(GLuint glTex, int W, int H, UINT32 frameCount, const char* saveAs) {
    glViewport(0, 0, W, H);
    glClearColor(0.f, 0.f, 0.f, 1.f);
    glClear(GL_COLOR_BUFFER_BIT);
    glEnable(GL_TEXTURE_2D);
    glBindTexture(GL_TEXTURE_2D, glTex);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glColor4f(1, 1, 1, 1);
    glBegin(GL_QUADS);
        glTexCoord2f(0, 0); glVertex2f(-1, -1);
        glTexCoord2f(1, 0); glVertex2f(1, -1);
        glTexCoord2f(1, 1); glVertex2f(1, 1);
        glTexCoord2f(0, 1); glVertex2f(-1, 1);
    glEnd();
    check_gl("glEnd(quad)");
    glFinish();

    std::vector<uint8_t> rgba((size_t)W * H * 4);
    glReadPixels(0, 0, W, H, GL_RGBA, GL_UNSIGNED_BYTE, rgba.data());
    check_gl("glReadPixels");
    size_t mid = ((size_t)(H / 2) * W + (W / 2)) * 4;
    uint8_t blueMarker = rgba[mid + 2]; // RGBA order -> index 2 is B
    int recovered = (int)lround((blueMarker * (double)(frameCount - 1)) / 255.0);

    if (saveAs) save_bmp(saveAs, W, H, rgba);
    return recovered;
}

int main() {
    g_log = fopen("mf_playback_log.txt", "w");
    LOG("=== Prototype 4: playback over time + seeking (WGL bridge) ===");

    HDC hdc; HGLRC hglrc;
    create_gl_window(&hdc, &hglrc);
    LOG("GL_RENDERER: %s", (const char*)glGetString(GL_RENDERER));
    if (!load_wgl_dx_interop()) { LOG("FAIL: WGL_NV_DX_interop2 not available"); return 1; }

    CHECK(CoInitializeEx(nullptr, COINIT_MULTITHREADED), "CoInitializeEx");
    CHECK(MFStartup(MF_VERSION), "MFStartup");

    IDXGIFactory1* factory = nullptr;
    CHECK(CreateDXGIFactory1(__uuidof(IDXGIFactory1), (void**)&factory), "CreateDXGIFactory1");
    IDXGIAdapter1* adapter = nullptr;
    {
        IDXGIAdapter1* a = nullptr;
        for (UINT i = 0; factory->EnumAdapters1(i, &a) != DXGI_ERROR_NOT_FOUND; i++) {
            DXGI_ADAPTER_DESC1 desc; a->GetDesc1(&desc);
            if (!adapter && wcsstr(desc.Description, L"NVIDIA")) { adapter = a; adapter->AddRef(); }
            a->Release();
        }
    }
    if (!adapter) { LOG("FAIL: no NVIDIA adapter found"); return 1; }

    ID3D11Device* dev = nullptr; ID3D11DeviceContext* ctx = nullptr; D3D_FEATURE_LEVEL fl;
    CHECK(D3D11CreateDevice(adapter, D3D_DRIVER_TYPE_UNKNOWN, nullptr,
          D3D11_CREATE_DEVICE_BGRA_SUPPORT | D3D11_CREATE_DEVICE_VIDEO_SUPPORT,
          nullptr, 0, D3D11_SDK_VERSION, &dev, &fl, &ctx), "D3D11CreateDevice");

    ID3D10Multithread* mt = nullptr;
    if (SUCCEEDED(ctx->QueryInterface(__uuidof(ID3D10Multithread), (void**)&mt))) {
        mt->SetMultithreadProtected(TRUE); mt->Release();
    }

    UINT resetToken = 0;
    IMFDXGIDeviceManager* dxgiMgr = nullptr;
    CHECK(MFCreateDXGIDeviceManager(&resetToken, &dxgiMgr), "MFCreateDXGIDeviceManager");
    CHECK(dxgiMgr->ResetDevice(dev, resetToken), "ResetDevice");

    EngineNotify* notify = new EngineNotify();
    IMFAttributes* attrs = nullptr;
    CHECK(MFCreateAttributes(&attrs, 3), "MFCreateAttributes");
    attrs->SetUnknown(MF_MEDIA_ENGINE_DXGI_MANAGER, dxgiMgr);
    attrs->SetUnknown(MF_MEDIA_ENGINE_CALLBACK, static_cast<IMFMediaEngineNotify*>(notify));
    attrs->SetUINT32(MF_MEDIA_ENGINE_VIDEO_OUTPUT_FORMAT, DXGI_FORMAT_B8G8R8A8_UNORM);

    IMFMediaEngineClassFactory* engineFactory = nullptr;
    CHECK(CoCreateInstance(CLSID_MFMediaEngineClassFactory, nullptr, CLSCTX_INPROC_SERVER,
          __uuidof(IMFMediaEngineClassFactory), (void**)&engineFactory), "CoCreateInstance(MediaEngine)");
    IMFMediaEngine* engine = nullptr;
    CHECK(engineFactory->CreateInstance(0, attrs, &engine), "CreateInstance(engine)");

    wchar_t fullPath[MAX_PATH];
    GetFullPathNameW(L"mf_playback_test_clip.mp4", MAX_PATH, fullPath, nullptr);
    std::wstring uri = L"file:///";
    for (wchar_t* p = fullPath; *p; p++) uri += (*p == L'\\') ? L'/' : *p;
    BSTR url = SysAllocString(uri.c_str());
    LOG("Setting source: %ls", url);
    CHECK(engine->SetSource(url), "SetSource");
    SysFreeString(url);

    ULONGLONG start = GetTickCount64();
    USHORT readyState = 0;
    while (true) {
        readyState = engine->GetReadyState();
        if (readyState >= MF_MEDIA_ENGINE_READY_HAVE_CURRENT_DATA) break;
        if (GetTickCount64() - start > 10000) { LOG("FAIL: timed out waiting for ready data"); return 1; }
        WaitForSingleObject(notify->event_, 100);
        ResetEvent(notify->event_);
    }
    LOG("Ready state reached: %u", readyState);

    const int W = 256, H = 256;
    const UINT32 FRAME_COUNT = 60; // must match mf_encode_animated_clip.cpp
    D3D11_TEXTURE2D_DESC td = {};
    td.Width = W; td.Height = H; td.MipLevels = 1; td.ArraySize = 1;
    td.Format = DXGI_FORMAT_B8G8R8A8_UNORM; td.SampleDesc.Count = 1;
    td.Usage = D3D11_USAGE_DEFAULT;
    td.BindFlags = D3D11_BIND_RENDER_TARGET | D3D11_BIND_SHADER_RESOURCE;
    ID3D11Texture2D* destTex = nullptr;
    CHECK(dev->CreateTexture2D(&td, nullptr, &destTex), "CreateTexture2D(dest)");

    // Register ONCE, up front -- this is the thing Prototype 3 never
    // tested: does one registration keep working as MF repeatedly
    // overwrites destTex's contents underneath it, frame after frame.
    HANDLE hDevice = pwglDXOpenDeviceNV(dev);
    if (!hDevice) { LOG("FAIL: wglDXOpenDeviceNV failed"); return 1; }
    GLuint glTex = 0;
    glGenTextures(1, &glTex);
    HANDLE hObj = pwglDXRegisterObjectNV(hDevice, destTex, glTex, GL_TEXTURE_2D, WGL_ACCESS_READ_ONLY_NV);
    if (!hObj) { LOG("FAIL: wglDXRegisterObjectNV failed"); return 1; }
    LOG("WGL interop object registered once, reused for every frame below.");

    CHECK(engine->Play(), "Play");

    LOG("--- Phase 1: playback over the whole clip ---");
    int framesDelivered = 0;
    int anomalies = 0;
    LONGLONG lastPts = -1;
    start = GetTickCount64();
    while (!notify->ended && GetTickCount64() - start < 15000) {
        LONGLONG pts = 0;
        HRESULT hr = engine->OnVideoStreamTick(&pts);
        if (hr == S_OK) {
            CHECK(engine->TransferVideoFrame(destTex, nullptr, nullptr, nullptr), "TransferVideoFrame");

            pwglDXLockObjectsNV(hDevice, 1, &hObj);
            int recovered = draw_and_recover_frame_index(glTex, W, H, FRAME_COUNT, nullptr);
            pwglDXUnlockObjectsNV(hDevice, 1, &hObj);

            double ptsSec = pts / 1.0e7;
            int expected = (int)lround(ptsSec * 30.0); // FPS = 30
            bool ptsRegressed = (lastPts >= 0 && pts < lastPts);
            bool markerOff = abs(recovered - expected) > 2; // allow for compression rounding
            if (ptsRegressed || markerOff) {
                anomalies++;
                LOG("  ANOMALY frame#%d pts=%.4fs expectedFrame=%d recoveredFrame=%d%s%s",
                    framesDelivered, ptsSec, expected, recovered,
                    ptsRegressed ? " [pts regressed]" : "",
                    markerOff ? " [marker mismatch]" : "");
            } else {
                LOG("  frame#%d pts=%.4fs expectedFrame=%d recoveredFrame=%d OK",
                    framesDelivered, ptsSec, expected, recovered);
            }
            lastPts = pts;
            framesDelivered++;
        } else {
            WaitForSingleObject(notify->event_, 10);
            ResetEvent(notify->event_);
        }
    }
    LOG("Phase 1 done: %d frames delivered, %d anomalies, ended=%s",
        framesDelivered, anomalies, notify->ended ? "yes" : "no (timed out)");

    LOG("--- Phase 2: seek back to ~1.0s and confirm the delivered frame matches ---");
    notify->seeked = false;
    CHECK(engine->SetCurrentTime(1.0), "SetCurrentTime(seek)");
    start = GetTickCount64();
    while (!notify->seeked && GetTickCount64() - start < 10000) {
        WaitForSingleObject(notify->event_, 50);
        ResetEvent(notify->event_);
    }
    if (!notify->seeked) { LOG("FAIL: seek did not complete (no SEEKED event) within timeout"); return 1; }
    LOG("Seek completed (SEEKED event received).");

    bool haveSeekFrame = false;
    int seekRecovered = -1;
    start = GetTickCount64();
    while (GetTickCount64() - start < 10000) {
        LONGLONG pts = 0;
        HRESULT hr = engine->OnVideoStreamTick(&pts);
        if (hr == S_OK) {
            CHECK(engine->TransferVideoFrame(destTex, nullptr, nullptr, nullptr), "TransferVideoFrame(seek)");
            pwglDXLockObjectsNV(hDevice, 1, &hObj);
            seekRecovered = draw_and_recover_frame_index(glTex, W, H, FRAME_COUNT, "mf_playback_seek_result.bmp");
            pwglDXUnlockObjectsNV(hDevice, 1, &hObj);
            LOG("Post-seek frame: pts=%.4fs recoveredFrame=%d (target was frame ~30, i.e. 1.0s)",
                pts / 1.0e7, seekRecovered);
            haveSeekFrame = true;
            break;
        }
        WaitForSingleObject(notify->event_, 20);
        ResetEvent(notify->event_);
    }
    if (!haveSeekFrame) { LOG("FAIL: no frame delivered after seek within timeout"); return 1; }

    int seekTargetFrame = 30;
    if (abs(seekRecovered - seekTargetFrame) > 2) {
        LOG("ANOMALY: post-seek frame (%d) is far from the seek target (%d)", seekRecovered, seekTargetFrame);
    } else {
        LOG("Seek landed correctly: recovered frame %d is within tolerance of target %d.", seekRecovered, seekTargetFrame);
    }

    pwglDXUnregisterObjectNV(hDevice, hObj);
    pwglDXCloseDeviceNV(hDevice);

    LOG("=== Done: %d frames over playback, %d anomalies, seek target=%d recovered=%d ===",
        framesDelivered, anomalies, seekTargetFrame, seekRecovered);
    fclose(g_log);
    return (anomalies > 0 || abs(seekRecovered - seekTargetFrame) > 2) ? 1 : 0;
}
