/* ─────────────────────────────────────────────────────────────────
   credits.js — Shared AI credits status pill
   Used by review, bucket, atlas, mindmap mastheads.
   Requires the canonical credits markup:
     <button id="creditsBtn"><span id="creditsDot"></span>…</button>
     <div id="creditsDrop"><div id="creditsContent"></div></div>
   ───────────────────────────────────────────────────────────────── */
(function(){
  function esc(s){ if(!s) return ''; var d=document.createElement('div'); d.textContent=s; return d.innerHTML; }
  var $credBtn = document.getElementById('creditsBtn');
  var $credDrop = document.getElementById('creditsDrop');
  var $credDot = document.getElementById('creditsDot');
  var $credContent = document.getElementById('creditsContent');
  if (!$credBtn) return;

  function authHeaders(){
    var t = (typeof localStorage !== 'undefined') ? localStorage.getItem('dashboard_token') : '';
    return t ? { 'Authorization': 'Bearer ' + t } : {};
  }

  $credBtn.addEventListener('click', function(e){
    e.stopPropagation();
    $credDrop.classList.toggle('open');
    if ($credDrop.classList.contains('open')) fetchCredits();
  });
  document.addEventListener('click', function(e){
    if (!$credDrop.contains(e.target) && !$credBtn.contains(e.target)) $credDrop.classList.remove('open');
  });

  window.fetchCredits = function(){
    fetch('/api/credits', { headers: authHeaders() })
      .then(function(r){ return r.json(); })
      .then(function(data){
        if (data.error){
          $credContent.innerHTML = '<div style="color:var(--text-disabled);font-size:12px;font-family:var(--font-display);font-style:italic">'+esc(data.error)+'</div>';
          $credDot.className = 'status-dot';
          return;
        }
        var providers = Object.keys(data);
        if (!providers.length){
          $credContent.innerHTML = '<div style="color:var(--text-disabled);font-size:12px;font-family:var(--font-display);font-style:italic">No data</div>';
          return;
        }
        var worstPct = 0;
        var h = '';
        providers.forEach(function(p){
          var d = data[p] || {};
          var reqsUsed = d.requests_used || 0;
          var reqsLimit = d.requests_limit || 1;
          var reqsPct = reqsLimit > 0 ? (reqsUsed / reqsLimit * 100) : 0;
          var tokensPct = (d.tokens_pct != null) ? d.tokens_pct : null;
          var pct = (tokensPct != null) ? Math.max(tokensPct, reqsPct) : reqsPct;
          if (d.status === 'exhausted') pct = 100;
          pct = Math.round(pct);
          worstPct = Math.max(worstPct, pct);
          var cls = (d.status === 'exhausted' || pct >= 90) ? 'red' : pct >= 60 ? '' : 'green';
          var fmt = function(n){ return (n == null) ? '?' : n.toLocaleString(); };
          var tip = fmt(reqsUsed) + ' / ' + fmt(reqsLimit) + ' reqs'
            + (d.tokens_used != null ? ' · ' + fmt(d.tokens_used) + ' / ' + fmt(d.tokens_limit) + ' tokens' : '')
            + (d.status === 'exhausted' ? ' · exhausted' : '');
          h += '<div class="credit-row" title="'+esc(tip)+'">';
          h += '<div class="credit-name">'+esc(p)+'</div>';
          h += '<div class="credit-bar"><div class="credit-bar-fill '+cls+'" style="width:'+pct+'%"></div></div>';
          h += '<div class="credit-pct">'+pct+'%</div>';
          h += '</div>';
        });
        $credContent.innerHTML = h;
        $credDot.className = 'status-dot ' + (worstPct >= 90 ? 'red' : worstPct >= 60 ? 'yellow' : 'green');
      }).catch(function(){
        $credContent.innerHTML = '<div style="color:var(--text-disabled);font-size:12px;font-family:var(--font-display);font-style:italic">Could not fetch</div>';
      });
  };
  fetchCredits();
  setInterval(fetchCredits, 60000);
})();
