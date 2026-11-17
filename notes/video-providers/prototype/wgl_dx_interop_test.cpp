// Native prototype: tests the WGL_NV_DX_interop2 bridge that a Windows
// video provider would use under Kivy's DEFAULT GL backend (glew / sdl3,
// native desktop OpenGL via WGL) to share a Media-Foundation-decoded
// D3D11 texture into GL with no CPU copy.
//
// This does NOT decode real video yet -- it stands in a D3D11 texture
// filled with a recognizable test pattern, because the open question
// from the spike is specifically about the GPU interop bridge (does
// wglDXRegisterObjectNV/wglDXLockObjectsNV actually work, and does GPU
// adapter choice matter), not about Media Foundation's decode API
// (well-documented, low risk by comparison). See
// notes/video-providers/windows-spike.md.
//
// For each DXGI adapter on the machine, this:
//   1. creates a D3D11 device against that adapter
//   2. creates and fills a test D3D11 texture
//   3. opens a WGL/DX interop device, registers the texture as a GL
//      texture, locks it
//   4. draws it as a textured quad in the (already-created, adapter-
//      independent) WGL context
//   5. reads the framebuffer back and saves it as a BMP
//
// so success/failure and the rendered image are both visible per adapter,
// directly answering "does this need to match the GPU the GL context is
// actually using" on a real multi-GPU machine.

#define _CRT_SECURE_NO_WARNINGS
#define WIN32_LEAN_AND_MEAN

#include <windows.h>
#include <d3d11.h>
#include <dxgi.h>
#include <gl/gl.h>
#include <cstdio>
#include <cstdint>
#include <vector>
#include <string>

#pragma comment(lib, "d3d11.lib")
#pragma comment(lib, "dxgi.lib")
#pragma comment(lib, "opengl32.lib")
#pragma comment(lib, "gdi32.lib")
#pragma comment(lib, "user32.lib")

// ---------------------------------------------------------------------
// WGL_NV_DX_interop2 is not shipped as a header by the Windows SDK (it's
// a Khronos/OpenGL-registry extension, not a Microsoft one). Signatures
// and tokens below are hand-declared from the published extension spec;
// all entry points are resolved at runtime via wglGetProcAddress, so no
// import lib / official header is needed to build or link this.
// ---------------------------------------------------------------------
typedef HANDLE(WINAPI* PFNWGLDXOPENDEVICENVPROC)(void* dxDevice);
typedef BOOL(WINAPI* PFNWGLDXCLOSEDEVICENVPROC)(HANDLE hDevice);
typedef HANDLE(WINAPI* PFNWGLDXREGISTEROBJECTNVPROC)(
    HANDLE hDevice, void* dxObject, GLuint name, GLenum type, GLenum access);
typedef BOOL(WINAPI* PFNWGLDXUNREGISTEROBJECTNVPROC)(HANDLE hDevice, HANDLE hObject);
typedef BOOL(WINAPI* PFNWGLDXLOCKOBJECTSNVPROC)(HANDLE hDevice, GLint count, HANDLE* hObjects);
typedef BOOL(WINAPI* PFNWGLDXUNLOCKOBJECTSNVPROC)(HANDLE hDevice, GLint count, HANDLE* hObjects);

#define WGL_ACCESS_READ_ONLY_NV 0x0000
#define WGL_ACCESS_READ_WRITE_NV 0x0001
// Windows SDK's gl.h only declares OpenGL 1.1; this token is from 1.2.
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

// Minimal 24-bit BMP writer, no external deps. `rgba` is a glReadPixels
// buffer (GL row order: row 0 = bottom scanline), which is already the
// row order BMP wants for a positive-height header, so rows are written
// through unchanged.
static void save_bmp(const char* path, int width, int height,
                      const std::vector<uint8_t>& rgba) {
    int row_bytes = width * 3;
    int row_pad = (4 - (row_bytes % 4)) % 4;
    uint32_t data_size = (uint32_t)((row_bytes + row_pad) * height);
    uint32_t file_size = 54 + data_size;

    FILE* f = fopen(path, "wb");
    if (!f) { LOG("  save_bmp: could not open %s for write", path); return; }

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
            row[x * 3 + 0] = src[x * 4 + 2]; // B
            row[x * 3 + 1] = src[x * 4 + 1]; // G
            row[x * 3 + 2] = src[x * 4 + 0]; // R
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
    wc.lpszClassName = L"KivyWglDxInteropProbe";
    RegisterClassW(&wc);

    HWND hwnd = CreateWindowExW(
        0, wc.lpszClassName, L"Kivy WGL/DX interop probe",
        WS_OVERLAPPEDWINDOW, CW_USEDEFAULT, CW_USEDEFAULT, 300, 300,
        nullptr, nullptr, wc.hInstance, nullptr);
    ShowWindow(hwnd, SW_SHOWNOACTIVATE);

    HDC hdc = GetDC(hwnd);
    PIXELFORMATDESCRIPTOR pfd = {};
    pfd.nSize = sizeof(pfd);
    pfd.nVersion = 1;
    pfd.dwFlags = PFD_DRAW_TO_WINDOW | PFD_SUPPORT_OPENGL | PFD_DOUBLEBUFFER;
    pfd.iPixelType = PFD_TYPE_RGBA;
    pfd.cColorBits = 32;
    pfd.cDepthBits = 24;
    int pf = ChoosePixelFormat(hdc, &pfd);
    SetPixelFormat(hdc, pf, &pfd);

    HGLRC hglrc = wglCreateContext(hdc);
    wglMakeCurrent(hdc, hglrc);

    *out_hdc = hdc;
    *out_hglrc = hglrc;
    return hwnd;
}

