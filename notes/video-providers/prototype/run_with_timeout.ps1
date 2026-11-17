param(
    [string]$Script,
    [string]$OutFile,
    [int]$TimeoutSec = 4
)
$env:KIVY_NO_ARGS = "1"
$env:PYTHONUNBUFFERED = "1"
Remove-Item Env:\KIVY_GL_BACKEND -ErrorAction SilentlyContinue
$py = "..\..\..\.venv\Scripts\python.exe"
$p = Start-Process -FilePath $py -ArgumentList $Script -RedirectStandardOutput $OutFile -RedirectStandardError "$OutFile.err" -PassThru -WindowStyle Normal
$finished = $p.WaitForExit($TimeoutSec * 1000)
if (-not $finished) {
    Write-Output "TIMED_OUT_KILLING"
    Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
} else {
    Write-Output "EXITED_NORMALLY: $($p.ExitCode)"
}
