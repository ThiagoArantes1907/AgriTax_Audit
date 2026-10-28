import re, base64, sys, os
d = sys.argv[1]  # .../package/esm
def strip(p):
    s = open(os.path.join(d, p), encoding='utf-8').read()
    s = re.sub(r'^import .*?;\s*$', '', s, flags=re.M)
    s = re.sub(r'^export \* from .*?;\s*$', '', s, flags=re.M)
    s = re.sub(r'^//# sourceMappingURL=.*$', '', s, flags=re.M)
    s = re.sub(r'^export (async function|class|function|const)', r'\1', s, flags=re.M)
    return s
u = open(os.path.join(d, 'js/unrar.js'), encoding='utf-8').read()
u = u.replace('export default Module;', 'const unrarFactory = Module;')
assert 'unrarFactory = Module' in u
parts = [u] + [strip(p) for p in ['js/unrar.singleton.js', 'js/Extractor.js', 'js/ExtractorData.helper.js', 'js/ExtractorData.js', 'index.esm.js']]
wasm = base64.b64encode(open(os.path.join(d, 'js/unrar.wasm'), 'rb').read()).decode()
js = ('/* node-unrar-js 2.0.2 (MIT, Jianrong Yu) — unrar oficial compilado em WebAssembly, embutido para leitura offline de .rar */\n'
      'const UnrarLib=(()=>{\n' + '\n'.join(parts) + '\nreturn {createExtractorFromData};\n})();\n'
      'const UNRAR_WASM_B64="' + wasm + '";\n')
open(sys.argv[2], 'w', encoding='utf-8', newline='\n').write(js)
print(len(js))
