// Prototype 3a: real IMFMediaEngine decode -> WGL_NV_DX_interop2 bridge.
// Replaces Prototype 1's (wgl_dx_interop_test.cpp) synthetic D3D11 test
// texture with an actual decoded frame from mf_test_clip.mp4 (generated
// by mf_encode_test_clip.cpp), pushed through the exact same WGL/DX
// interop pipeline already proven in Prototype 1. The interop bridge
// itself is not the open question here -- Prototype 1 already answered
// that -- this closes out "does a real Media-Foundation-decoded frame
// survive the bridge the same way the synthetic one did."
//
// Media Foundation setup, in order:
//   1. Create a D3D11 device on the adapter the WGL context is actually
//      using (same adapter-matching requirement Prototype 1 found),
//      with D3D11_CREATE_DEVICE_VIDEO_SUPPORT, and mark it
//      multithread-protected (required when MF's internal decode
//      threads and this thread share one device).
//   2. Wrap it in an IMFDXGIDeviceManager via MFCreateDXGIDeviceManager
//      + ResetDevice -- this is the device Media Foundation will
//      allocate decoded-frame textures from.
//   3. Create an IMFMediaEngine bound to that device manager, point it
//      at the test clip, wait for enough data, call Play(), poll
//      OnVideoStreamTick for a ready frame, then TransferVideoFrame
//      into a destination texture (created on the SAME device).
//   4. Hand that destination texture to the WGL/DX interop pipeline.

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

// --- WGL_NV_DX_interop2: same hand-declared entry points as Prototype 1 ---
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
    wc.lpszClassName = L"KivyMfWglInteropProbe";
    RegisterClassW(&wc);
    HWND hwnd = CreateWindowExW(0, wc.lpszClassName, L"Kivy MF+WGL interop probe",
        WS_OVERLAPPEDWINDOW, CW_USEDEFAULT, CW_USEDEFAULT, 300, 300,
        nullptr, nullptr, wc.hInstance, nullptr);
    ShowWindow(hwnd, SW_SHOWNOACTIVATE); // SW_SHOWMINNOACTIVE minimizes -- see Prototype 1's writeup
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

static void verify_via_d3d_readback(ID3D11Device* dev, ID3D11DeviceContext* ctx,
                                     ID3D11Texture2D* tex, int w, int h) {
    D3D11_TEXTURE2D_DESC td = {};
    tex->GetDesc(&td);
    td.Usage = D3D11_USAGE_STAGING; td.BindFlags = 0; td.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    ID3D11Texture2D* staging = nullptr;
    HRESULT hr = dev->CreateTexture2D(&td, nullptr, &staging);
    if (FAILED(hr)) { LOG("  [d3d-readback] CreateTexture2D(staging) failed: 0x%08lX", hr); return; }
    ctx->CopyResource(staging, tex);
    D3D11_MAPPED_SUBRESOURCE mapped;
    hr = ctx->Map(staging, 0, D3D11_MAP_READ, 0, &mapped);
    if (FAILED(hr)) { LOG("  [d3d-readback] Map failed: 0x%08lX", hr); staging->Release(); return; }
    uint8_t* row0 = (uint8_t*)mapped.pData;
    uint8_t* midrow = (uint8_t*)mapped.pData + mapped.RowPitch * (h / 2);
    LOG("  [d3d-readback] decoded texture: pixel(0,0) BGRA = %u,%u,%u,%u ; pixel(w/2,h/2) BGRA = %u,%u,%u,%u",
        row0[0], row0[1], row0[2], row0[3],
        midrow[(w/2)*4+0], midrow[(w/2)*4+1], midrow[(w/2)*4+2], midrow[(w/2)*4+3]);
    ctx->Unmap(staging, 0);
    staging->Release();
}

static void check_gl(const char* where) {
    GLenum e = glGetError();
    if (e != GL_NO_ERROR) LOG("  [gl error] after %s: 0x%04X", where, e);
}

static void fail(const char* where, HRESULT hr) { LOG("FAIL at %s: 0x%08lX", where, hr); exit(1); }
#define CHECK(expr, where) do { HRESULT _hr = (expr); if (FAILED(_hr)) fail(where, _hr); } while (0)

class EngineNotify : public IMFMediaEngineNotify {
public:
    std::atomic<ULONG> ref{1};
    HANDLE event_;
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
        LOG("  [MF event] code=%lu param1=%llu param2=%lu", event, (unsigned long long)param1, param2);
        SetEvent(event_);
        return S_OK;
    }
};

