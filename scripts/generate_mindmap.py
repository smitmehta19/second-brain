"""Generate the 3D mind map HTML from current vault structure.

Reads domains from domains.py (including auto-discovered ones) and
notes from the SQLite database to produce an up-to-date mind map.

Usage:
    python scripts/generate_mindmap.py                 # generate docs/mindmap.html
    python scripts/generate_mindmap.py --deploy        # generate + push to GitHub Pages
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

# Add project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.domains import DOMAINS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _build_graph_data() -> dict:
    """Build nodes and links from the current domain configuration."""
    nodes = []
    links = []
    node_id = 0

    def add_node(label, color, level, group=None):
        nonlocal node_id
        nid = node_id
        nodes.append({"id": nid, "label": label, "color": color, "level": level, "group": group or label})
        node_id += 1
        return nid

    def add_link(s, t):
        links.append({"source": s, "target": t})

    # Central — cosmic universe palette
    cid = add_node("Mind Palace", "#FFD700", 0, "Central")

    # Level 1 systems
    tg = add_node("Telegram Bot", "#00E5FF", 1, "Telegram")
    pp = add_node("Processing Pipeline", "#39FF14", 1, "Pipeline")
    nc = add_node("Notion Cloud", "#FF6B9D", 1, "Notion")
    ov = add_node("Knowledge Vault", "#B24BF3", 1, "Obsidian")
    oc = add_node("Oracle Cloud", "#FF6B35", 1, "Oracle")

    for nid in [tg, pp, nc, ov, oc]:
        add_link(cid, nid)

    # Telegram children
    for label in ["Text", "URLs", "Images", "Voice", "Videos",
                   "WhatsApp", "Instagram", "YouTube", "Substack"]:
        add_link(tg, add_node(label, "#00E5FF", 2, "Telegram"))

    # Pipeline children
    for label in ["Extractors", "AI Categorizer", "Smart Tags", "Auto-Summary", "Domain Detection"]:
        add_link(pp, add_node(label, "#39FF14", 2, "Pipeline"))

    # Notion children
    for label in ["Inbox DB", "Resources DB", "Auto-Properties", "Rate Retry"]:
        add_link(nc, add_node(label, "#FF6B9D", 2, "Notion"))

    # Oracle children
    for label in ["Free VM", "Docker", "Auto-Restart", "SQLite"]:
        add_link(oc, add_node(label, "#FF6B35", 2, "Oracle"))

    # Obsidian domains — dynamically from DOMAINS dict
    for domain_key, domain_info in DOMAINS.items():
        display = domain_key.replace("-", " ").title()
        did = add_node(display, "#B24BF3", 2, "Obsidian")
        add_link(ov, did)

        # Add keywords as sub-nodes (first 4)
        for kw in domain_info.get("keywords", [])[:4]:
            kid = add_node(kw.title(), "#B24BF3", 3, "Obsidian")
            add_link(did, kid)

    return {"nodes": nodes, "links": links}


def generate_html(output_path: Path) -> None:
    """Generate the complete mind map HTML file."""
    graph = _build_graph_data()
    nodes_json = json.dumps(graph["nodes"])
    links_json = json.dumps(graph["links"])

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Second Brain — Live Architecture</title>
<style>
  *{{margin:0;padding:0;box-sizing:border-box}}
  body{{background:#0a0a0a;overflow:hidden;font-family:'Segoe UI',sans-serif;color:#fff;width:100vw;height:100vh}}
  #graph{{width:100vw;height:100vh}}
  .ot{{position:fixed;top:24px;left:0;right:0;text-align:center;z-index:10;pointer-events:none}}
  .ot h1{{font-size:26px;font-weight:300;letter-spacing:10px;text-transform:uppercase;color:#d4a574;text-shadow:0 0 30px rgba(212,165,116,0.5)}}
  .ot .sub{{font-size:10px;letter-spacing:6px;color:rgba(255,255,255,0.2);margin-top:6px;text-transform:uppercase}}
  .ins{{position:fixed;bottom:20px;left:0;right:0;text-align:center;z-index:10;pointer-events:none;font-size:11px;letter-spacing:2px;color:rgba(255,255,255,0.18)}}
  .st{{position:fixed;top:24px;right:24px;z-index:10;pointer-events:none;text-align:right;font-size:10px;color:rgba(255,255,255,0.15);line-height:1.8}}
  #tip{{position:fixed;padding:8px 16px;background:rgba(15,15,25,0.9);border:1px solid rgba(255,255,255,0.1);border-radius:8px;font-size:13px;pointer-events:none;z-index:100;display:none;backdrop-filter:blur(10px)}}
  #tip .l{{font-size:14px;font-weight:600}}
  #tip .v{{font-size:9px;color:rgba(255,255,255,0.35);margin-top:2px;letter-spacing:2px;text-transform:uppercase}}
  .gen{{position:fixed;bottom:48px;left:0;right:0;text-align:center;z-index:10;pointer-events:none;font-size:9px;color:rgba(255,255,255,0.1)}}
</style>
</head>
<body>
<div id="graph"></div>
<div class="ot"><h1>Second Brain</h1><div class="sub">Live Architecture</div></div>
<div class="ins">Click &amp; drag to rotate &bull; Scroll to zoom &bull; Click node to focus</div>
<div class="st" id="stats"></div>
<div id="tip"><div class="l" id="tl"></div><div class="v" id="tv"></div></div>
<div class="gen">Auto-generated from vault structure &bull; {len(graph["nodes"])} nodes &bull; {len(graph["links"])} connections</div>

<script src="https://unpkg.com/three@0.160.0/build/three.min.js"></script>
<script src="https://unpkg.com/3d-force-graph@1.73.3/dist/3d-force-graph.min.js"></script>
<script>
(function(){{
  const nodes={nodes_json};
  const links={links_json};
  const LVL=['Core','System','Module','Component'];

  document.getElementById('stats').innerHTML=nodes.length+' nodes<br>'+links.length+' connections';

  function sz(l){{ return [28,16,9,5][l]||4; }}

  const el=document.getElementById('graph');
  const tip=document.getElementById('tip');
  const tl=document.getElementById('tl');
  const tv=document.getElementById('tv');
  let hoverNode=null;

  const G=ForceGraph3D()(el)
    .graphData({{nodes,links}})
    .backgroundColor('#00000000')
    .showNavInfo(false)
    .nodeRelSize(1)
    .nodeVal(n=>Math.pow(sz(n.level),2))
    .nodeColor(n=>n.color)
    .nodeOpacity(0.95)
    .nodeResolution(20)
    .linkWidth(l=>{{
      const s=typeof l.source==='object'?l.source:nodes[l.source];
      return s&&s.level===0?2.5:s&&s.level===1?1.2:0.6;
    }})
    .linkColor(l=>{{
      const s=typeof l.source==='object'?l.source:nodes[l.source];
      if(!s)return'rgba(255,255,255,0.05)';
      const c=new THREE.Color(s.color);
      return'rgba('+[c.r,c.g,c.b].map(v=>Math.round(v*255)).join(',')+',0.18)';
    }})
    .linkDirectionalParticles(l=>{{const s=typeof l.source==='object'?l.source:nodes[l.source];return s&&s.level<=1?4:2;}})
    .linkDirectionalParticleWidth(1.5)
    .linkDirectionalParticleSpeed(0.005)
    .linkDirectionalParticleColor(l=>{{const s=typeof l.source==='object'?l.source:nodes[l.source];return s?s.color:'#fff';}})
    .nodeThreeObject(node=>{{
      const s=sz(node.level);const col=new THREE.Color(node.color);const grp=new THREE.Group();
      const mat=new THREE.MeshStandardMaterial({{color:col,emissive:col,emissiveIntensity:node.level===0?0.8:0.4,roughness:0.3,metalness:0.1,transparent:true,opacity:0.95}});
      grp.add(new THREE.Mesh(new THREE.SphereGeometry(s,24,24),mat));
      grp.add(new THREE.Mesh(new THREE.SphereGeometry(s*(node.level===0?2.2:1.6),16,16),new THREE.MeshBasicMaterial({{color:col,transparent:true,opacity:node.level===0?0.1:0.04,side:THREE.BackSide}})));
      if(node.level<=1)grp.add(new THREE.Mesh(new THREE.SphereGeometry(s*(node.level===0?3.2:2.2),12,12),new THREE.MeshBasicMaterial({{color:col,transparent:true,opacity:0.02,side:THREE.BackSide}})));
      const cv=document.createElement('canvas');const cx=cv.getContext('2d');cv.width=512;cv.height=96;
      const fs=node.level===0?44:node.level===1?34:node.level===2?26:20;
      cx.font=(node.level<=1?'600 ':'400 ')+fs+'px "Segoe UI",sans-serif';cx.textAlign='center';cx.textBaseline='middle';cx.fillStyle='#fff';
      cx.shadowColor='rgba('+[col.r,col.g,col.b].map(v=>Math.round(v*255)).join(',')+',0.7)';cx.shadowBlur=14;cx.fillText(node.label,256,48);
      const sp=new THREE.Sprite(new THREE.SpriteMaterial({{map:new THREE.CanvasTexture(cv),transparent:true,opacity:node.level<=1?1:0.85,depthWrite:false}}));
      const sc=node.level===0?75:node.level===1?55:node.level===2?40:30;sp.scale.set(sc,sc*0.19,1);sp.position.y=s+(node.level===0?18:node.level===1?12:8);grp.add(sp);
      node._grp=grp;node._mat=mat;node._sp=sp;return grp;
    }})
    .onNodeHover(node=>{{el.style.cursor=node?'pointer':'default';hoverNode=node;if(node){{tl.textContent=node.label;tv.textContent=LVL[node.level]||'';tip.style.display='block';tip.style.borderColor=node.color;}}else{{tip.style.display='none';}}}})
    .onNodeClick(node=>{{if(!node)return;const d=node.level===0?350:node.level===1?200:130;G.cameraPosition({{x:node.x+d*0.4,y:node.y+d*0.3,z:node.z+d}},{{x:node.x,y:node.y,z:node.z}},1200);}});

  G.d3Force('charge').strength(n=>n.level===0?-800:n.level===1?-350:n.level===2?-100:-50).distanceMax(600);
  G.d3Force('link').distance(l=>{{const s=typeof l.source==='object'?l.source:nodes[l.source];if(!s)return 100;return s.level===0?180:s.level===1?100:60;}}).strength(0.6);

  el.addEventListener('mousemove',e=>{{tip.style.left=(e.clientX+14)+'px';tip.style.top=(e.clientY-8)+'px';}});

  const scene=G.scene();const renderer=G.renderer();
  renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.2;
  scene.add(new THREE.AmbientLight(0x404060,1.5));
  const p1=new THREE.PointLight(0xd4a574,2.5,900);p1.position.set(250,250,250);scene.add(p1);
  const p2=new THREE.PointLight(0x4a9eff,1.5,700);p2.position.set(-250,-100,-250);scene.add(p2);
  const p3=new THREE.PointLight(0xa78bfa,1,500);p3.position.set(0,-250,150);scene.add(p3);
  const sg=new THREE.BufferGeometry();const spos=new Float32Array(2500*3);for(let i=0;i<spos.length;i++)spos[i]=(Math.random()-0.5)*3500;
  sg.setAttribute('position',new THREE.BufferAttribute(spos,3));scene.add(new THREE.Points(sg,new THREE.PointsMaterial({{color:0xffffff,size:0.7,transparent:true,opacity:0.35}})));
  setTimeout(()=>G.cameraPosition({{x:0,y:100,z:500}},{{x:0,y:0,z:0}},0),200);

  let idle=true,idleTimer;
  el.addEventListener('pointerdown',()=>{{idle=false;clearTimeout(idleTimer);}});
  el.addEventListener('pointerup',()=>{{clearTimeout(idleTimer);idleTimer=setTimeout(()=>idle=true,3000);}});
  el.addEventListener('wheel',()=>{{idle=false;clearTimeout(idleTimer);idleTimer=setTimeout(()=>idle=true,3000);}});

  function getConn(nid){{const s=new Set([nid]);links.forEach(l=>{{const si=typeof l.source==='object'?l.source.id:l.source;const ti=typeof l.target==='object'?l.target.id:l.target;if(si===nid)s.add(ti);if(ti===nid)s.add(si);}});return s;}}

  let angle=0;
  (function tick(){{
    requestAnimationFrame(tick);
    if(idle){{angle+=0.002;const cam=G.camera();const dist=cam.position.length();cam.position.x=Math.sin(angle)*dist;cam.position.z=Math.cos(angle)*dist;cam.lookAt(0,0,0);}}
    const conn=hoverNode?getConn(hoverNode.id):null;
    nodes.forEach(n=>{{if(!n._mat)return;if(conn){{const on=conn.has(n.id);n._mat.emissiveIntensity=on?(n.level===0?1:0.8):0.05;n._mat.opacity=on?1:0.12;if(n._sp)n._sp.material.opacity=on?1:0.1;}}else{{n._mat.emissiveIntensity=n.level===0?0.8:0.4;n._mat.opacity=0.95;if(n._sp)n._sp.material.opacity=n.level<=1?1:0.85;}}}});
  }})();
}})();
</script>
</body>
</html>"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    logger.info("Mind map generated: %s (%d nodes, %d links)",
                output_path, len(graph["nodes"]), len(graph["links"]))


def deploy_to_github_pages(repo_dir: Path) -> None:
    """Push the docs/ folder to gh-pages branch for GitHub Pages hosting."""
    docs_dir = repo_dir / "docs"
    if not docs_dir.exists():
        logger.error("docs/ directory not found")
        return

    # Create a .nojekyll file so GitHub serves raw HTML
    (docs_dir / ".nojekyll").touch()

    # Create index.html that redirects to mindmap.html
    index = docs_dir / "index.html"
    if not index.exists():
        index.write_text(
            '<meta http-equiv="refresh" content="0;url=mindmap.html">',
            encoding="utf-8",
        )

    try:
        # Check if gh-pages branch exists
        result = subprocess.run(
            ["git", "branch", "--list", "gh-pages"],
            capture_output=True, text=True, cwd=repo_dir,
        )

        if "gh-pages" not in result.stdout:
            # Create orphan gh-pages branch
            subprocess.run(["git", "checkout", "--orphan", "gh-pages"], cwd=repo_dir, check=True)
            subprocess.run(["git", "rm", "-rf", "."], cwd=repo_dir, check=True)
        else:
            subprocess.run(["git", "checkout", "gh-pages"], cwd=repo_dir, check=True)

        # Copy docs content
        subprocess.run(["git", "add", "docs/"], cwd=repo_dir, check=True)
        subprocess.run(
            ["git", "commit", "-m", "Update mind map"],
            cwd=repo_dir, check=True,
        )
        subprocess.run(["git", "push", "origin", "gh-pages"], cwd=repo_dir, check=True)
        subprocess.run(["git", "checkout", "main"], cwd=repo_dir, check=True)

        logger.info("Deployed to GitHub Pages! Visit: https://YOUR_USER.github.io/secondbrain/")

    except subprocess.CalledProcessError as exc:
        logger.error("GitHub Pages deploy failed: %s", exc)
        # Switch back to main if we're stuck
        subprocess.run(["git", "checkout", "main"], cwd=repo_dir, capture_output=True)


def main():
    parser = argparse.ArgumentParser(description="Generate and deploy mind map")
    parser.add_argument("--deploy", action="store_true", help="Deploy to GitHub Pages")
    parser.add_argument("--output", type=str, default=None, help="Output path for HTML")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    output = Path(args.output) if args.output else project_root / "docs" / "mindmap.html"

    generate_html(output)
    print(f"Mind map generated: {output}")

    if args.deploy:
        deploy_to_github_pages(project_root)


if __name__ == "__main__":
    main()
