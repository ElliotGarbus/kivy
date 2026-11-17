// Prototype 3b: real IMFMediaEngine decode -> EGL_ANGLE_d3d_texture_client_buffer
// bridge. Counterpart to mf_decode_wgl_test.cpp (Prototype 3a), for the
// angle_sdl3 backend path instead of glew/sdl3. Combines Prototype 2's
// EGL/ANGLE device-identity finding (Media Foundation must decode onto
// ANGLE's OWN internal ID3D11Device, queried via EGL_ANGLE_device_d3d --
// a same-adapter-but-different-device target fails) with Prototype 3a's
// real decode pipeline.
//
// No window needed (EGL pbuffers are offscreen), same as Prototype 2.

#define _CRT_SECURE_NO_WARNINGS
#define WIN32_LEAN_AND_MEAN

#include <windows.h>
#include <d3d11.h>
#include <dxgi.h>
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
#pragma comment(lib, "mfplat.lib")
#pragma comment(lib, "mf.lib")
#pragma comment(lib, "mfuuid.lib")
#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "oleaut32.lib")

// --- Hand-declared EGL / GLES2 (same as Prototype 2) ---
typedef void* EGLDisplay; typedef void* EGLConfig; typedef void* EGLSurface;
typedef void* EGLContext; typedef void* EGLClientBuffer; typedef void* EGLDeviceEXT;
typedef int32_t EGLint; typedef unsigned int EGLBoolean; typedef unsigned int EGLenum;
typedef intptr_t EGLAttrib;
typedef unsigned int GLenum; typedef unsigned int GLuint; typedef int GLint;
typedef int GLsizei; typedef unsigned int GLbitfield; typedef float GLfloat;
typedef unsigned char GLboolean; typedef unsigned char GLubyte; typedef char GLchar;
typedef intptr_t GLsizeiptr;

#define EGL_NO_DISPLAY ((EGLDisplay)0)
#define EGL_NO_CONTEXT ((EGLContext)0)
#define EGL_NO_SURFACE ((EGLSurface)0)
#define EGL_DEFAULT_DISPLAY ((void*)0)
#define EGL_TRUE 1
#define EGL_FALSE 0
#define EGL_NONE 0x3038
#define EGL_SUCCESS 0x3000
#define EGL_SURFACE_TYPE 0x3033
#define EGL_PBUFFER_BIT 0x0001
#define EGL_RENDERABLE_TYPE 0x3040
#define EGL_OPENGL_ES2_BIT 0x0004
#define EGL_RED_SIZE 0x3024
#define EGL_GREEN_SIZE 0x3023
#define EGL_BLUE_SIZE 0x3022
#define EGL_ALPHA_SIZE 0x3021
#define EGL_WIDTH 0x3057
#define EGL_HEIGHT 0x3056
#define EGL_TEXTURE_FORMAT 0x3080
#define EGL_TEXTURE_RGBA 0x305E
#define EGL_TEXTURE_TARGET 0x3081
#define EGL_TEXTURE_2D_ATTR 0x305F
#define EGL_BACK_BUFFER 0x3084
#define EGL_CONTEXT_CLIENT_VERSION 0x3098
#define EGL_PLATFORM_ANGLE_ANGLE 0x3202
#define EGL_PLATFORM_ANGLE_TYPE_ANGLE 0x3203
#define EGL_PLATFORM_ANGLE_TYPE_D3D11_ANGLE 0x3208
#define EGL_DEVICE_EXT 0x322C
#define EGL_D3D11_DEVICE_ANGLE 0x33A1
#define EGL_D3D_TEXTURE_ANGLE 0x33A3

#define GL_NO_ERROR 0
#define GL_FALSE_ 0
#define GL_TEXTURE_2D 0x0DE1
#define GL_TEXTURE0 0x84C0
#define GL_TEXTURE_MIN_FILTER 0x2801
#define GL_TEXTURE_MAG_FILTER 0x2800
#define GL_TEXTURE_WRAP_S 0x2802
#define GL_TEXTURE_WRAP_T 0x2803
#define GL_NEAREST 0x2600
#define GL_CLAMP_TO_EDGE 0x812F
#define GL_COLOR_BUFFER_BIT 0x4000
#define GL_RGBA 0x1908
#define GL_UNSIGNED_BYTE 0x1401
#define GL_FLOAT 0x1406
#define GL_ARRAY_BUFFER 0x8892
#define GL_STATIC_DRAW 0x88E4
#define GL_TRIANGLE_STRIP 0x0005
#define GL_VERTEX_SHADER 0x8B31
#define GL_FRAGMENT_SHADER 0x8B30
#define GL_COMPILE_STATUS 0x8B81
#define GL_LINK_STATUS 0x8B82
#define GL_VENDOR 0x1F00
#define GL_RENDERER 0x1F01
#define GL_VERSION 0x1F02

