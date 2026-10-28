# PyInstaller must not bundle an incompatible third-party ICU DLL found on PATH.
# Qt on Windows uses the operating system's ICU implementation.
a = Analysis(['run_app.py'], pathex=[], binaries=[],
             datas=[('assets/milk_frog_icon.png', 'assets'), ('assets/SBHarness.ico', 'assets')], hiddenimports=[],
             hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[],
             noarchive=False, optimize=0)
a.binaries = [entry for entry in a.binaries
              if entry[0].replace('\\', '/').lower().split('/')[-1] not in ('icuuc.dll', 'icudt78.dll')]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='SBHarness-Standalone',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=True,
          upx_exclude=[], runtime_tmpdir=None, console=False,
          disable_windowed_traceback=False, argv_emulation=False,
          target_arch=None, codesign_identity=None, entitlements_file=None,
          icon='assets/SBHarness.ico')
