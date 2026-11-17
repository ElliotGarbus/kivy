# Builds and runs all five Windows video-provider interop prototypes in
# order, on whatever machine this is run on. Intended for testing the
# spike's findings on different hardware (see windows-spike.md's "still
# unverified" notes) -- just clone/copy the repo onto the target machine
# and run this from inside notes\video-providers\prototype.
#
# Requires: MSVC Build Tools (any 2022 edition: Community/Professional/
# Enterprise/BuildTools) and, for the two EGL/ANGLE-dependent prototypes,
# the ANGLE DLLs at ..\..\..\.venv\share\angle\bin relative to this
# script -- i.e. a repo-root .venv with kivy_deps.angle installed
# (`pip install kivy_deps.angle` inside that venv), or just copy
# libEGL.dll / libGLESv2.dll / d3dcompiler_47.dll there by hand.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Find-VcVars64 {
    $roots = @("C:\Program Files\Microsoft Visual Studio\2022",
               "C:\Program Files (x86)\Microsoft Visual Studio\2022")
    foreach ($root in $roots) {
        if (Test-Path $root) {
            $found = Get-ChildItem $root -Recurse -Filter "vcvars64.bat" -ErrorAction SilentlyContinue |
                     Select-Object -First 1 -ExpandProperty FullName
            if ($found) { return $found }
        }
    }
    return $null
}

$vcvars = Find-VcVars64
if (-not $vcvars) {
    Write-Error "Could not find vcvars64.bat under a VS 2022 install. Install MSVC Build Tools first."
    exit 1
}
Write-Host "Using MSVC toolchain: $vcvars"

$angleDir = Join-Path $PSScriptRoot "..\..\..\.venv\share\angle\bin"
$haveAngle = (Test-Path (Join-Path $angleDir "libEGL.dll")) -and (Test-Path (Join-Path $angleDir "libGLESv2.dll"))
if (-not $haveAngle) {
    Write-Warning "ANGLE DLLs not found at $angleDir -- egl_angle_interop_test and mf_decode_egl_test will fail to load them. See this script's header comment."
}

$sources = @(
    "mf_encode_test_clip.cpp",
    "mf_encode_animated_clip.cpp",
    "mf_decode_sanity_check.cpp",
    "wgl_dx_interop_test.cpp",
    "egl_angle_interop_test.cpp",
    "mf_decode_wgl_test.cpp",
    "mf_decode_egl_test.cpp",
    "mf_playback_test.cpp"
)

Write-Host "`n=== Compiling ===" -ForegroundColor Cyan
foreach ($src in $sources) {
    $exe = [System.IO.Path]::ChangeExtension($src, ".exe")
    Write-Host "  $src -> $exe"
    $cmd = "`"$vcvars`" && cl /EHsc /nologo `"$src`" /Fe:`"$exe`""
    $out = cmd /c $cmd 2>&1
    if (-not (Test-Path $exe)) {
        Write-Host "--- build output ---"
        $out | Write-Host
        Write-Error "Build failed for $src"
        exit 1
    }
}
Get-ChildItem *.obj -ErrorAction SilentlyContinue | Remove-Item -Force

# Order matters: the encoder must run before anything that decodes
# mf_test_clip.mp4; the two synthetic-texture probes have no dependency
# on it and can run any time.
$runOrder = @(
    @{ Name = "mf_encode_test_clip.exe";     Required = $true },
    @{ Name = "mf_encode_animated_clip.exe"; Required = $true },
    @{ Name = "mf_decode_sanity_check.exe";  Required = $true },
    @{ Name = "wgl_dx_interop_test.exe";     Required = $true },
    @{ Name = "egl_angle_interop_test.exe";  Required = $haveAngle; SkipReason = "ANGLE DLLs not available" },
    @{ Name = "mf_decode_wgl_test.exe";      Required = $true },
    @{ Name = "mf_decode_egl_test.exe";      Required = $haveAngle; SkipReason = "ANGLE DLLs not available" },
    @{ Name = "mf_playback_test.exe";        Required = $true }
)

Write-Host "`n=== Running ===" -ForegroundColor Cyan
$results = @()
foreach ($step in $runOrder) {
    $name = $step.Name
    if (-not $step.Required) {
        Write-Host "  SKIP  $name ($($step.SkipReason))" -ForegroundColor Yellow
        $results += [PSCustomObject]@{ Prototype = $name; ExitCode = "skipped" }
        continue
    }
    Write-Host "  --- $name ---"
    & ".\$name"
    $code = $LASTEXITCODE
    $status = if ($code -eq 0) { "OK" } else { "FAIL ($code)" }
    Write-Host "  -> $status`n"
    $results += [PSCustomObject]@{ Prototype = $name; ExitCode = $code }
}

Write-Host "`n=== Summary ===" -ForegroundColor Cyan
$results | Format-Table -AutoSize
Write-Host "Logs: *_log.txt   Screenshots: *.bmp (convert to .png with System.Drawing if needed)"
