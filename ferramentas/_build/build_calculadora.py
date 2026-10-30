import re, sys
# uso: build_calculadora.py ../lcdpr_visualizador.html calculadora_reforma_produtor.body.html ../calculadora_reforma_produtor.html
src = open(sys.argv[1], encoding='utf-8').read()
body = open(sys.argv[2], encoding='utf-8').read()
style = re.search(r'<style>(.*?)</style>', src, re.S).group(1)
L = dict(re.findall(r'<img class="logo logo-(l|d)"[^>]*src="(data:image[^"]+)"', src))
extra = r'''
/* calculadora */
.hidden{display:none!important}
.muted{color:var(--muted)}
.cols{display:grid;grid-template-columns:minmax(0,5fr) minmax(0,7fr);gap:16px;align-items:start}
@media (max-width:1100px){.cols{grid-template-columns:minmax(0,1fr)}}
.col-out{position:sticky;top:84px;max-height:calc(100vh - 100px);overflow-y:auto}
@media (max-width:1100px){.col-out{position:static;max-height:none}}
.top select.btn{max-width:190px}
.form{display:flex;flex-direction:column;gap:12px}
.fl{display:flex;justify-content:space-between;gap:16px;align-items:center;font-size:12.5px}
.fl>span:first-child{display:flex;flex-direction:column;gap:2px;min-width:0}
.fl small,.compras small,.trat small{display:block;color:var(--muted);font-size:11.5px;font-weight:400}
.fl.chk{justify-content:flex-start;align-items:flex-start}
.fl.chk input{margin-top:3px;accent-color:var(--brand);flex:none}
.fl.chk span{display:flex;flex-direction:column;gap:2px}
.fl select,.ativ select,.txt{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:6px 8px;max-width:260px;min-width:0}
.txt{width:240px}
.inp{display:inline-flex;align-items:center;gap:6px;color:var(--muted);flex:none}
input[type=number]{width:120px;text-align:right;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:6px 8px;color:var(--ink)}
.h3{font-size:12.5px;margin:6px 0 0;color:var(--brand);font-weight:650}
.ativ{border:1px solid var(--border);border-radius:10px;padding:10px 12px;margin-bottom:10px;background:var(--surface-2);display:flex;flex-direction:column;gap:8px}
.ativ-top{display:flex;gap:8px;align-items:center}
.ativ select.wide{width:100%;max-width:none;flex:1}
.ativ-nums{display:flex;flex-wrap:wrap;gap:10px}
.ativ-nums label{display:flex;flex-direction:column;gap:3px;font-size:11.5px;color:var(--ink-2);flex:1 1 110px;min-width:0}
.ativ-nums input{width:100%}
.mini{font-size:12px}
.compras{width:100%;border-collapse:collapse;font-size:12.5px}
.compras th,.trat th{font-size:11px;color:var(--muted);font-weight:600;text-align:left;padding:4px 6px;border-bottom:1px solid var(--axis)}
.compras td,.trat td{padding:7px 6px;border-bottom:1px solid var(--grid);vertical-align:middle}
.compras th.v,.trat th.v,td.v{text-align:right}
.compras input{width:104px}
.compras input.pct{width:62px}
.trat{width:100%;border-collapse:collapse;font-size:12.5px}
details>summary{cursor:pointer}
.verdict{border-left:5px solid var(--corn)}
.v-head{display:flex;gap:14px;align-items:flex-start}
.v-ico{flex:none;width:34px;height:34px;border-radius:10px;background:var(--brand);color:var(--on-brand);display:grid;place-items:center;font-weight:700;font-size:16px}
.verdict h2{font-size:16px}
.v-p{margin:4px 0 0;font-size:13px;color:var(--ink-2)}
.verdict .muted{margin:6px 0 0;font-size:12px}
.kpis{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.kpi{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:12px 14px;border-top:3px solid var(--c)}
.kpi .k{font-size:11.5px;color:var(--muted)}
.kpi .v{font-size:19px;font-weight:650;font-variant-numeric:tabular-nums;margin:2px 0;color:var(--ink)}
.kpi .d{font-size:11.5px;color:var(--ink-2)}
.st{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;font-weight:600;border-radius:999px;padding:3px 10px;white-space:nowrap}
.st.ok{background:color-mix(in srgb,var(--good) 14%,transparent);color:var(--good-ink)}
.st.warn{background:color-mix(in srgb,var(--warn) 22%,transparent);color:var(--ink)}
.st.crit{background:color-mix(in srgb,var(--crit) 16%,transparent);color:var(--crit-ink)}
.al{margin:6px 0 0;padding-left:18px;font-size:12.5px;color:var(--ink-2)}
.al li{margin:6px 0}
.al b{color:var(--ink)}
.tbl-wrap{overflow-x:auto}
table.proj{width:100%;border-collapse:collapse;font-size:12px;font-variant-numeric:tabular-nums}
table.proj th{font-size:11px;color:var(--muted);text-align:left;padding:4px 6px;border-bottom:1px solid var(--axis)}
table.proj th.v{text-align:right}
table.proj td{padding:4px 6px;border-bottom:1px solid var(--grid);white-space:nowrap}
table.proj tr.sec td{background:var(--surface-2);font-weight:650;color:var(--char);padding-top:8px}
table.proj tr.sec.sa td{box-shadow:inset 4px 0 0 var(--s2)}
table.proj tr.sec.sb td{box-shadow:inset 4px 0 0 var(--s4)}
table.proj tr.tot td{font-weight:600}
table.proj tr.big td{font-weight:700;border-bottom:2px solid var(--axis)}
table.proj tr.dif td{font-weight:700;color:var(--char)}
table.proj tr.aliq td{color:var(--ink-2)}
.prose{font-size:13px;line-height:1.55;color:var(--ink-2)}
.prose ul{padding-left:18px;margin:0}
.prose li{margin:6px 0}
.prose b{color:var(--ink)}
@media (max-width:1000px){.top-in{flex-wrap:wrap;padding:10px 16px;gap:10px}.top .sep,.top .spacer{display:none}.top h1{flex:1 1 200px}.top{position:static}main{padding:16px 16px 48px}}
@media (max-width:560px){.fl{flex-wrap:wrap}.fl select,.txt{max-width:100%;width:100%}.compras input{width:88px}.compras input.pct{width:56px}.kpis{grid-template-columns:minmax(0,1fr)}.st{white-space:normal}}
@media print{.top .btn,#btnAddAtiv,[data-del]{display:none}.cols{display:block}.col-out{position:static;max-height:none;overflow:visible;margin-top:16px}.card{break-inside:avoid}}
'''
head = f'''<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Calculadora Reforma Tributária</title>
<style>{style}{extra}</style>
</head>
'''
out = head + body.replace('%%LOGO_L%%', L['l']).replace('%%LOGO_D%%', L['d'])
open(sys.argv[3], 'w', encoding='utf-8', newline='\n').write(out)
print(len(out), list(L))
