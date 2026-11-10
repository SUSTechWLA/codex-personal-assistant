# Local launcher; never changes the user's PATH.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$taskPython = $null
foreach ($taskName in @('python', 'python3', 'py')) {
    $taskCommand = Get-Command $taskName -ErrorAction SilentlyContinue
    if ($null -eq $taskCommand) { continue }
    try {
        $taskVersion = & $taskCommand.Source -c 'import sys; print(sys.version_info[0], sys.version_info[1])' 2>$null
        if ($LASTEXITCODE -eq 0 -and ($taskVersion -match '^3 (?:[9]|[1-9][0-9]+)$')) {
            $taskPython = $taskCommand.Source
            break
        }
    } catch { }
}
if ($null -eq $taskPython) {
    $taskPackages = Get-AppxPackage -Name 'OpenAI.CodexPrimaryRuntime*' -ErrorAction SilentlyContinue | Sort-Object Version -Descending
    foreach ($taskPackage in $taskPackages) {
        $taskCandidate = Join-Path $taskPackage.InstallLocation 'dependencies\python\python.exe'
        if (Test-Path -LiteralPath $taskCandidate -PathType Leaf) {
            $taskPython = $taskCandidate
            break
        }
    }
}
if ($null -eq $taskPython) {
    Write-Error '未找到可用的 Python 3.9+。请安装 Python，或确认 Codex 已安装运行时依赖。'
    exit 2
}
& $taskPython -X utf8 (Join-Path $PSScriptRoot 'assistant.py') --root $PSScriptRoot @args
exit $LASTEXITCODE
