"""Package only verified artifacts and the source needed to rebuild them."""
from pathlib import Path
import json
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(__file__).resolve().parent
report = root / "artifacts" / "exe-self-test.json"
result = json.loads(report.read_text(encoding="utf-8"))
assert result["passed"] and not result["desktop_input_sent"] and not result["clipboard_read"]
assert not result.get("live_keyboard_hook_installed", True)
executable = root / "dist" / "HumanTypePython-Editable.exe"
assert executable.is_file()
with ZipFile(root / "HumanTypePython-Windows.zip", "w", ZIP_DEFLATED) as package:
    package.write(executable, executable.name)
    package.write(root / "README.md", "README.md")
    package.write(report, "verification.json")
files = [*root.glob("*.py"), *root.glob("*.pyw"), *root.glob("*.ps1"), root / "README.md", root / "requirements-build.txt", root / ".gitignore"]
files += list((root / "tests").glob("*.py"))
files += list((root / "assets").glob("*.ico"))
files += list((root / "assets").glob("*.md"))
with ZipFile(root / "HumanTypePython-Source.zip", "w", ZIP_DEFLATED) as package:
    for path in sorted(files):
        package.write(path, str(Path("HumanTypePython") / path.relative_to(root)))
print("Packaged executable and source. Verified tests:", result["tests"])