typedef EGLBoolean (WINAPI *PFNEGLINITIALIZEPROC)(EGLDisplay, EGLint*, EGLint*);
typedef EGLBoolean (WINAPI *PFNEGLCHOOSECONFIGPROC)(EGLDisplay, const EGLint*, EGLConfig*, EGLint, EGLint*);
typedef EGLContext (WINAPI *PFNEGLCREATECONTEXTPROC)(EGLDisplay, EGLConfig, EGLContext, const EGLint*);
typedef EGLBoolean (WINAPI *PFNEGLMAKECURRENTPROC)(EGLDisplay, EGLSurface, EGLSurface, EGLContext);
typedef EGLSurface (WINAPI *PFNEGLCREATEPBUFFERSURFACEPROC)(EGLDisplay, EGLConfig, const EGLint*);
typedef EGLSurface (WINAPI *PFNEGLCREATEPBUFFERFROMCLIENTBUFFERPROC)(EGLDisplay, EGLenum, EGLClientBuffer, EGLConfig, const EGLint*);
typedef EGLBoolean (WINAPI *PFNEGLBINDTEXIMAGEPROC)(EGLDisplay, EGLSurface, EGLint);
typedef EGLBoolean (WINAPI *PFNEGLRELEASETEXIMAGEPROC)(EGLDisplay, EGLSurface, EGLint);
typedef EGLBoolean (WINAPI *PFNEGLDESTROYSURFACEPROC)(EGLDisplay, EGLSurface);
typedef EGLBoolean (WINAPI *PFNEGLDESTROYCONTEXTPROC)(EGLDisplay, EGLContext);
typedef EGLBoolean (WINAPI *PFNEGLTERMINATEPROC)(EGLDisplay);
typedef EGLint (WINAPI *PFNEGLGETERRORPROC)(void);
typedef void* (WINAPI *PFNEGLGETPROCADDRESSPROC)(const char*);
typedef EGLDisplay (WINAPI *PFNEGLGETPLATFORMDISPLAYEXTPROC)(EGLenum, void*, const EGLint*);
typedef EGLBoolean (WINAPI *PFNEGLQUERYDISPLAYATTRIBEXTPROC)(EGLDisplay, EGLint, EGLAttrib*);
typedef EGLBoolean (WINAPI *PFNEGLQUERYDEVICEATTRIBEXTPROC)(EGLDeviceEXT, EGLint, EGLAttrib*);

