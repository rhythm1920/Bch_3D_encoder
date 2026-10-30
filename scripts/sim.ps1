param(
    [Parameter(Mandatory = $true)][string]$Top,        # testbench top module
    [Parameter(Mandatory = $true)][string[]]$Files,    # Verilog sources (absolute or relative to repo root)
    [string]$WorkDir = "build\sim",                    # run directory (absolute, or relative to repo root)
    [string[]]$Defines = @(),
    [switch]$Glbl,                                     # post-synthesis/route netlists: also elaborate work.glbl (add glbl.v to -Files)
    [string]$VivadoBin = "D:\Rhythm\2026.1\Vivado\bin"
)
# Vivado xsim batch flow: xvlog -> xelab -> xsim -R. Exit code 0 only if the log has PASS and no FAIL/ERROR.
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$wd = if ([IO.Path]::IsPathRooted($WorkDir)) { $WorkDir } else { Join-Path $repo $WorkDir }
New-Item -ItemType Directory -Force $wd | Out-Null
Push-Location $wd
try {
    $src = ($Files -split ',') | ForEach-Object { $_.Trim() } | Where-Object { $_ } | ForEach-Object {
        $p = if ([IO.Path]::IsPathRooted($_)) { $_ } else { Join-Path $repo $_ }
        $p -replace '\\','/'
    }
    $defs = ($Defines -split ',') | ForEach-Object { $_.Trim() } | Where-Object { $_ } | ForEach-Object { "-d"; $_ }
    $t0 = Get-Date
    & "$VivadoBin\xvlog.bat" --nolog --incr @defs @src > xvlog.log 2>&1
    if ($LASTEXITCODE -ne 0) { Get-Content xvlog.log | Select-String "ERROR" | Select-Object -First 20; throw "xvlog failed" }
    & "$VivadoBin\xelab.bat" --nolog --debug off --relax --mt 8 -L unisims_ver -s "${Top}_snap" $Top @(if ($Glbl) { "work.glbl" }) > xelab.log 2>&1
    if ($LASTEXITCODE -ne 0) { Get-Content xelab.log | Select-String "ERROR" | Select-Object -First 20; throw "xelab failed" }
    $t1 = Get-Date
    & "$VivadoBin\xsim.bat" "${Top}_snap" -R --nolog > xsim.log 2>&1
    $t2 = Get-Date
    $log = Get-Content xsim.log
    $log | Where-Object { $_ -match "PASS|FAIL|MISMATCH|ERROR|INFO:" } | Select-Object -First 40
    "compile+elab {0:N1}s  sim {1:N1}s" -f ($t1 - $t0).TotalSeconds, ($t2 - $t1).TotalSeconds
    if (($log -match "^PASS$") -and -not ($log -match "^(FAIL|ERROR)|MISMATCH")) { "RESULT: PASS"; exit 0 } else { "RESULT: FAIL"; exit 1 }
}
finally { Pop-Location }
