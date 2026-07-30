from pathlib import Path


root = Path(SPECPATH).resolve().parents[1]
scripts = root / "scripts"

analysis = Analysis(
    [str(scripts / "frame_timing_agent" / "ui" / "app.py")],
    pathex=[str(scripts)],
    binaries=[],
    datas=[
        (
            str(scripts / "frame_timing_agent" / "config"),
            "frame_timing_agent/config",
        ),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

python_archive = PYZ(analysis.pure)

executable = EXE(
    python_archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="FrameTimingSkill",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)

bundle = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="FrameTimingSkill",
)