int main() {
    g_log = fopen("mf_decode_wgl_log.txt", "w");
    LOG("=== Prototype 3a: IMFMediaEngine decode -> WGL_NV_DX_interop2 ===");

    HDC hdc; HGLRC hglrc;
    create_gl_window(&hdc, &hglrc);
    LOG("GL_RENDERER: %s", (const char*)glGetString(GL_RENDERER));
    if (!load_wgl_dx_interop()) { LOG("FAIL: WGL_NV_DX_interop2 not available"); return 1; }

    // The WGL context above landed on whichever adapter the driver
    // picked for the first available WGL context (Prototype 1 confirmed
    // this is the NVIDIA RTX 2060 on this machine); find that same
    // adapter by name rather than assuming index 0, since DXGI's
    // enumeration order is not guaranteed to match WGL's choice.
    CHECK(CoInitializeEx(nullptr, COINIT_MULTITHREADED), "CoInitializeEx");
    CHECK(MFStartup(MF_VERSION), "MFStartup");

    IDXGIFactory1* factory = nullptr;
    CHECK(CreateDXGIFactory1(__uuidof(IDXGIFactory1), (void**)&factory), "CreateDXGIFactory1");
    IDXGIAdapter1* adapter = nullptr;
    {
        IDXGIAdapter1* a = nullptr;
        for (UINT i = 0; factory->EnumAdapters1(i, &a) != DXGI_ERROR_NOT_FOUND; i++) {
            DXGI_ADAPTER_DESC1 desc; a->GetDesc1(&desc);
            LOG("Adapter %u: %ls", i, desc.Description);
            if (!adapter && wcsstr(desc.Description, L"NVIDIA")) { adapter = a; adapter->AddRef(); }
            a->Release();
        }
    }
    if (!adapter) { LOG("FAIL: no NVIDIA adapter found"); return 1; }

    ID3D11Device* dev = nullptr; ID3D11DeviceContext* ctx = nullptr; D3D_FEATURE_LEVEL fl;
    CHECK(D3D11CreateDevice(adapter, D3D_DRIVER_TYPE_UNKNOWN, nullptr,
          D3D11_CREATE_DEVICE_BGRA_SUPPORT | D3D11_CREATE_DEVICE_VIDEO_SUPPORT,
          nullptr, 0, D3D11_SDK_VERSION, &dev, &fl, &ctx), "D3D11CreateDevice");

    // Required: MF decodes on its own internal threads, which touch this
    // same device concurrently with this thread.
    ID3D10Multithread* mt = nullptr;
    if (SUCCEEDED(ctx->QueryInterface(__uuidof(ID3D10Multithread), (void**)&mt))) {
        mt->SetMultithreadProtected(TRUE);
        mt->Release();
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
    GetFullPathNameW(L"mf_test_clip.mp4", MAX_PATH, fullPath, nullptr);
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
        if (GetTickCount64() - start > 10000) { LOG("FAIL: timed out waiting for ready data (readyState=%u)", readyState); return 1; }
        WaitForSingleObject(notify->event_, 100);
        ResetEvent(notify->event_);
    }
    LOG("Ready state reached: %u", readyState);

    CHECK(engine->Play(), "Play");

    bool haveFrame = false;
    LONGLONG pts = 0;
    start = GetTickCount64();
    while (GetTickCount64() - start < 10000) {
        HRESULT hr = engine->OnVideoStreamTick(&pts);
        if (hr == S_OK) { haveFrame = true; break; }
        WaitForSingleObject(notify->event_, 50);
        ResetEvent(notify->event_);
    }
    if (!haveFrame) { LOG("FAIL: no video frame became ready within timeout"); return 1; }
    LOG("Video frame ready at pts=%lld", pts);

    const int W = 256, H = 256;
    D3D11_TEXTURE2D_DESC td = {};
    td.Width = W; td.Height = H; td.MipLevels = 1; td.ArraySize = 1;
    td.Format = DXGI_FORMAT_B8G8R8A8_UNORM; td.SampleDesc.Count = 1;
    td.Usage = D3D11_USAGE_DEFAULT;
    td.BindFlags = D3D11_BIND_RENDER_TARGET | D3D11_BIND_SHADER_RESOURCE;
    ID3D11Texture2D* destTex = nullptr;
    CHECK(dev->CreateTexture2D(&td, nullptr, &destTex), "CreateTexture2D(dest)");

    RECT destRect = {0, 0, W, H};
    CHECK(engine->TransferVideoFrame(destTex, nullptr, &destRect, nullptr), "TransferVideoFrame");
    LOG("TransferVideoFrame OK -- decoded frame is now in our D3D11 texture.");
    verify_via_d3d_readback(dev, ctx, destTex, W, H);

    // From here: identical to Prototype 1, just with destTex in place of
    // the synthetic test texture.
    HANDLE hDevice = pwglDXOpenDeviceNV(dev);
    if (!hDevice) { LOG("FAIL: wglDXOpenDeviceNV failed (GetLastError=%lu)", GetLastError()); return 1; }
    GLuint glTex = 0;
    glGenTextures(1, &glTex);
    HANDLE hObj = pwglDXRegisterObjectNV(hDevice, destTex, glTex, GL_TEXTURE_2D, WGL_ACCESS_READ_ONLY_NV);
    if (!hObj) { LOG("FAIL: wglDXRegisterObjectNV failed (GetLastError=%lu)", GetLastError()); return 1; }
    if (!pwglDXLockObjectsNV(hDevice, 1, &hObj)) { LOG("FAIL: wglDXLockObjectsNV failed"); return 1; }

    glViewport(0, 0, W, H);
    glClearColor(0.f, 0.f, 0.f, 1.f);
    glClear(GL_COLOR_BUFFER_BIT);
    glEnable(GL_TEXTURE_2D);
    glBindTexture(GL_TEXTURE_2D, glTex);
    check_gl("glBindTexture");
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
    size_t mid = ((size_t)(H/2) * W + (W/2)) * 4;
    LOG("  GL readback center pixel RGBA = %u,%u,%u,%u ; corner(0,0) RGBA = %u,%u,%u,%u",
        rgba[mid], rgba[mid+1], rgba[mid+2], rgba[mid+3],
        rgba[0], rgba[1], rgba[2], rgba[3]);
    save_bmp("mf_decode_wgl_result.bmp", W, H, rgba);
    LOG("Saved mf_decode_wgl_result.bmp for visual verification.");

    SwapBuffers(hdc);
    pwglDXUnlockObjectsNV(hDevice, 1, &hObj);
    pwglDXUnregisterObjectNV(hDevice, hObj);
    pwglDXCloseDeviceNV(hDevice);

    LOG("=== Done ===");
    fclose(g_log);
    return 0;
}
