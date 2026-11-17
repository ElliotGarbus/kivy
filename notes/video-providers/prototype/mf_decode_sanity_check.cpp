// Sanity check only (not one of the interop prototypes): decodes
// mf_test_clip.mp4 with the simple synchronous IMFSourceReader API
// (CPU-side RGB32 output) and logs a couple of pixel values, to confirm
// the encoder produced a clip that actually decodes to the expected
// gradient before adding IMFMediaEngine + GPU-interop complexity on top.

#define _CRT_SECURE_NO_WARNINGS
#define WIN32_LEAN_AND_MEAN

#include <windows.h>
#include <mfapi.h>
#include <mfidl.h>
#include <mfreadwrite.h>
#include <cstdio>

#pragma comment(lib, "mfplat.lib")
#pragma comment(lib, "mfreadwrite.lib")
#pragma comment(lib, "mfuuid.lib")
#pragma comment(lib, "ole32.lib")

static void fail(const char* where, HRESULT hr) {
    fprintf(stderr, "FAIL at %s: 0x%08lX\n", where, hr);
    exit(1);
}
#define CHECK(expr, where) do { HRESULT _hr = (expr); if (FAILED(_hr)) fail(where, _hr); } while (0)

int main() {
    CHECK(CoInitializeEx(nullptr, COINIT_MULTITHREADED), "CoInitializeEx");
    CHECK(MFStartup(MF_VERSION), "MFStartup");

    IMFAttributes* readerAttrs = nullptr;
    CHECK(MFCreateAttributes(&readerAttrs, 1), "MFCreateAttributes");
    // Needed so the reader will auto-convert the H.264 decoder's native
    // NV12 output to RGB32 for us (without this, SetCurrentMediaType to
    // RGB32 below fails with MF_E_INVALIDMEDIATYPE).
    readerAttrs->SetUINT32(MF_SOURCE_READER_ENABLE_VIDEO_PROCESSING, TRUE);

    IMFSourceReader* reader = nullptr;
    CHECK(MFCreateSourceReaderFromURL(L"mf_test_clip.mp4", readerAttrs, &reader),
          "MFCreateSourceReaderFromURL");

    IMFMediaType* rgbType = nullptr;
    CHECK(MFCreateMediaType(&rgbType), "MFCreateMediaType");
    rgbType->SetGUID(MF_MT_MAJOR_TYPE, MFMediaType_Video);
    rgbType->SetGUID(MF_MT_SUBTYPE, MFVideoFormat_RGB32);
    CHECK(reader->SetCurrentMediaType((DWORD)MF_SOURCE_READER_FIRST_VIDEO_STREAM, nullptr, rgbType),
          "SetCurrentMediaType(RGB32)");

    IMFMediaType* actualType = nullptr;
    CHECK(reader->GetCurrentMediaType((DWORD)MF_SOURCE_READER_FIRST_VIDEO_STREAM, &actualType),
          "GetCurrentMediaType");
    UINT32 w = 0, h = 0;
    MFGetAttributeSize(actualType, MF_MT_FRAME_SIZE, &w, &h);
    printf("Decoded frame size: %ux%u\n", w, h);

    DWORD flags = 0;
    IMFSample* sample = nullptr;
    CHECK(reader->ReadSample((DWORD)MF_SOURCE_READER_FIRST_VIDEO_STREAM, 0, nullptr, &flags, nullptr, &sample),
          "ReadSample");
    if (!sample) { fprintf(stderr, "FAIL: no sample read (flags=0x%lX)\n", flags); return 1; }

    IMFMediaBuffer* buffer = nullptr;
    CHECK(sample->ConvertToContiguousBuffer(&buffer), "ConvertToContiguousBuffer");
    BYTE* data = nullptr;
    DWORD len = 0;
    CHECK(buffer->Lock(&data, nullptr, &len), "Lock");

    DWORD rowBytes = w * 4;
    // RGB32 output is bottom-up, same as the encoder's input was.
    BYTE* row0 = data + (size_t)(h - 1) * rowBytes;          // top-left pixel (0,0)
    BYTE* rowMid = data + (size_t)(h - 1 - h / 2) * rowBytes; // center row
    printf("pixel(0,0)   BGRX = %u,%u,%u,%u  (expected ~0,0,0 -- R=G=0 at origin)\n",
           row0[0], row0[1], row0[2], row0[3]);
    printf("pixel(w/2,h/2) BGRX = %u,%u,%u,%u  (expected ~128,128,128 -- mid gradient)\n",
           rowMid[(w/2)*4+0], rowMid[(w/2)*4+1], rowMid[(w/2)*4+2], rowMid[(w/2)*4+3]);

    buffer->Unlock();
    buffer->Release();
    sample->Release();
    actualType->Release();
    rgbType->Release();
    reader->Release();
    MFShutdown();
    CoUninitialize();
    return 0;
}
