#!/usr/bin/env python3
"""apply_backend_edit.py - Update backend version/hash/dir across Stet files.

Usage:
  apply_backend_edit.py CONSTANTS BUILD CONFIG BENCH NEW_VERSION LLAMA_SHA CUDA_SHA NEW_DIR

Edits constants.py, build.py, config.json, and benchmark_backend_version.ps1.

Hashes come from the GitHub asset digest (lowercase). Writes UPPERCASE into
constants.py and the build.py .bat template, lowercase into the .sh template,
per docs/BACKEND_UPDATE_GUIDE.md. The benchmark $versions array is replaced
with one driven by its -OldDir/-NewDir/-OldLabel/-NewLabel parameters.
"""
import re, sys

def sub_once(s, pattern, repl):
    new, n = re.subn(pattern, repl, s, count=1)
    if n == 0:
        raise SystemExit("FAILED to match pattern: %r" % pattern)
    return new

def edit_constants(path, version, ll_up, cu_up):
    s = open(path, encoding="utf-8").read()
    s = sub_once(s, r'(LLAMA_BACKEND_VERSION\s*=\s*")b\d+(")', r"\g<1>" + version + r"\2")
    s = sub_once(s, r'("llama":\s*")[A-Fa-f0-9]+(")', r"\g<1>" + ll_up + r"\2")
    s = sub_once(s, r'("cuda":\s*")[A-Fa-f0-9]+(")', r"\g<1>" + cu_up + r"\2")
    open(path, "w", encoding="utf-8").write(s)

def edit_build(path, version, ll_up, cu_up, ll_lo, cu_lo):
    s = open(path, encoding="utf-8").read()
    s = sub_once(s, r'(_LLAMA_BACKEND_VERSION\s*=\s*")b\d+(")', r"\g<1>" + version + r"\2")
    s = sub_once(s, r'(set LLAMA_HASH=)[A-Fa-f0-9]+', r"\g<1>" + ll_up)
    s = sub_once(s, r'(set CUDA_HASH=)[A-Fa-f0-9]+', r"\g<1>" + cu_up)
    s = sub_once(s, r'(LLAMA_HASH=")[a-f0-9]+(")', r"\g<1>" + ll_lo + r"\2")
    s = sub_once(s, r'(CUDA_HASH=")[a-f0-9]+(")', r"\g<1>" + cu_lo + r"\2")
    open(path, "w", encoding="utf-8").write(s)

def edit_config(path, new_dir):
    s = open(path, encoding="utf-8").read()
    # Preserve the absolute directory prefix above the backend folder so a new
    # llama_server_path keeps its original base location (e.g. D:/Projects/...).
    ma = re.search(r'"llama_server_path"\s*:\s*"([^"]*llama-server\.exe)"', s)
    prefix = ""
    if ma:
        raw = ma.group(1)
        norm = raw.replace("\\\\", "/").replace("\\", "/")
        base = norm.rsplit("/llama-server.exe", 1)[0]
        if "/" in base:
            prefix = base.rsplit("/", 1)[0] + "/"
    exe = prefix + new_dir + "/llama-server.exe"
    s = sub_once(s, r'("llama_server_path"\s*:\s*")[^"]*(")', r"\g<1>" + exe + r"\2")
    open(path, "w", encoding="utf-8").write(s)

def edit_benchmark(path):
    s = open(path, encoding="utf-8").read()
    if "[string]$OldDir" not in s:
        anchor = '    [int]$Repeats = 5\n'
        close = s.index(anchor)
        tail = s[close+len(anchor):]
        new_params = ('    [string]$OldDir,\n'
                      '    [string]$NewDir,\n'
                      '    [string]$OldLabel = "bOLD",\n'
                      '    [string]$NewLabel = "bNEW"\n')
        s = s[:close] + anchor.rstrip() + ', ' + new_params + '    ' + tail
    idx = s.find("$versions")
    if idx < 0:
        raise SystemExit("benchmark $versions assignment not found")
    start = s.rfind("\n", 0, idx) + 1
    eq = s.find("=", idx)
    open_paren = s.find("@(", eq)
    close2 = s.find(")", open_paren)
    if close2 < 0:
        raise SystemExit("benchmark $versions array unterminated")
    block = ('$versions = @(\n'
             '    @{Label=$OldLabel; Dir=$OldDir},\n'
             '    @{Label=$NewLabel; Dir=$NewDir}\n'
             ')')
    s = s[:start] + block + s[close2+1:]
    open(path, "w", encoding="utf-8").write(s)

def main(argv):
    if len(argv) != 8:
        print(__doc__); return 2
    const, build, cfg, bench, version, ll, cu, new_dir = argv
    ll_up, cu_up = ll.upper(), cu.upper()
    ll_lo, cu_lo = ll.lower(), cu.lower()
    print("editing", const); edit_constants(const, version, ll_up, cu_up)
    print("editing", build); edit_build(build, version, ll_up, cu_up, ll_lo, cu_lo)
    print("editing", cfg); edit_config(cfg, new_dir)
    print("editing", bench); edit_benchmark(bench)
    print("done ok")
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