typedef void (WINAPI *PFNGLGENTEXTURESPROC)(GLsizei, GLuint*);
typedef void (WINAPI *PFNGLDELETETEXTURESPROC)(GLsizei, const GLuint*);
typedef void (WINAPI *PFNGLBINDTEXTUREPROC)(GLenum, GLuint);
typedef void (WINAPI *PFNGLTEXPARAMETERIPROC)(GLenum, GLenum, GLint);
typedef void (WINAPI *PFNGLVIEWPORTPROC)(GLint, GLint, GLsizei, GLsizei);
typedef void (WINAPI *PFNGLCLEARCOLORPROC)(GLfloat, GLfloat, GLfloat, GLfloat);
typedef void (WINAPI *PFNGLCLEARPROC)(GLbitfield);
typedef void (WINAPI *PFNGLREADPIXELSPROC)(GLint, GLint, GLsizei, GLsizei, GLenum, GLenum, void*);
typedef GLenum (WINAPI *PFNGLGETERRORPROC)(void);
typedef void (WINAPI *PFNGLFINISHPROC)(void);
typedef const GLubyte* (WINAPI *PFNGLGETSTRINGPROC)(GLenum);
typedef GLuint (WINAPI *PFNGLCREATESHADERPROC)(GLenum);
typedef void (WINAPI *PFNGLSHADERSOURCEPROC)(GLuint, GLsizei, const GLchar* const*, const GLint*);
typedef void (WINAPI *PFNGLCOMPILESHADERPROC)(GLuint);
typedef void (WINAPI *PFNGLGETSHADERIVPROC)(GLuint, GLenum, GLint*);
typedef void (WINAPI *PFNGLGETSHADERINFOLOGPROC)(GLuint, GLsizei, GLsizei*, GLchar*);
typedef GLuint (WINAPI *PFNGLCREATEPROGRAMPROC)(void);
typedef void (WINAPI *PFNGLATTACHSHADERPROC)(GLuint, GLuint);
typedef void (WINAPI *PFNGLLINKPROGRAMPROC)(GLuint);
typedef void (WINAPI *PFNGLGETPROGRAMIVPROC)(GLuint, GLenum, GLint*);
typedef void (WINAPI *PFNGLGETPROGRAMINFOLOGPROC)(GLuint, GLsizei, GLsizei*, GLchar*);
typedef void (WINAPI *PFNGLUSEPROGRAMPROC)(GLuint);
typedef void (WINAPI *PFNGLGENBUFFERSPROC)(GLsizei, GLuint*);
typedef void (WINAPI *PFNGLBINDBUFFERPROC)(GLenum, GLuint);
typedef void (WINAPI *PFNGLBUFFERDATAPROC)(GLenum, GLsizeiptr, const void*, GLenum);
typedef void (WINAPI *PFNGLVERTEXATTRIBPOINTERPROC)(GLuint, GLint, GLenum, GLboolean, GLsizei, const void*);
typedef void (WINAPI *PFNGLENABLEVERTEXATTRIBARRAYPROC)(GLuint);
typedef void (WINAPI *PFNGLDRAWARRAYSPROC)(GLenum, GLint, GLsizei);
typedef GLint (WINAPI *PFNGLGETATTRIBLOCATIONPROC)(GLuint, const GLchar*);
typedef GLint (WINAPI *PFNGLGETUNIFORMLOCATIONPROC)(GLuint, const GLchar*);
typedef void (WINAPI *PFNGLUNIFORM1IPROC)(GLint, GLint);
typedef void (WINAPI *PFNGLACTIVETEXTUREPROC)(GLenum);

static PFNEGLINITIALIZEPROC peglInitialize;
static PFNEGLCHOOSECONFIGPROC peglChooseConfig;
static PFNEGLCREATECONTEXTPROC peglCreateContext;
static PFNEGLMAKECURRENTPROC peglMakeCurrent;
static PFNEGLCREATEPBUFFERSURFACEPROC peglCreatePbufferSurface;
static PFNEGLCREATEPBUFFERFROMCLIENTBUFFERPROC peglCreatePbufferFromClientBuffer;
static PFNEGLBINDTEXIMAGEPROC peglBindTexImage;
static PFNEGLRELEASETEXIMAGEPROC peglReleaseTexImage;
static PFNEGLDESTROYSURFACEPROC peglDestroySurface;
static PFNEGLDESTROYCONTEXTPROC peglDestroyContext;
static PFNEGLTERMINATEPROC peglTerminate;
static PFNEGLGETERRORPROC peglGetError;
static PFNEGLGETPROCADDRESSPROC peglGetProcAddress;
static PFNEGLGETPLATFORMDISPLAYEXTPROC peglGetPlatformDisplayEXT;
static PFNEGLQUERYDISPLAYATTRIBEXTPROC peglQueryDisplayAttribEXT;
static PFNEGLQUERYDEVICEATTRIBEXTPROC peglQueryDeviceAttribEXT;

