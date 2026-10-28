$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    python -m unittest discover -s tests -q
    if ($LASTEXITCODE -ne 0) { throw 'Automated tests failed.' }
    python main.py --self-test --report artifacts\source-self-test.json
    if ($LASTEXITCODE -ne 0) { throw 'Source self-test failed.' }
    python -m PyInstaller --noconfirm --clean --onefile --windowed --noupx --name HumanTypePython-Editable --icon assets\HumanType.ico --add-data 'assets/HumanType.ico:assets' --distpath dist --workpath build --specpath . --exclude-module numpy --exclude-module matplotlib --exclude-module pandas main.py
    if ($LASTEXITCODE -ne 0) { throw 'Executable build failed.' }
    $executable = Join-Path $PSScriptRoot 'dist\HumanTypePython-Editable.exe'
    $report = Join-Path $PSScriptRoot 'artifacts\exe-self-test.json'
    $testProcess = Start-Process -FilePath $executable -ArgumentList @('--self-test', '--report', ('"' + $report + '"')) -WindowStyle Hidden -PassThru -Wait
    if ($testProcess.ExitCode -ne 0) { throw 'Packaged executable self-test failed.' }
    # Python's JSON parser preserves case-sensitive keys such as character probes a/A.
    python -c 'import json,sys; r=json.load(open(sys.argv[1], encoding="utf-8")); assert r["passed"], "Packaged tests failed"; print("Packaged executable verified:", r["tests"], "automated tests")' $report
    if ($LASTEXITCODE -ne 0) { throw 'Packaged executable reported a test failure.' }
    Write-Output "Built and verified $executable"
}
finally { Pop-Location }
