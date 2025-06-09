from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

binaries = collect_dynamic_libs('coincurve')
hiddenimports = collect_submodules('coincurve')