static PFNGLGENTEXTURESPROC pglGenTextures;
static PFNGLDELETETEXTURESPROC pglDeleteTextures;
static PFNGLBINDTEXTUREPROC pglBindTexture;
static PFNGLTEXPARAMETERIPROC pglTexParameteri;
static PFNGLVIEWPORTPROC pglViewport;
static PFNGLCLEARCOLORPROC pglClearColor;
static PFNGLCLEARPROC pglClear;
static PFNGLREADPIXELSPROC pglReadPixels;
static PFNGLGETERRORPROC pglGetError;
static PFNGLFINISHPROC pglFinish;
static PFNGLGETSTRINGPROC pglGetString;
static PFNGLCREATESHADERPROC pglCreateShader;
static PFNGLSHADERSOURCEPROC pglShaderSource;
static PFNGLCOMPILESHADERPROC pglCompileShader;
static PFNGLGETSHADERIVPROC pglGetShaderiv;
static PFNGLGETSHADERINFOLOGPROC pglGetShaderInfoLog;
static PFNGLCREATEPROGRAMPROC pglCreateProgram;
static PFNGLATTACHSHADERPROC pglAttachShader;
static PFNGLLINKPROGRAMPROC pglLinkProgram;
static PFNGLGETPROGRAMIVPROC pglGetProgramiv;
static PFNGLGETPROGRAMINFOLOGPROC pglGetProgramInfoLog;
static PFNGLUSEPROGRAMPROC pglUseProgram;
static PFNGLGENBUFFERSPROC pglGenBuffers;
static PFNGLBINDBUFFERPROC pglBindBuffer;
static PFNGLBUFFERDATAPROC pglBufferData;
static PFNGLVERTEXATTRIBPOINTERPROC pglVertexAttribPointer;
static PFNGLENABLEVERTEXATTRIBARRAYPROC pglEnableVertexAttribArray;
static PFNGLDRAWARRAYSPROC pglDrawArrays;
static PFNGLGETATTRIBLOCATIONPROC pglGetAttribLocation;
static PFNGLGETUNIFORMLOCATIONPROC pglGetUniformLocation;
static PFNGLUNIFORM1IPROC pglUniform1i;
static PFNGLACTIVETEXTUREPROC pglActiveTexture;

static FILE* g_log = nullptr;
#define LOG(...) do { \
    fprintf(stdout, __VA_ARGS__); fprintf(stdout, "\n"); \
    if (g_log) { fprintf(g_log, __VA_ARGS__); fprintf(g_log, "\n"); fflush(g_log); } \
} while (0)

static bool load_egl(HMODULE hEGL) {
#define GETP(var, name) do { var = (decltype(var))GetProcAddress(hEGL, name); if (!var) { LOG("  missing EGL export: %s", name); return false; } } while (0)
    GETP(peglInitialize, "eglInitialize");
    GETP(peglChooseConfig, "eglChooseConfig");
    GETP(peglCreateContext, "eglCreateContext");
    GETP(peglMakeCurrent, "eglMakeCurrent");
    GETP(peglCreatePbufferSurface, "eglCreatePbufferSurface");
    GETP(peglCreatePbufferFromClientBuffer, "eglCreatePbufferFromClientBuffer");
    GETP(peglBindTexImage, "eglBindTexImage");
    GETP(peglReleaseTexImage, "eglReleaseTexImage");
    GETP(peglDestroySurface, "eglDestroySurface");
    GETP(peglDestroyContext, "eglDestroyContext");
    GETP(peglTerminate, "eglTerminate");
    GETP(peglGetError, "eglGetError");
    GETP(peglGetProcAddress, "eglGetProcAddress");
#undef GETP
    peglGetPlatformDisplayEXT = (PFNEGLGETPLATFORMDISPLAYEXTPROC)peglGetProcAddress("eglGetPlatformDisplayEXT");
    peglQueryDisplayAttribEXT = (PFNEGLQUERYDISPLAYATTRIBEXTPROC)peglGetProcAddress("eglQueryDisplayAttribEXT");
    peglQueryDeviceAttribEXT = (PFNEGLQUERYDEVICEATTRIBEXTPROC)peglGetProcAddress("eglQueryDeviceAttribEXT");
    return peglGetPlatformDisplayEXT && peglQueryDisplayAttribEXT && peglQueryDeviceAttribEXT;
}

