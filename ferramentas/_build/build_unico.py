import base64, re, sys
# uso: build_unico.py ../lcdpr_visualizador.html ../simulador_reforma_produtor.html ../calculadora_reforma_produtor.html ../reforma_tributaria_produtor.html
# Junta as duas ferramentas num arquivo só: cada uma roda num iframe próprio (sem conflito de nomes e de CSS),
# e o menu do topo troca entre "Importar notas" e "Preencher valores".
src = open(sys.argv[1], encoding='utf-8').read()
style = re.search(r'<style>(.*?)</style>', src, re.S).group(1)
L = dict(re.findall(r'<img class="logo logo-(l|d)"[^>]*src="(data:image[^"]+)"', src))

# dentro do arquivo único, o cabeçalho de cada ferramenta vira só a barra de botões
EMBUTIDO = '.top .logo,.top .sep,.top h1,#btnTheme{display:none!important}.top::after{display:none}.top-in{padding:8px 24px;min-height:50px}'

def app(path):
    h = open(path, encoding='utf-8').read()
    h = h.replace('</style>', EMBUTIDO + '</style>', 1)
    return base64.b64encode(h.encode('utf-8')).decode('ascii')

sim, calc = app(sys.argv[2]), app(sys.argv[3])
out = f'''<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Reforma Tributária Produtor</title>
<style>{style}
html,body{{height:100%}}
body{{display:flex;flex-direction:column;overflow:hidden}}
.top{{position:static}}
.menu{{display:flex;gap:4px;align-self:stretch;align-items:flex-end}}
.menu .tab{{font-size:14px;padding:10px 16px}}
.menu .tab svg{{width:16px;height:16px}}
.frames{{flex:1;min-height:0;position:relative}}
.frames iframe{{position:absolute;inset:0;width:100%;height:100%;border:0;background:var(--page)}}
.hidden{{display:none}}
@media (max-width:760px){{.top-in{{flex-wrap:wrap;padding:8px 16px;gap:8px}}.top .sep,.top h1 small{{display:none}}.menu{{order:3;width:100%}}.menu .tab{{flex:1;justify-content:center;padding:8px 6px}}}}
</style>
</head>
<body>
<header class="top">
  <div class="top-in">
    <img class="logo logo-l" alt="AgriTax Tributário & Contábil" src="{L['l']}"><img class="logo logo-d" alt="AgriTax Tributário & Contábil" src="{L['d']}"><span class="sep"></span>
    <h1>Reforma Tributária do Produtor Rural<small>IBS/CBS 2026–2033 · LC 214/2025</small></h1>
    <nav class="menu" role="tablist">
      <button class="tab" role="tab" data-app="sim"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 15V3M7 8l5-5 5 5M5 21h14"/></svg>Importar notas</button>
      <button class="tab" role="tab" data-app="calc"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20h4L19 9l-4-4L4 16v4zM14 6l4 4"/></svg>Preencher valores</button>
    </nav>
    <div class="spacer"></div>
    <button class="btn icon-btn" id="tema" title="Alternar tema claro/escuro">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg></button>
  </div>
</header>
<div class="frames"><iframe id="f-sim" title="Importar notas" class="hidden"></iframe><iframe id="f-calc" title="Preencher valores" class="hidden"></iframe></div>
<script type="text/plain" id="src-sim">{sim}</script>
<script type="text/plain" id="src-calc">{calc}</script>
<script>
const LS='agritax-reforma-aba';
const dec=id=>new TextDecoder().decode(Uint8Array.from(atob(document.getElementById(id).textContent),c=>c.charCodeAt(0)));
const tema=()=>document.documentElement.dataset.theme||'';
function aplicarTema(f){{ try{{ const r=f.contentDocument&&f.contentDocument.documentElement; if(!r) return;
  if(tema()) r.dataset.theme=tema(); else delete r.dataset.theme; }}catch(e){{}} }}
function abrir(k){{
  for(const b of document.querySelectorAll('.menu .tab')){{ const on=b.dataset.app===k; b.classList.toggle('on',on); b.setAttribute('aria-selected',on); }}
  for(const x of ['sim','calc']){{ const f=document.getElementById('f-'+x);
    if(x===k&&!f.srcdoc){{ f.onload=()=>aplicarTema(f); f.srcdoc=dec('src-'+x); }}
    f.classList.toggle('hidden',x!==k); }}
  try{{ localStorage.setItem(LS,k); }}catch(e){{}}
}}
for(const b of document.querySelectorAll('.menu .tab')) b.onclick=()=>abrir(b.dataset.app);
document.getElementById('tema').onclick=()=>{{ const r=document.documentElement,
  dark=r.dataset.theme?r.dataset.theme==='dark':matchMedia('(prefers-color-scheme: dark)').matches;
  r.dataset.theme=dark?'light':'dark'; for(const f of document.querySelectorAll('iframe')) aplicarTema(f); }};
let ini='sim'; try{{ ini=localStorage.getItem(LS)||'sim'; }}catch(e){{}}
abrir(ini==='calc'?'calc':'sim');
</script>
</body>
</html>
'''
open(sys.argv[4], 'w', encoding='utf-8', newline='\n').write(out)
print(len(out), list(L))
