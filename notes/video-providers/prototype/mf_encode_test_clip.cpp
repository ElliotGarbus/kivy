// One-off helper for the Media-Foundation-decode prototypes: encodes a
// tiny (256x256, 2s, 30fps) H.264-in-MP4 test clip using Windows' own
// in-box H.264 encoder MFT via IMFSinkWriter, so the decode prototypes
// have something real to decode without downloading any external file
// or shipping any binary asset. The clip content is the same R=x, G=y,
// B=0x80 gradient the WGL/EGL interop probes used, so decoded output can
// be compared against a known-exact pattern (allowing for H.264's lossy
// compression -- see the decode prototypes for that comparison).
//
// Run once; mf_test_clip.mp4 is then reused by the decode prototypes.

#define _CRT_SECURE_NO_WARNINGS
#define WIN32_LEAN_AND_MEAN

#include <windows.h>
#include <mfapi.h>
#include <mfidl.h>
#include <mfreadwrite.h>
#include <cstdio>
#include <cstdint>
#include <vector>

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
    const UINT32 W = 256, H = 256;
    const UINT32 FPS = 30;
    const UINT32 DURATION_SEC = 2;
    const UINT32 FRAME_COUNT = FPS * DURATION_SEC;
    const LONGLONG FRAME_DURATION_100NS = 10000000LL / FPS;

    CHECK(CoInitializeEx(nullptr, COINIT_MULTITHREADED), "CoInitializeEx");
    CHECK(MFStartup(MF_VERSION), "MFStartup");

    IMFSinkWriter* writer = nullptr;
    CHECK(MFCreateSinkWriterFromURL(L"mf_test_clip.mp4", nullptr, nullptr, &writer),
          "MFCreateSinkWriterFromURL");

    IMFMediaType* outType = nullptr;
    CHECK(MFCreateMediaType(&outType), "MFCreateMediaType(out)");
    outType->SetGUID(MF_MT_MAJOR_TYPE, MFMediaType_Video);
    outType->SetGUID(MF_MT_SUBTYPE, MFVideoFormat_H264);
    outType->SetUINT32(MF_MT_AVG_BITRATE, 4000000);
    outType->SetUINT32(MF_MT_INTERLACE_MODE, MFVideoInterlace_Progressive);
    MFSetAttributeSize(outType, MF_MT_FRAME_SIZE, W, H);
    MFSetAttributeRatio(outType, MF_MT_FRAME_RATE, FPS, 1);
    MFSetAttributeRatio(outType, MF_MT_PIXEL_ASPECT_RATIO, 1, 1);

    DWORD streamIndex = 0;
    CHECK(writer->AddStream(outType, &streamIndex), "AddStream");

    IMFMediaType* inType = nullptr;
    CHECK(MFCreateMediaType(&inType), "MFCreateMediaType(in)");
    inType->SetGUID(MF_MT_MAJOR_TYPE, MFMediaType_Video);
    inType->SetGUID(MF_MT_SUBTYPE, MFVideoFormat_RGB32);
    inType->SetUINT32(MF_MT_INTERLACE_MODE, MFVideoInterlace_Progressive);
    MFSetAttributeSize(inType, MF_MT_FRAME_SIZE, W, H);
    MFSetAttributeRatio(inType, MF_MT_FRAME_RATE, FPS, 1);
    MFSetAttributeRatio(inType, MF_MT_PIXEL_ASPECT_RATIO, 1, 1);
    CHECK(writer->SetInputMediaType(streamIndex, inType, nullptr), "SetInputMediaType");

    CHECK(writer->BeginWriting(), "BeginWriting");

    const DWORD rowBytes = W * 4;
    const DWORD bufSize = rowBytes * H;

    for (UINT32 i = 0; i < FRAME_COUNT; i++) {
        IMFMediaBuffer* buffer = nullptr;
        CHECK(MFCreateMemoryBuffer(bufSize, &buffer), "MFCreateMemoryBuffer");
        BYTE* data = nullptr;
        CHECK(buffer->Lock(&data, nullptr, nullptr), "Lock");
        // RGB32 (MFVideoFormat_RGB32) is bottom-up BGRX in memory, same
        // byte layout as the D3D11 B8G8R8A8_UNORM texture the other
        // prototypes fill, and bottom-up row order -- so row y of the
        // image is written at buffer row (H-1-y).
        for (UINT32 y = 0; y < H; y++) {
            BYTE* row = data + (size_t)(H - 1 - y) * rowBytes;
            for (UINT32 x = 0; x < W; x++) {
                row[x * 4 + 0] = 0x80;                      // B
                row[x * 4 + 1] = (BYTE)((y * 255) / (H - 1)); // G
                row[x * 4 + 2] = (BYTE)((x * 255) / (W - 1)); // R
                row[x * 4 + 3] = 0xFF;                       // X
            }
        }
        buffer->Unlock();
        buffer->SetCurrentLength(bufSize);

        IMFSample* sample = nullptr;
        CHECK(MFCreateSample(&sample), "MFCreateSample");
        sample->AddBuffer(buffer);
        sample->SetSampleTime((LONGLONG)i * FRAME_DURATION_100NS);
        sample->SetSampleDuration(FRAME_DURATION_100NS);

        CHECK(writer->WriteSample(streamIndex, sample), "WriteSample");

        sample->Release();
        buffer->Release();
    }

    CHECK(writer->Finalize(), "Finalize");
    writer->Release();
    inType->Release();
    outType->Release();

    MFShutdown();
    CoUninitialize();

    printf("Wrote mf_test_clip.mp4: %ux%u, %u frames @ %ufps (%us)\n", W, H, FRAME_COUNT, FPS, DURATION_SEC);
    return 0;
}