static void fill_test_pattern(ID3D11DeviceContext* ctx, ID3D11Texture2D* tex,
                               int w, int h) {
    std::vector<uint32_t> pixels((size_t)w * h);
    for (int y = 0; y < h; y++) {
        for (int x = 0; x < w; x++) {
            uint8_t r = (uint8_t)((x * 255) / (w - 1));
            uint8_t g = (uint8_t)((y * 255) / (h - 1));
            uint8_t b = 0x80;
            uint8_t a = 0xFF;
            // DXGI_FORMAT_B8G8R8A8_UNORM memory order is B,G,R,A per texel;
            // on this little-endian target that's exactly what packing
            // (a<<24)|(r<<16)|(g<<8)|b produces in memory.
            pixels[(size_t)y * w + x] = ((uint32_t)a << 24) | ((uint32_t)r << 16) |
                                        ((uint32_t)g << 8) | b;
        }
    }
    ctx->UpdateSubresource(tex, 0, nullptr, pixels.data(), w * 4, 0);
    ctx->Flush();
}

// Independent check, bypassing WGL/GL entirely: copy the D3D11 texture to
// a CPU-readable staging texture and report a sample pixel. Isolates
// "did the D3D11 texture actually get filled" from "did the GL-side
// interop sampling work".
static void verify_via_d3d_readback(ID3D11Device* dev, ID3D11DeviceContext* ctx,
                                     ID3D11Texture2D* tex, int w, int h) {
    D3D11_TEXTURE2D_DESC td = {};
    tex->GetDesc(&td);
    td.Usage = D3D11_USAGE_STAGING;
    td.BindFlags = 0;
    td.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    ID3D11Texture2D* staging = nullptr;
    HRESULT hr = dev->CreateTexture2D(&td, nullptr, &staging);
    if (FAILED(hr)) { LOG("  [d3d-readback] CreateTexture2D(staging) failed: 0x%08lX", hr); return; }
    ctx->CopyResource(staging, tex);
    D3D11_MAPPED_SUBRESOURCE mapped;
    hr = ctx->Map(staging, 0, D3D11_MAP_READ, 0, &mapped);
    if (FAILED(hr)) { LOG("  [d3d-readback] Map failed: 0x%08lX", hr); staging->Release(); return; }
    uint8_t* row0 = (uint8_t*)mapped.pData;
    uint8_t* midrow = (uint8_t*)mapped.pData + mapped.RowPitch * (h / 2);
    LOG("  [d3d-readback] pixel(0,0) BGRA = %u,%u,%u,%u ; pixel(w/2,h/2) BGRA = %u,%u,%u,%u",
        row0[0], row0[1], row0[2], row0[3],
        midrow[(w/2)*4+0], midrow[(w/2)*4+1], midrow[(w/2)*4+2], midrow[(w/2)*4+3]);
    ctx->Unmap(staging, 0);
    staging->Release();
}

static void check_gl(const char* where) {
    GLenum e = glGetError();
    if (e != GL_NO_ERROR) LOG("  [gl error] after %s: 0x%04X", where, e);
}

