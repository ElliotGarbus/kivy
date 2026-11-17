/*
 * Video provider implementation for Windows using Media Foundation.
 *
 * Architecture, decided and validated against real hardware via four
 * throwaway prototypes under notes/video-providers/prototype/ (see
 * notes/video-providers/windows-spike.md): IMFMediaEngine decodes into a
 * caller-supplied ID3D11Texture2D via TransferVideoFrame; that texture is
 * then shared into GL with no CPU copy via one of two bridges, chosen at
 * runtime by which GL backend Kivy actually initialized:
 *
 *   - WGL_NV_DX_interop2, for the "glew" / "sdl3" backends (native
 *     desktop GL via WGL -- the pip-install default).
 *   - EGL_ANGLE_d3d_texture_client_buffer, for "angle_sdl3" (ANGLE/GLES
 *     over D3D11 -- the backend Kivy's own CI actually tests). ANGLE
 *     requires the texture to be created on its OWN internal D3D11
 *     device (queried via EGL_ANGLE_device_d3d), not merely a device on
 *     the same adapter -- confirmed the hard way in Prototype 2.
 *
 * If the one-time bridge registration fails (e.g. a WGL/DX interop
 * driver gap, or a GL backend that isn't angle_sdl3 despite being on
 * Windows), the player falls back to a CPU copy for every frame
 * (TransferVideoFrame -> staging texture -> Map -> swizzle -> Texture
 * upload) rather than failing outright -- matching Kivy 3.0's video
 * provider goal that secondary/fallback paths may copy through the CPU.
 *
 * This header is deliberately free of <windows.h>, <d3d11.h>,
 * <mfapi.h> and friends: those pull in macros (interface, IN, OUT,
 * small, ...) that can silently corrupt Cython-generated C++ if this
 * header were included into the same translation unit (the trick
 * video_avfoundation_implem.mm uses on macOS, where EGL/GLES headers
 * don't have this problem, doesn't translate safely to Windows's COM
 * headers). All real Windows/COM/D3D11/MF state lives in a private
 * ``MFVideoPlayerImpl`` struct defined only in
 * video_mediafoundation_implem.cpp, which is compiled as its own
 * separate translation unit (see setup.py).
 */

#ifndef KIVY_VIDEO_MEDIAFOUNDATION_IMPLEM_H
#define KIVY_VIDEO_MEDIAFOUNDATION_IMPLEM_H

class VideoFrame {
public:
    VideoFrame();
    ~VideoFrame();

    // CPU-copy RGBA pixel bytes. Populated for CPU-fallback playback
    // frames and for generateThumbnail(); NULL/zero for zero-copy
    // playback frames (pixels live in gl_texture_id instead).
    char *data;
    unsigned int datasize;
    unsigned int rowsize;

    int width;
    int height;
    double pts;

    // Zero-copy delivery: externally-owned GL texture id, shared with
    // the decoder's D3D11 texture via the active bridge. Zero when the
    // frame was delivered via the CPU-copy path above.
    unsigned int gl_texture_id;
    unsigned int gl_target;       // GL_TEXTURE_2D
};

struct MFVideoPlayerImpl;  // defined only in video_mediafoundation_implem.cpp

class VideoPlayer {
public:
    // gl_backend_name is cgl_get_initialized_backend_name()'s value
    // (e.g. "glew", "sdl3", "angle_sdl3") -- decides which bridge to
    // attempt.
    VideoPlayer(const char *url, const char *gl_backend_name);
    ~VideoPlayer();

    // Non-blocking: kicks off SetSource and returns immediately.
    // Readiness is discovered by polling hasNewFrame()/isBuffering()/
    // isEOS() from the caller's own frame-pump tick -- this class must
    // never block the calling (Kivy main) thread.
    void load();
    void play();
    void pause();
    void stop();
    void unload();

    // Note: IMFMediaEngine::SetCurrentTime has no "precise vs fast"
    // distinction the way AVPlayer's seek does; `precise` is accepted
    // for interface parity with the other providers but currently has
    // no effect.
    void seek(double seconds, bool precise);

    double position();
    double duration();
    void setVolume(double v);

    // Non-blocking poll; cheap to call every tick.
    bool hasNewFrame();
    VideoFrame *retrieveFrame();

    bool isEOS();

    // True between MF_MEDIA_ENGINE_EVENT_WAITING and the next PLAYING
    // (or CANPLAYTHROUGH) event -- i.e. playback is stalled waiting on
    // data, the same semantics as VideoBase.buffering.
    bool isBuffering();

    // Thumbnails are a synchronous, one-shot API (unlike playback) --
    // this is allowed to block the calling thread briefly waiting for
    // the seek + first frame, same as the other providers' thumbnail
    // paths. Caller owns the returned frame and must delete it. Returns
    // NULL on error/timeout. Always CPU-copy.
    static VideoFrame *generateThumbnail(const char *url, double t,
                                         int w, int h);

    // Diagnostics for the Cython layer to log via Kivy's own Logger
    // (this C++ layer has no logging of its own). lastError() returns
    // an empty string if nothing has failed outright. usingCpuFallback()
    // is only meaningful once a frame has actually been requested, since
    // bridge registration is probed lazily on the first transferred
    // frame.
    const char *lastError();
    bool usingCpuFallback();

private:
    MFVideoPlayerImpl *impl;
};

#endif  /* KIVY_VIDEO_MEDIAFOUNDATION_IMPLEM_H */