static bool load_gles(HMODULE hGLES) {
#define GETP(var, name) do { var = (decltype(var))GetProcAddress(hGLES, name); if (!var) { LOG("  missing GLES export: %s", name); return false; } } while (0)
    GETP(pglGenTextures, "glGenTextures"); GETP(pglDeleteTextures, "glDeleteTextures");
    GETP(pglBindTexture, "glBindTexture"); GETP(pglTexParameteri, "glTexParameteri");
    GETP(pglViewport, "glViewport"); GETP(pglClearColor, "glClearColor"); GETP(pglClear, "glClear");
    GETP(pglReadPixels, "glReadPixels"); GETP(pglGetError, "glGetError"); GETP(pglFinish, "glFinish");
    GETP(pglGetString, "glGetString"); GETP(pglCreateShader, "glCreateShader");
    GETP(pglShaderSource, "glShaderSource"); GETP(pglCompileShader, "glCompileShader");
    GETP(pglGetShaderiv, "glGetShaderiv"); GETP(pglGetShaderInfoLog, "glGetShaderInfoLog");
    GETP(pglCreateProgram, "glCreateProgram"); GETP(pglAttachShader, "glAttachShader");
    GETP(pglLinkProgram, "glLinkProgram"); GETP(pglGetProgramiv, "glGetProgramiv");
    GETP(pglGetProgramInfoLog, "glGetProgramInfoLog"); GETP(pglUseProgram, "glUseProgram");
    GETP(pglGenBuffers, "glGenBuffers"); GETP(pglBindBuffer, "glBindBuffer");
    GETP(pglBufferData, "glBufferData"); GETP(pglVertexAttribPointer, "glVertexAttribPointer");
    GETP(pglEnableVertexAttribArray, "glEnableVertexAttribArray"); GETP(pglDrawArrays, "glDrawArrays");
    GETP(pglGetAttribLocation, "glGetAttribLocation"); GETP(pglGetUniformLocation, "glGetUniformLocation");
    GETP(pglUniform1i, "glUniform1i"); GETP(pglActiveTexture, "glActiveTexture");
#undef GETP
    return true;
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

static void check_egl(const char* where) {
    EGLint e = peglGetError();
    if (e != EGL_SUCCESS) LOG("  [egl error] after %s: 0x%04X", where, e);
}
static void check_gl(const char* where) {
    GLenum e = pglGetError();
    if (e != GL_NO_ERROR) LOG("  [gl error] after %s: 0x%04X", where, e);
}

static GLuint compile_shader(GLenum type, const char* src) {
    GLuint s = pglCreateShader(type);
    pglShaderSource(s, 1, &src, nullptr);
    pglCompileShader(s);
    GLint ok = 0;
    pglGetShaderiv(s, GL_COMPILE_STATUS, &ok);
    if (!ok) { char buf[1024]; GLsizei n=0; pglGetShaderInfoLog(s, sizeof(buf), &n, buf); LOG("  shader compile error: %s", buf); }
    return s;
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
    g_log = fopen("mf_decode_egl_log.txt", "w");
    LOG("=== Prototype 3b: IMFMediaEngine decode -> EGL_ANGLE_d3d_texture_client_buffer ===");

    SetDllDirectoryW(L"..\\..\\..\\.venv\\share\\angle\\bin");
    HMODULE hEGL = LoadLibraryW(L"libEGL.dll");
    HMODULE hGLES = LoadLibraryW(L"libGLESv2.dll");
    if (!hEGL || !hGLES) { LOG("FAIL: could not load ANGLE DLLs"); return 1; }
    if (!load_egl(hEGL) || !load_gles(hGLES)) { LOG("FAIL: could not resolve EGL/GLES entry points"); return 1; }

    EGLint disp_attribs[] = { EGL_PLATFORM_ANGLE_TYPE_ANGLE, EGL_PLATFORM_ANGLE_TYPE_D3D11_ANGLE, EGL_NONE };
    EGLDisplay dpy = peglGetPlatformDisplayEXT(EGL_PLATFORM_ANGLE_ANGLE, EGL_DEFAULT_DISPLAY, disp_attribs);
    if (dpy == EGL_NO_DISPLAY) { LOG("FAIL: eglGetPlatformDisplayEXT returned EGL_NO_DISPLAY"); return 1; }
    EGLint maj=0, min=0;
    if (!peglInitialize(dpy, &maj, &min)) { LOG("FAIL: eglInitialize failed"); return 1; }
    LOG("ANGLE D3D11 EGL display initialized, version %d.%d", maj, min);

    EGLAttrib eglDeviceAttrib = 0;
    peglQueryDisplayAttribEXT(dpy, EGL_DEVICE_EXT, &eglDeviceAttrib);
    EGLDeviceEXT eglDevice = (EGLDeviceEXT)eglDeviceAttrib;
    EGLAttrib d3dDeviceAttrib = 0;
    peglQueryDeviceAttribEXT(eglDevice, EGL_D3D11_DEVICE_ANGLE, &d3dDeviceAttrib);
    ID3D11Device* angleDevice = (ID3D11Device*)d3dDeviceAttrib;
    if (!angleDevice) { LOG("FAIL: could not obtain ANGLE's internal ID3D11Device*"); return 1; }
    LOG("ANGLE's internal ID3D11Device* = %p", angleDevice);
    ID3D11DeviceContext* angleCtx = nullptr;
    angleDevice->GetImmediateContext(&angleCtx);

    // Required: Media Foundation's internal decode threads and this
    // thread will both touch angleDevice concurrently.
    ID3D10Multithread* mt = nullptr;
    if (SUCCEEDED(angleCtx->QueryInterface(__uuidof(ID3D10Multithread), (void**)&mt))) {
        mt->SetMultithreadProtected(TRUE);
        mt->Release();
    }

    EGLint cfg_attribs[] = {
        EGL_SURFACE_TYPE, EGL_PBUFFER_BIT, EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT,
        EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_ALPHA_SIZE, 8, EGL_NONE
    };
    EGLConfig config = nullptr; EGLint numConfigs = 0;
    if (!peglChooseConfig(dpy, cfg_attribs, &config, 1, &numConfigs) || numConfigs == 0) {
        LOG("FAIL: eglChooseConfig found no matching config"); return 1;
    }
    const int W = 256, H = 256;
    EGLint pbuf_attribs[] = { EGL_WIDTH, W, EGL_HEIGHT, H, EGL_NONE };
    EGLSurface renderSurf = peglCreatePbufferSurface(dpy, config, pbuf_attribs);
    EGLint ctx_attribs[] = { EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE };
    EGLContext ctx = peglCreateContext(dpy, config, EGL_NO_CONTEXT, ctx_attribs);
    if (!peglMakeCurrent(dpy, renderSurf, renderSurf, ctx)) { LOG("FAIL: eglMakeCurrent failed"); return 1; }
    LOG("GL_RENDERER: %s", (const char*)pglGetString(GL_RENDERER));

    const char* vs_src =
        "attribute vec2 aPos;\nattribute vec2 aUV;\nvarying vec2 vUV;\n"
        "void main() { vUV = aUV; gl_Position = vec4(aPos, 0.0, 1.0); }\n";
    const char* fs_src =
        "precision mediump float;\nvarying vec2 vUV;\nuniform sampler2D uTex;\n"
        "void main() { gl_FragColor = texture2D(uTex, vUV); }\n";
    GLuint vs = compile_shader(GL_VERTEX_SHADER, vs_src);
    GLuint fs = compile_shader(GL_FRAGMENT_SHADER, fs_src);
    GLuint prog = pglCreateProgram();
    pglAttachShader(prog, vs); pglAttachShader(prog, fs); pglLinkProgram(prog);
    GLint linkOk = 0; pglGetProgramiv(prog, GL_LINK_STATUS, &linkOk);
    if (!linkOk) { char buf[1024]; GLsizei n=0; pglGetProgramInfoLog(prog, sizeof(buf), &n, buf); LOG("FAIL: link error: %s", buf); return 1; }
    GLint locPos = pglGetAttribLocation(prog, "aPos");
    GLint locUV = pglGetAttribLocation(prog, "aUV");
    GLint locTex = pglGetUniformLocation(prog, "uTex");
    float quad[] = { -1.f,-1.f,0.f,0.f,  1.f,-1.f,1.f,0.f,  -1.f,1.f,0.f,1.f,  1.f,1.f,1.f,1.f };
    GLuint vbo = 0; pglGenBuffers(1, &vbo); pglBindBuffer(GL_ARRAY_BUFFER, vbo);
    pglBufferData(GL_ARRAY_BUFFER, sizeof(quad), quad, GL_STATIC_DRAW);

    // --- Media Foundation decode, bound to ANGLE's own device ---
    CHECK(CoInitializeEx(nullptr, COINIT_MULTITHREADED), "CoInitializeEx");
    CHECK(MFStartup(MF_VERSION), "MFStartup");

    UINT resetToken = 0;
    IMFDXGIDeviceManager* dxgiMgr = nullptr;
    CHECK(MFCreateDXGIDeviceManager(&resetToken, &dxgiMgr), "MFCreateDXGIDeviceManager");
    CHECK(dxgiMgr->ResetDevice(angleDevice, resetToken), "ResetDevice(angleDevice)");
    LOG("IMFDXGIDeviceManager bound to ANGLE's own device OK.");

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

    bool haveFrame = false; LONGLONG pts = 0;
    start = GetTickCount64();
    while (GetTickCount64() - start < 10000) {
        HRESULT hr = engine->OnVideoStreamTick(&pts);
        if (hr == S_OK) { haveFrame = true; break; }
        WaitForSingleObject(notify->event_, 50);
        ResetEvent(notify->event_);
    }
    if (!haveFrame) { LOG("FAIL: no video frame became ready within timeout"); return 1; }
    LOG("Video frame ready at pts=%lld", pts);

    D3D11_TEXTURE2D_DESC td = {};
    td.Width = W; td.Height = H; td.MipLevels = 1; td.ArraySize = 1;
    td.Format = DXGI_FORMAT_B8G8R8A8_UNORM; td.SampleDesc.Count = 1;
    td.Usage = D3D11_USAGE_DEFAULT;
    td.BindFlags = D3D11_BIND_RENDER_TARGET | D3D11_BIND_SHADER_RESOURCE;
    ID3D11Texture2D* destTex = nullptr;
    CHECK(angleDevice->CreateTexture2D(&td, nullptr, &destTex), "CreateTexture2D(dest)");

    RECT destRect = {0, 0, W, H};
    CHECK(engine->TransferVideoFrame(destTex, nullptr, &destRect, nullptr), "TransferVideoFrame");
    LOG("TransferVideoFrame OK -- decoded frame is now in a texture on ANGLE's own device.");
    verify_via_d3d_readback(angleDevice, angleCtx, destTex, W, H);

    // --- Push through the EGL/ANGLE interop bridge (same as Prototype 2's case A) ---
    EGLint pbuf_cb_attribs[] = { EGL_TEXTURE_FORMAT, EGL_TEXTURE_RGBA, EGL_TEXTURE_TARGET, EGL_TEXTURE_2D_ATTR, EGL_NONE };
    EGLSurface clientBufSurf = peglCreatePbufferFromClientBuffer(dpy, EGL_D3D_TEXTURE_ANGLE, (EGLClientBuffer)destTex, config, pbuf_cb_attribs);
    if (clientBufSurf == EGL_NO_SURFACE) { LOG("FAIL: eglCreatePbufferFromClientBuffer failed"); check_egl("eglCreatePbufferFromClientBuffer"); return 1; }
    LOG("eglCreatePbufferFromClientBuffer OK");

    GLuint glTex = 0;
    pglGenTextures(1, &glTex);
    pglBindTexture(GL_TEXTURE_2D, glTex);
    pglTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    pglTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    pglTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    pglTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    if (!peglBindTexImage(dpy, clientBufSurf, EGL_BACK_BUFFER)) { LOG("FAIL: eglBindTexImage failed"); check_egl("eglBindTexImage"); return 1; }
    LOG("eglBindTexImage OK");

    pglViewport(0, 0, W, H);
    pglClearColor(0.f, 0.f, 0.f, 1.f);
    pglClear(GL_COLOR_BUFFER_BIT);
    pglUseProgram(prog);
    pglBindBuffer(GL_ARRAY_BUFFER, vbo);
    pglEnableVertexAttribArray((GLuint)locPos);
    pglVertexAttribPointer((GLuint)locPos, 2, GL_FLOAT, GL_FALSE_, 4 * sizeof(float), (void*)0);
    pglEnableVertexAttribArray((GLuint)locUV);
    pglVertexAttribPointer((GLuint)locUV, 2, GL_FLOAT, GL_FALSE_, 4 * sizeof(float), (void*)(2 * sizeof(float)));
    pglActiveTexture(GL_TEXTURE0);
    pglBindTexture(GL_TEXTURE_2D, glTex);
    pglUniform1i(locTex, 0);
    pglDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
    check_gl("glDrawArrays");
    pglFinish();

    std::vector<uint8_t> rgba((size_t)W * H * 4);
    pglReadPixels(0, 0, W, H, GL_RGBA, GL_UNSIGNED_BYTE, rgba.data());
    check_gl("glReadPixels");
    size_t mid = ((size_t)(H/2) * W + (W/2)) * 4;
    LOG("  readback center pixel RGBA = %u,%u,%u,%u ; corner(0,0) RGBA = %u,%u,%u,%u",
        rgba[mid], rgba[mid+1], rgba[mid+2], rgba[mid+3], rgba[0], rgba[1], rgba[2], rgba[3]);
    save_bmp("mf_decode_egl_result.bmp", W, H, rgba);
    LOG("Saved mf_decode_egl_result.bmp for visual verification.");

    peglReleaseTexImage(dpy, clientBufSurf, EGL_BACK_BUFFER);
    peglDestroySurface(dpy, clientBufSurf);

    LOG("=== Done ===");
    fclose(g_log);
    return 0;
}