int main() {
    g_log = fopen("wgl_dx_interop_log.txt", "w");
    LOG("=== WGL_NV_DX_interop2 bridge probe ===");

    HDC hdc; HGLRC hglrc;
    HWND hwnd = create_gl_window(&hdc, &hglrc);

    LOG("GL_VENDOR:   %s", (const char*)glGetString(GL_VENDOR));
    LOG("GL_RENDERER: %s", (const char*)glGetString(GL_RENDERER));
    LOG("GL_VERSION:  %s", (const char*)glGetString(GL_VERSION));

    if (!load_wgl_dx_interop()) {
        LOG("FAIL: WGL_NV_DX_interop2 entry points not found via wglGetProcAddress "
            "(driver does not expose the extension under this GL context).");
        return 1;
    }
    LOG("WGL_NV_DX_interop2 entry points resolved OK.");

    IDXGIFactory1* factory = nullptr;
    HRESULT hr = CreateDXGIFactory1(__uuidof(IDXGIFactory1), (void**)&factory);
    if (FAILED(hr)) { LOG("FAIL: CreateDXGIFactory1 0x%08lX", hr); return 1; }

    std::vector<IDXGIAdapter1*> adapters;
    IDXGIAdapter1* a = nullptr;
    for (UINT i = 0; factory->EnumAdapters1(i, &a) != DXGI_ERROR_NOT_FOUND; i++) {
        DXGI_ADAPTER_DESC1 desc;
        a->GetDesc1(&desc);
        LOG("Adapter %u: %ls (VRAM=%llu MB)", i, desc.Description,
            (unsigned long long)(desc.DedicatedVideoMemory / (1024 * 1024)));
        adapters.push_back(a);
    }

    const int W = 256, H = 256;

    for (size_t i = 0; i < adapters.size(); i++) {
        LOG("--- Adapter %zu ---", i);
        ID3D11Device* dev = nullptr;
        ID3D11DeviceContext* ctx = nullptr;
        D3D_FEATURE_LEVEL fl;
        HRESULT hr2 = D3D11CreateDevice(
            adapters[i], D3D_DRIVER_TYPE_UNKNOWN, nullptr,
            D3D11_CREATE_DEVICE_BGRA_SUPPORT, nullptr, 0,
            D3D11_SDK_VERSION, &dev, &fl, &ctx);
        if (FAILED(hr2)) {
            LOG("  D3D11CreateDevice failed: 0x%08lX", hr2);
            continue;
        }

        D3D11_TEXTURE2D_DESC td = {};
        td.Width = W; td.Height = H; td.MipLevels = 1; td.ArraySize = 1;
        td.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
        td.SampleDesc.Count = 1;
        td.Usage = D3D11_USAGE_DEFAULT;
        td.BindFlags = D3D11_BIND_RENDER_TARGET | D3D11_BIND_SHADER_RESOURCE;

        ID3D11Texture2D* tex = nullptr;
        hr2 = dev->CreateTexture2D(&td, nullptr, &tex);
        if (FAILED(hr2)) {
            LOG("  CreateTexture2D failed: 0x%08lX", hr2);
            ctx->Release(); dev->Release();
            continue;
        }
        fill_test_pattern(ctx, tex, W, H);
        verify_via_d3d_readback(dev, ctx, tex, W, H);

        HANDLE hDevice = pwglDXOpenDeviceNV(dev);
        if (!hDevice) {
            LOG("  wglDXOpenDeviceNV FAILED (GetLastError=%lu) -- this is the "
                "adapter-mismatch failure mode the spike flagged.", GetLastError());
            tex->Release(); ctx->Release(); dev->Release();
            continue;
        }
        LOG("  wglDXOpenDeviceNV OK");

        GLuint glTex = 0;
        glGenTextures(1, &glTex);
        HANDLE hObj = pwglDXRegisterObjectNV(hDevice, tex, glTex, GL_TEXTURE_2D,
                                              WGL_ACCESS_READ_ONLY_NV);
        if (!hObj) {
            LOG("  wglDXRegisterObjectNV FAILED (GetLastError=%lu)", GetLastError());
            pwglDXCloseDeviceNV(hDevice);
            tex->Release(); ctx->Release(); dev->Release();
            continue;
        }
        LOG("  wglDXRegisterObjectNV OK");

        BOOL lockOk = pwglDXLockObjectsNV(hDevice, 1, &hObj);
        LOG("  wglDXLockObjectsNV: %s", lockOk ? "OK" : "FAILED");

        if (lockOk) {
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
            GLint tex_w = -1, tex_h = -1;
            glGetTexLevelParameteriv(GL_TEXTURE_2D, 0, GL_TEXTURE_WIDTH, &tex_w);
            glGetTexLevelParameteriv(GL_TEXTURE_2D, 0, GL_TEXTURE_HEIGHT, &tex_h);
            LOG("  GL sees bound texture as %dx%d", tex_w, tex_h);
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
            LOG("  readback center pixel RGBA = %u,%u,%u,%u ; corner(0,0) RGBA = %u,%u,%u,%u",
                rgba[mid], rgba[mid+1], rgba[mid+2], rgba[mid+3],
                rgba[0], rgba[1], rgba[2], rgba[3]);

            char path[256];
            sprintf(path, "wgl_interop_adapter%zu.bmp", i);
            save_bmp(path, W, H, rgba);
            LOG("  Saved %s for visual verification.", path);

            SwapBuffers(hdc);
            pwglDXUnlockObjectsNV(hDevice, 1, &hObj);
        }

        pwglDXUnregisterObjectNV(hDevice, hObj);
        pwglDXCloseDeviceNV(hDevice);
        tex->Release();
        ctx->Release();
        dev->Release();
    }

    LOG("=== Done ===");
    fclose(g_log);
    return 0;
}
