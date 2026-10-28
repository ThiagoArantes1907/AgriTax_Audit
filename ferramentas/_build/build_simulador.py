import re, sys
src = open(sys.argv[1], encoding='utf-8').read()
body = open(sys.argv[2], encoding='utf-8').read()
style = re.search(r'<style>(.*?)</style>', src, re.S).group(1)
logos = re.findall(r'<img class="logo logo-(l|d)"[^>]*src="(data:image[^"]+)"', src)
L = dict(logos)
extra = r'''
/* simulador */
.prog{max-width:720px;margin:16px auto 0;display:flex;align-items:center;gap:12px;font-size:12px;color:var(--ink-2)}
.prog-bar{flex:1;height:8px;border-radius:4px;background:var(--surface-3);overflow:hidden}
.prog-bar i{display:block;height:100%;width:0;background:var(--lime);transition:width .2s}
.period{display:flex;flex-wrap:wrap;gap:10px;align-items:center;font-size:12px;color:var(--ink-2)}
.period select{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:5px 8px;margin-left:4px}
.chkl{display:inline-flex;align-items:center;gap:6px;font-size:12px;color:var(--ink-2)}
.prods{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px;align-items:center}
.prods .filter-note{width:100%}
.pchk{display:flex;gap:8px;align-items:flex-start;border:1px solid var(--border);border-radius:10px;padding:6px 10px;cursor:pointer;font-size:12px;line-height:1.25;max-width:340px}
.pchk b{display:block;font-size:12.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:280px}
.pchk span{color:var(--muted);font-size:11px}
.pchk.on{border-color:var(--brand);background:var(--surface-2)}
.pchk input{margin-top:2px;accent-color:var(--brand)}
.mais{width:100%}
.pbtn{border:0;background:none;padding:0;text-align:left;cursor:pointer;display:flex;flex-direction:column;line-height:1.25;color:inherit;min-width:0}
.pbtn:hover b{text-decoration:underline}
tr.atual td{background:color-mix(in srgb,var(--lime) 12%,transparent)}
.tab .badge.info{background:var(--surface-3);color:var(--ink-2)}
.mais summary{cursor:pointer;font-size:12px;color:var(--brand);margin:2px 0}
.mais .prods{margin-top:6px}
.kpi .v .st{font-size:13px;vertical-align:middle}
.st{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;font-weight:600;border-radius:999px;padding:3px 10px;white-space:nowrap}
.verdict{border-left:5px solid var(--corn)}
.v-head{display:flex;gap:14px;align-items:flex-start}
.v-ico{flex:none;width:34px;height:34px;border-radius:10px;background:var(--brand);color:var(--on-brand);display:grid;place-items:center;font-weight:700;font-size:16px}
.verdict h2{font-size:16px}
.verdict ul{margin:6px 0 0;padding-left:66px;font-size:13px;color:var(--ink-2)}
.verdict li{margin:4px 0}
.verdict li b{color:var(--ink)}
.warn-li::marker{color:var(--warn)}
table.proj td:first-child{white-space:nowrap}
table.proj th small{display:block;font-weight:400;color:var(--muted);font-size:10px}
table.proj tr.sec td{background:var(--surface-2);font-weight:650;color:var(--char);padding-top:10px;border-bottom:1px solid var(--border)}
table.proj tr.sec.sa td{box-shadow:inset 4px 0 0 var(--s2)}
table.proj tr.sec.sb td{box-shadow:inset 4px 0 0 var(--s1)}
table.proj tr.tot td{font-weight:600}
table.proj tr.big td{font-weight:700;border-bottom:2px solid var(--axis)}
table.proj tr.dif td{font-weight:700;color:var(--char)}
table.proj tr.sub2 td{color:var(--muted);font-size:11.5px}
table.proj tr.aliq td{color:var(--ink-2);font-size:11.5px}
.tbl-wrap.tall{max-height:560px}
td.mono,.mono{font-family:ui-monospace,Consolas,monospace;font-size:12px;white-space:nowrap}
tr.divg td{background:color-mix(in srgb,var(--warn) 9%,transparent)}
.tag-w{background:color-mix(in srgb,var(--warn) 25%,transparent)!important;color:var(--ink)!important}
.tag.t-s{background:color-mix(in srgb,var(--s1) 18%,transparent);color:var(--good-ink)}
.tag.t-e{background:color-mix(in srgb,var(--s2) 18%,transparent);color:var(--s2)}
td select{background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:3px 6px;font-size:12px;max-width:190px}
td select.ovr{border-color:var(--brand);box-shadow:inset 0 0 0 1px var(--brand);font-weight:600}
.muted{color:var(--muted)}
.form{display:flex;flex-direction:column;gap:12px}
.fl{display:flex;justify-content:space-between;gap:16px;align-items:center;font-size:12.5px}
.fl>span:first-child{display:flex;flex-direction:column;gap:2px;min-width:0}
.fl small{color:var(--muted);font-size:11.5px;font-weight:400}
.fl.chk{justify-content:flex-start;align-items:flex-start}
.fl.chk input{margin-top:3px;accent-color:var(--brand)}
.fl.chk span{display:flex;flex-direction:column;gap:2px}
.fl select{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:6px 8px;max-width:260px}
.inp{display:inline-flex;align-items:center;gap:6px;color:var(--muted);flex:none}
.inp input{width:120px;text-align:right;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:6px 8px}
.inp input:disabled{opacity:.6}
.alrt{display:flex;gap:10px;align-items:center;font-size:13px;padding:6px 0;border-bottom:1px solid var(--grid)}
.st.info{background:var(--surface-2);color:var(--ink-2)}
.st.ok{background:color-mix(in srgb,var(--good) 14%,transparent);color:var(--good-ink)}
.st.warn{background:color-mix(in srgb,var(--warn) 22%,transparent);color:var(--ink)}
.st.crit{background:color-mix(in srgb,var(--crit) 16%,transparent);color:var(--crit-ink)}
.prose{max-width:900px;font-size:13.5px;line-height:1.6;color:var(--ink-2)}
.prose h2{font-size:18px;margin-bottom:8px}
.prose h3{font-size:14px;color:var(--char);margin:18px 0 4px}
.prose b{color:var(--ink)}
.prose ul{padding-left:20px}
@media print{.verdict,.card{break-inside:avoid}#prods,.period select{display:none}}
'''
head = f'''<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Simulador Reforma Tributária</title>
<style>{style}{extra}</style>
</head>
'''
unr = open(sys.argv[4], encoding='utf-8').read() if len(sys.argv)>4 else ''
out = head + body.replace('%%UNRAR%%', unr).replace('%%LOGO_L%%', L['l']).replace('%%LOGO_D%%', L['d'])
open(sys.argv[3], 'w', encoding='utf-8', newline='\n').write(out)
print(len(out), list(L))
