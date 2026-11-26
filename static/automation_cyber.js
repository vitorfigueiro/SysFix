// ==============================================================================
// SETOR AUTOMAÇÃO (CÂMERAS, INFRA, IOT, ALARMES) & SETOR CIBERSEGURANÇA
// ==============================================================================

let automacaoPilarAtual = 'todas';
let automacaoAtivosCache = [];
let cyberSubTabAtual = 'auditoria';
let wikiSecaoAtual = 'noticias';
let cyberChecklistCache = [];
let cyberAchadosCache = [];
let cyberWikiCache = { noticias_mundo_cyber: { itens: [] }, publicados_internos: [], guias_referencia: [] };
let cyberCenariosCache = [];
let cyberCenarioSelecionadoId = '';
let cyberAutomacoesCache = [];
let govKeyEquipeCache = [];
let cyberBotsDockMinimizado = false;

const CYBER_BOTS_DEF = [
  {
    id: 'bot_net',
    tab: 'auditoria',
    nome: 'Byte-Scanner',
    pacote: 'SCAN',
    cor: '#7C3AED',
    status: 'Auditando domínios Web/TLS e portas LAN',
  },
  {
    id: 'bot_lab',
    tab: 'laboratorio',
    nome: 'Purple-Bot',
    pacote: 'LAB',
    cor: '#D946EF',
    status: 'Simulador didático Ataque × Mitigação',
  },
  {
    id: 'bot_auto',
    tab: 'automacoes',
    nome: 'Auto-Shield',
    pacote: 'AUTO',
    cor: '#0EA5E9',
    status: 'Caça automática de falhas e proteção',
  },
  {
    id: 'bot_vuln',
    tab: 'achados',
    nome: 'Patch-Bot',
    pacote: 'VULN',
    cor: '#EC4899',
    status: 'Transportando fila de vulnerabilidades',
  },
  {
    id: 'bot_cis',
    tab: 'checklists',
    nome: 'Guard-CIS',
    pacote: 'CIS',
    cor: '#10B981',
    status: 'Validando pacotes CIS v8, NIST e LGPD',
  },
  {
    id: 'bot_kb',
    tab: 'conhecimento',
    nome: 'Wiki-Core',
    pacote: 'NEWS',
    cor: '#6366F1',
    status: 'Notícias reais cyber + playbooks internos',
  },
  {
    id: 'bot_gov',
    tab: 'governanca',
    nome: 'Gov-Key',
    pacote: 'WHO',
    cor: '#F59E0B',
    status: 'Controlando quem pode executar tarefas',
  },
];

function renderizarCyberBotsDock(escaneando = false) {
  const dock = document.getElementById('cyberBotsTrack');
  const body = document.getElementById('cyberBotsBody');
  const btnMin = document.getElementById('btnMinimizarCyberBots');
  if (!dock || !body) return;

  dock.classList.toggle('scanning', escaneando);
  body.classList.toggle('hidden', cyberBotsDockMinimizado);
  if (btnMin) {
    btnMin.textContent = cyberBotsDockMinimizado ? 'Expandir Mini Seres' : 'Minimizar';
  }

  const isAdmin = sessaoAtual?.user?.role === 'admin';
  const bots = isAdmin ? CYBER_BOTS_DEF : CYBER_BOTS_DEF.filter(b => b.tab !== 'governanca');

  body.innerHTML = bots.map(bot => {
    const ativo = cyberSubTabAtual === bot.tab;
    return `
      <button type="button" onclick="alternarCyberTab('${bot.tab}')" title="${escapeHtml(bot.status)}"
        class="cyber-bot-card flex items-center gap-2 px-2.5 py-2 rounded-xl border text-left transition-all ${ativo ? 'bg-violet-600 text-white border-violet-500 shadow-lg shadow-violet-600/25' : 'bg-white/95 dark:bg-[#141024]/95 text-slate-800 dark:text-white border-violet-200 dark:border-violet-800 hover:border-violet-400'}">
        <svg class="cyber-bot-svg w-8 h-8 shrink-0" viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
          <rect class="cyber-packet" x="27" y="4" width="17" height="11" rx="3" fill="${bot.cor}" stroke="#FFFFFF" stroke-width="1.5"/>
          <text x="35.5" y="11.5" text-anchor="middle" font-size="5" font-weight="bold" fill="#FFFFFF">${bot.pacote}</text>
          <rect x="9" y="17" width="20" height="17" rx="5" fill="${ativo ? '#FFFFFF' : '#2E1065'}" stroke="${bot.cor}" stroke-width="2"/>
          <circle cx="15.5" cy="25" r="2.2" fill="${ativo ? bot.cor : '#C4B5FD'}"/>
          <circle cx="22.5" cy="25" r="2.2" fill="${ativo ? bot.cor : '#C4B5FD'}"/>
          <path d="M19 12V17" stroke="${bot.cor}" stroke-width="2" stroke-linecap="round"/>
          <circle cx="19" cy="10" r="2.5" fill="${bot.cor}"/>
          <path d="M28 21L33 15" stroke="${bot.cor}" stroke-width="2.2" stroke-linecap="round"/>
          <rect x="12" y="35" width="4" height="6" rx="1.5" fill="${bot.cor}"/>
          <rect x="22" y="35" width="4" height="6" rx="1.5" fill="${bot.cor}"/>
        </svg>
        <div class="min-w-0">
          <div class="text-[11px] font-extrabold truncate">${escapeHtml(bot.nome)}</div>
          <div class="text-[10px] ${ativo ? 'text-violet-100' : 'text-slate-500 dark:text-violet-300/80'} truncate">${escapeHtml(bot.status)}</div>
        </div>
      </button>
    `;
  }).join('');
}

function alternarMinimizarCyberBots() {
  cyberBotsDockMinimizado = !cyberBotsDockMinimizado;
  renderizarCyberBotsDock(false);
}

// ==============================================================================
// SETOR AUTOMAÇÃO (CFTV, INFRAESTRUTURA, IOT & ALARMES)
// ==============================================================================

function alternarAutomacaoPilar(pilar) {
  automacaoPilarAtual = pilar;
  const mapa = {
    todas: 'btnAutTabTodas',
    cameras: 'btnAutTabCameras',
    infraestrutura: 'btnAutTabInfra',
    iot: 'btnAutTabIot',
    alarmes: 'btnAutTabAlarmes',
  };
  const act = 'px-3 py-1.5 text-xs font-bold rounded-lg bg-violet-600 text-white';
  const inact = 'px-3 py-1.5 text-xs font-semibold rounded-lg text-violet-800 dark:text-violet-200';
  Object.entries(mapa).forEach(([k, id]) => {
    const el = document.getElementById(id);
    if (typeof aplicarEstadoAbaBotao === 'function') {
      aplicarEstadoAbaBotao(el, k === pilar, act, inact);
    } else if (el) {
      el.className = k === pilar ? act : inact;
    }
  });
  if (pilar !== 'todas' && document.getElementById('aut_pilar')) {
    document.getElementById('aut_pilar').value = pilar;
  }
  renderizarTabelaAutomacao();
}

async function carregarPainelAutomacao() {
  if (!temPermissao('ver_automacao') && !temPermissao('ver_dashboard_automacao')) return;
  const res = await apiFetch('/automacao/ativos');
  if (!res.ok) return;
  const data = await res.json();
  const r = data.resumo || {};
  automacaoAtivosCache = Array.isArray(data.ativos) ? data.ativos : [];

  const setTxt = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  setTxt('kpiAutTotal', r.total_ativos ?? 0);
  setTxt('kpiAutTaxa', `${r.taxa_conformidade_percent ?? 100}%`);
  setTxt('kpiAutAlertas', r.alertas ?? 0);
  setTxt('kpiAutCriticos', r.criticos ?? 0);

  renderizarTabelaAutomacao();
}

function renderizarTabelaAutomacao() {
  const tbody = document.getElementById('tabelaAutomacaoAtivos');
  if (!tbody) return;
  const busca = (document.getElementById('aut_busca')?.value || '').trim().toLowerCase();
  const podeGerir = temPermissao('gerenciar_automacao');

  const filtrados = automacaoAtivosCache.filter(a => {
    if (automacaoPilarAtual !== 'todas' && a.pilar !== automacaoPilarAtual) return false;
    if (busca) {
      return [a.nome, a.subtipo, a.localizacao, a.ip_mac, a.vlan, a.fabricante_modelo]
        .some(v => String(v || '').toLowerCase().includes(busca));
    }
    return true;
  });

  if (filtrados.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" class="p-4 text-center text-slate-400">Nenhum dispositivo ou item auditado encontrado neste pilar.</td></tr>';
    return;
  }

  const pilaresLabels = {
    cameras: '📷 Câmeras / CFTV',
    infraestrutura: '🏗️ Infraestrutura',
    iot: '🌐 IoT / Sensor',
    alarmes: '🚨 Alarme / Acesso',
  };

  tbody.innerHTML = filtrados.map(a => {
    const chk = a.checks || {};
    const pts = Object.values(chk).filter(Boolean).length;
    const badgeCls = a.status_auditoria === 'Conforme'
      ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
      : (a.status_auditoria === 'Crítico'
        ? 'bg-rose-100 text-rose-700 border-rose-300'
        : 'bg-amber-50 text-amber-800 border-amber-200');
    return `
      <tr>
        <td class="p-3 text-[11px] font-bold text-violet-700 dark:text-violet-300">${escapeHtml(pilaresLabels[a.pilar] || a.pilar)}</td>
        <td class="p-3">
          <div class="font-bold text-slate-900 dark:text-white">${escapeHtml(a.nome)}</div>
          <div class="text-[10px] text-slate-400">${escapeHtml(a.fabricante_modelo || '-')} • ${escapeHtml(a.localizacao || '-')}</div>
        </td>
        <td class="p-3 font-mono text-[11px] text-slate-700 dark:text-violet-200">${escapeHtml(a.ip_mac || '---')}<div class="text-[10px] text-slate-400">${escapeHtml(a.vlan ? `VLAN ${a.vlan}` : '')}</div></td>
        <td class="p-3 text-[11px] text-slate-600 dark:text-violet-200">${pts}/5 itens OK<div class="text-[10px] text-slate-400">${escapeHtml(a.firmware || '')}</div></td>
        <td class="p-3"><span class="px-2 py-0.5 rounded-full text-[10px] font-bold border ${badgeCls}">${escapeHtml(a.status_auditoria)}</span></td>
        <td class="p-3 text-right space-x-1">
          ${podeGerir ? `
            <button onclick="revalidarAtivoAutomacao('${escapeHtml(a.id)}')" class="px-2 py-1 rounded-lg bg-emerald-50 text-emerald-700 text-[10px] font-bold" title="Marcar tudo Conforme">✓ Conforme</button>
            <button onclick="excluirAtivoAutomacao('${escapeHtml(a.id)}')" class="px-2 py-1 rounded-lg bg-rose-50 text-rose-600 text-[10px] font-semibold">✕</button>
          ` : ''}
        </td>
      </tr>
    `;
  }).join('');
}

async function criarAtivoAutomacao(event) {
  event.preventDefault();
  const payload = {
    pilar: document.getElementById('aut_pilar').value,
    nome: document.getElementById('aut_nome').value.trim(),
    subtipo: document.getElementById('aut_subtipo').value.trim(),
    localizacao: document.getElementById('aut_loc').value.trim(),
    ip_mac: document.getElementById('aut_ip_mac').value.trim(),
    vlan: document.getElementById('aut_vlan').value.trim(),
    fabricante_modelo: document.getElementById('aut_modelo').value.trim(),
    firmware: document.getElementById('aut_firmware').value.trim(),
    senha_padrao_alterada: document.getElementById('aut_chk_senha').checked,
    firmware_atualizado: document.getElementById('aut_chk_fw').checked,
    rede_segmentada: document.getElementById('aut_chk_vlan').checked,
    energia_backup_ok: document.getElementById('aut_chk_energia').checked,
    monitoramento_ativo: document.getElementById('aut_chk_mon').checked,
    observacoes: document.getElementById('aut_obs').value.trim(),
  };

  const res = await apiFetch('/automacao/ativos', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    mostrarToast(await extrairErroResposta(res, 'Erro ao registrar auditoria de automação.'), 'erro');
    return;
  }
  event.target.reset();
  if (automacaoPilarAtual !== 'todas') {
    document.getElementById('aut_pilar').value = automacaoPilarAtual;
  }
  mostrarToast('Ativo de automação auditado e salvo!', 'sucesso');
  await carregarPainelAutomacao();
}

async function revalidarAtivoAutomacao(itemId) {
  const res = await apiFetch(`/automacao/ativos/${encodeURIComponent(itemId)}`, {
    method: 'PATCH',
    body: JSON.stringify({
      senha_padrao_alterada: true,
      firmware_atualizado: true,
      rede_segmentada: true,
      energia_backup_ok: true,
      monitoramento_ativo: true,
      status_auditoria: 'Conforme',
    }),
  });
  if (res.ok) {
    mostrarToast('Ativo marcado como Conforme!', 'sucesso');
    await carregarPainelAutomacao();
  }
}

async function excluirAtivoAutomacao(itemId) {
  const res = await apiFetch(`/automacao/ativos/${encodeURIComponent(itemId)}`, { method: 'DELETE' });
  if (res.ok) await carregarPainelAutomacao();
}

function baixarRelatorioAutomacaoPdf() {
  abrirPdfAutenticado('/automacao/relatorio/pdf', 'Erro ao gerar relatório PDF de Automação.');
}

// ==============================================================================
// SETOR CIBERSEGURANÇA (BYTE-SCANNER, LAB DIDÁTICO, AUTOMAÇÕES, WIKI & GOV-KEY)
// ==============================================================================

const CYBER_ALL_TABS = ['auditoria', 'laboratorio', 'automacoes', 'achados', 'checklists', 'conhecimento', 'governanca'];

function alternarCyberTab(tab) {
  const isAdmin = sessaoAtual?.user?.role === 'admin';
  if (tab === 'governanca' && !isAdmin) tab = 'auditoria';
  cyberSubTabAtual = tab;

  const act = 'px-2.5 py-1.5 text-xs font-bold rounded-lg bg-violet-600 text-white';
  const inact = 'px-2.5 py-1.5 text-xs font-semibold rounded-lg text-violet-800 dark:text-violet-200';

  CYBER_ALL_TABS.forEach(key => {
    const el = document.getElementById(`cyberTab_${key}`);
    if (el) el.classList.toggle('hidden', key !== tab || (key === 'governanca' && !isAdmin));
    const btn = document.getElementById(`btnCyberTab_${key}`);
    if (btn && key === 'governanca') {
      btn.classList.toggle('hidden', !isAdmin);
    }
    if (typeof aplicarEstadoAbaBotao === 'function') {
      aplicarEstadoAbaBotao(btn, key === tab, act, inact);
    } else if (btn) {
      const oculto = btn.classList.contains('hidden');
      btn.className = key === tab ? act : inact;
      if (oculto) btn.classList.add('hidden');
    }
  });
  renderizarCyberBotsDock(false);
}

function alternarCampoPortasByteScanner() {
  const tipo = document.getElementById('cyb_aud_tipo')?.value || 'protocolos_pacotes';
  const box = document.getElementById('grupoPortasCustomCyber');
  if (box) box.classList.toggle('hidden', tipo === 'web_tls');
}

function autoAjustarModoByteScanner(valor) {
  const sel = document.getElementById('cyb_aud_tipo');
  if (!sel) return;
  if (sel.value === 'protocolos_pacotes') {
    alternarCampoPortasByteScanner();
    return;
  }
  const txt = String(valor || '').trim().toLowerCase();
  if (!txt) return;
  const pareceIpOuCidr = /^(\d{1,3}\.){3}\d{1,3}(\/\d{1,2})?$/.test(txt);
  if (pareceIpOuCidr) {
    sel.value = 'rede_privada';
  } else if (txt.startsWith('http://') || txt.startsWith('https://') || /[a-z]/i.test(txt)) {
    sel.value = 'web_tls';
  }
  alternarCampoPortasByteScanner();
}

async function carregarPainelCyber() {
  if (!temPermissao('ver_ciberseguranca') && !temPermissao('ver_dashboard_cyber')) return;
  renderizarCyberBotsDock(false);
  alternarCyberTab(cyberSubTabAtual);

  const tarefas = [carregarResumoCyber()];
  if (temPermissao('ver_ciberseguranca')) {
    tarefas.push(
      carregarAchadosCyber(),
      carregarChecklistCyber(),
      carregarConhecimentoCyber(false),
      carregarLaboratorioCyber(),
      carregarAutomacoesCyber()
    );
  }
  if (sessaoAtual?.user?.role === 'admin') {
    tarefas.push(carregarOpcoesEquipeGovKey());
  }
  await Promise.all(tarefas);
}

async function carregarResumoCyber() {
  const res = await apiFetch('/cyber/resumo');
  if (!res.ok) return;
  const d = await res.json();
  const gov = d.governanca || {};

  const setTxt = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  setTxt('kpiCybMaturidade', `${d.maturidade_percent ?? 0}%`);
  setTxt('kpiCybAbertos', d.achados_abertos ?? 0);
  setTxt('kpiCybCriticos', `${d.achados_criticos ?? 0} críticos`);
  setTxt('kpiCybAuditorias', `${d.total_auditorias ?? 0} / ${d.total_automacoes ?? 0} auto`);

  const qtdUsers = (gov.usuarios_autorizados || []).length;
  const govBadge = document.getElementById('kpiCybGovBadge');
  if (govBadge) {
    if (gov.permitir_auditoria_rede) {
      govBadge.textContent = qtdUsers > 0
        ? `🟢 Ativo (${qtdUsers} operador${qtdUsers > 1 ? 'es' : ''})`
        : '🟢 Autorizada pelo Gestor';
      govBadge.className = 'text-xs font-extrabold text-emerald-600 dark:text-emerald-400 mt-1.5';
    } else {
      govBadge.textContent = '🔒 Bloqueada (Aguarda Gestor)';
      govBadge.className = 'text-xs font-extrabold text-amber-600 dark:text-amber-400 mt-1.5';
    }
  }

  // Preenche seção Gov-Key
  const chkGov = document.getElementById('gov_permitir_auditoria');
  const txtEscopo = document.getElementById('gov_escopo');
  const txtObs = document.getElementById('gov_termo');
  const infoGov = document.getElementById('gov_info_auditoria');
  if (chkGov) chkGov.checked = Boolean(gov.permitir_auditoria_rede);
  if (txtEscopo) txtEscopo.value = (gov.escopo_autorizado || []).join('\n');
  if (txtObs) txtObs.value = gov.observacao_termo || '';
  if (infoGov) {
    infoGov.textContent = gov.permitir_auditoria_rede
      ? `Autorizado por ${gov.autorizado_por || 'Gestor'} (${formatarDataBR(gov.autorizado_em)}). Operadores liberados: ${qtdUsers > 0 ? qtdUsers + ' colaborador(es) selecionado(s)' : 'Equipe com permissão de auditoria'}.`
      : 'As tarefas ativas de auditoria estão pausadas. Marque a autorização abaixo e escolha quem pode realizar as tarefas.';
  }
  renderizarCheckboxesGovKey(gov);

  // Lista de auditorias recentes
  const listaAud = document.getElementById('listaAuditoriasCyber');
  const auds = Array.isArray(d.ultimas_auditorias) ? d.ultimas_auditorias : [];
  window.cyberAuditoriasCache = auds;
  if (listaAud) {
    listaAud.innerHTML = auds.length === 0
      ? '<p class="text-slate-400">Nenhuma auditoria executada ou importada ainda.</p>'
      : auds.map((a, idx) => `
        <div class="p-3 rounded-xl bg-violet-50/70 dark:bg-[#1C1633] border border-violet-200/80 dark:border-violet-800/70 flex flex-wrap justify-between items-center gap-2">
          <div>
            <div class="font-bold text-slate-900 dark:text-white">${escapeHtml(a.alvo)} <span class="text-[10px] font-semibold text-violet-600">(${escapeHtml(a.tipo)})</span></div>
            <div class="text-[11px] text-slate-500 dark:text-violet-300/80">Hosts: ${a.hosts_ativos} • Portas/Itens: ${a.portas_expostas} • Achados: ${a.achados_count} • Por ${escapeHtml(a.executado_por)}</div>
          </div>
          <div class="flex items-center gap-1.5 flex-wrap">
            <span class="px-2.5 py-1 rounded-full text-xs font-extrabold ${a.score >= 80 ? 'bg-emerald-100 text-emerald-800' : (a.score >= 50 ? 'bg-amber-100 text-amber-800' : 'bg-rose-100 text-rose-800')}">Score ${a.score}/100</span>
            <button type="button" onclick="abrirDetalheAuditoriaCyber(${idx})" class="px-2.5 py-1 rounded-lg bg-violet-600 hover:bg-violet-700 text-white text-[11px] font-bold transition-all">🔎 Ver Tudo</button>
            <button type="button" onclick="baixarDocumentoAuditoriaCyber('${escapeHtml(a.id)}', 'txt')" class="px-2.5 py-1 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white text-[11px] font-bold transition-all">📄 Baixar Doc (.txt)</button>
          </div>
        </div>
      `).join('');
  }
}

async function carregarOpcoesEquipeGovKey() {
  if (sessaoAtual?.user?.role !== 'admin') return;
  const res = await apiFetch('/cyber/equipe-opcoes');
  if (!res.ok) return;
  govKeyEquipeCache = await res.json();
  const gov = sessaoAtual?.tenant?.cyber_governanca || {};
  renderizarCheckboxesGovKey(gov);
}

function renderizarCheckboxesGovKey(gov = {}) {
  const box = document.getElementById('listaOperadoresGovKey');
  if (!box) return;
  if (!govKeyEquipeCache.length) {
    box.innerHTML = '<p class="text-slate-400 col-span-2">Carregando colaboradores ativos da empresa...</p>';
    return;
  }

  const autorizadosSet = new Set((gov.usuarios_autorizados || []).map(u => String(u).toLowerCase()));
  const nuncaConfigurado = !gov.restringir_usuarios && autorizadosSet.size === 0;

  box.innerHTML = govKeyEquipeCache.map(m => {
    const emailLow = String(m.email || '').toLowerCase();
    const uidLow = String(m.uid || '').toLowerCase();
    const marcado = nuncaConfigurado
      ? Boolean(m.pode_cyber)
      : (autorizadosSet.has(emailLow) || autorizadosSet.has(uidLow));
    return `
      <label class="flex items-start gap-2.5 p-3 rounded-xl bg-white dark:bg-[#141024] border border-violet-200 dark:border-violet-800 cursor-pointer hover:border-violet-400 transition-all">
        <input type="checkbox" name="gov_operador_chk" value="${escapeHtml(m.email || m.uid)}" data-pode-cyber="${m.pode_cyber ? '1' : '0'}" ${marcado ? 'checked' : ''} class="mt-0.5 rounded text-violet-600">
        <div class="min-w-0 flex-1">
          <div class="font-bold text-slate-900 dark:text-white truncate">${escapeHtml(m.nome)}</div>
          <div class="text-[10px] text-slate-500 dark:text-violet-300/80 truncate">${escapeHtml(m.email)}</div>
          <div class="flex flex-wrap gap-1 mt-1">
            <span class="px-1.5 py-0.5 rounded text-[9px] font-bold ${m.role === 'admin' ? 'bg-violet-600 text-white' : 'bg-violet-100 dark:bg-violet-900/60 text-violet-800 dark:text-violet-200'}">${m.role === 'admin' ? 'Gestor / Admin' : 'Colaborador'}</span>
            ${m.mfa_enabled ? '<span class="px-1.5 py-0.5 rounded text-[9px] font-bold bg-emerald-100 text-emerald-800">2FA Ativo</span>' : '<span class="px-1.5 py-0.5 rounded text-[9px] font-semibold bg-amber-50 text-amber-700">Sem 2FA</span>'}
          </div>
        </div>
      </label>
    `;
  }).join('');
}

function selecionarOperadoresGovKey(modo) {
  document.querySelectorAll('input[name="gov_operador_chk"]').forEach(chk => {
    chk.checked = modo === 'todos' ? true : chk.getAttribute('data-pode-cyber') === '1';
  });
}

async function salvarGovernancaCyber(event) {
  event.preventDefault();
  const permitir = document.getElementById('gov_permitir_auditoria').checked;
  const selecionados = Array.from(document.querySelectorAll('input[name="gov_operador_chk"]:checked'))
    .map(el => el.value.trim())
    .filter(Boolean);
  const escopo = (document.getElementById('gov_escopo')?.value || '')
    .split(/\r?\n|,/)
    .map(s => s.trim())
    .filter(Boolean);
  const termo = document.getElementById('gov_termo').value.trim();

  const res = await apiFetch('/cyber/governanca', {
    method: 'PUT',
    body: JSON.stringify({
      permitir_auditoria_rede: permitir,
      usuarios_autorizados: selecionados,
      restringir_usuarios: true,
      escopo_autorizado: escopo,
      observacao_termo: termo,
    }),
  });
  if (!res.ok) {
    mostrarToast(await extrairErroResposta(res, 'Erro ao salvar Gov-Key.'), 'erro');
    return;
  }
  const data = await res.json();
  sessaoAtual.tenant = data.tenant;
  mostrarToast('Gov-Key atualizado com os operadores autorizados!', 'sucesso');
  await carregarResumoCyber();
}

async function executarAuditoriaCyber(event) {
  event.preventDefault();
  const alvo = document.getElementById('cyb_aud_alvo').value.trim();
  const tipo = document.getElementById('cyb_aud_tipo').value;
  const portasCustom = document.getElementById('cyb_aud_portas')?.value.trim() || '';
  const registrarAchados = document.getElementById('cyb_aud_gerar_achados').checked;
  const btn = document.getElementById('btnExecutarAuditoriaCyber');

  if (btn) btn.disabled = true;
  renderizarCyberBotsDock(true);
  try {
    const res = await apiFetch('/cyber/auditorias/executar', {
      method: 'POST',
      body: JSON.stringify({
        alvo,
        tipo_auditoria: tipo,
        portas_custom: portasCustom,
        registrar_achados: registrarAchados,
      }),
    });
    if (!res.ok) {
      mostrarToast(await extrairErroResposta(res, 'Auditoria bloqueada ou inválida.'), 'erro');
      return;
    }
    const data = await res.json();
    mostrarToast(`Byte-Scanner concluído! Score ${data.auditoria.score}/100 (${data.achados_criados} falha(s) registrada(s)).`, 'sucesso');
    renderizarDetalheAuditoriaCyber(data.auditoria);
    await Promise.all([carregarResumoCyber(), carregarAchadosCyber()]);
  } catch (e) {
    mostrarToast('Falha ao executar auditoria defensiva.', 'erro');
  } finally {
    if (btn) btn.disabled = false;
    renderizarCyberBotsDock(false);
  }
}

let ultimaAuditoriaDetalhadaCyber = null;

function abrirDetalheAuditoriaCyber(idx) {
  const lista = Array.isArray(window.cyberAuditoriasCache) ? window.cyberAuditoriasCache : [];
  if (lista[idx]) {
    renderizarDetalheAuditoriaCyber(lista[idx]);
  }
}

function fecharDetalheAuditoriaCyber() {
  document.getElementById('painelDetalheAuditoriaCyber')?.classList.add('hidden');
}

function renderizarDetalheAuditoriaCyber(a) {
  const box = document.getElementById('painelDetalheAuditoriaCyber');
  if (!box || !a) return;
  ultimaAuditoriaDetalhadaCyber = a;
  const det = a.detalhes || {};
  const achados = Array.isArray(a.achados) ? a.achados : (Array.isArray(det.achados) ? det.achados : []);

  const corSev = (sev) => sev === 'Crítica'
    ? 'bg-rose-100 text-rose-800 border-rose-300'
    : (sev === 'Alta'
      ? 'bg-orange-100 text-orange-800 border-orange-300'
      : (sev === 'Média' ? 'bg-amber-100 text-amber-800 border-amber-300' : 'bg-sky-100 text-sky-800 border-sky-300'));

  let corpoHtml = '';

  if (a.tipo === 'web_tls') {
    const tls = det.tls || {};
    const hdrs = det.headers || {};
    const todosHdrs = det.todos_headers || {};
    const ips = Array.isArray(det.ips_resolvidos) && det.ips_resolvidos.length > 0 ? det.ips_resolvidos.join(', ') : '---';
    const sans = Array.isArray(tls.sans) && tls.sans.length > 0 ? tls.sans.join(', ') : '---';

    const hdrRows = Object.entries(hdrs).map(([k, v]) => {
      const ok = v && v !== 'Ausente';
      return `
        <tr class="border-b border-violet-100 dark:border-violet-900/50">
          <td class="p-2 font-mono font-semibold text-slate-800 dark:text-violet-100">${escapeHtml(k)}</td>
          <td class="p-2"><span class="px-2 py-0.5 rounded-full text-[10px] font-bold ${ok ? 'bg-emerald-100 text-emerald-800' : 'bg-rose-100 text-rose-800'}">${ok ? '✅ Presente' : '❌ Ausente'}</span></td>
          <td class="p-2 font-mono text-[10px] text-slate-600 dark:text-violet-300 break-all">${escapeHtml(String(v || 'Ausente'))}</td>
        </tr>
      `;
    }).join('');

    const todosHdrRows = Object.entries(todosHdrs).map(([k, v]) =>
      `<div class="text-[10px] font-mono break-all"><span class="font-bold text-violet-700 dark:text-violet-300">${escapeHtml(k)}:</span> ${escapeHtml(String(v))}</div>`
    ).join('');

    corpoHtml = `
      <div class="grid sm:grid-cols-2 lg:grid-cols-3 gap-2.5">
        <div class="p-2.5 rounded-xl bg-violet-50/70 dark:bg-[#1C1633] border border-violet-200/70 dark:border-violet-800/70">
          <span class="text-[10px] font-bold text-slate-400 uppercase block">IPs Resolvidos (DNS)</span>
          <span class="font-mono font-bold text-slate-800 dark:text-white">${escapeHtml(ips)}</span>
        </div>
        <div class="p-2.5 rounded-xl bg-violet-50/70 dark:bg-[#1C1633] border border-violet-200/70 dark:border-violet-800/70">
          <span class="text-[10px] font-bold text-slate-400 uppercase block">HTTP Status &amp; Latência</span>
          <span class="font-bold text-slate-800 dark:text-white">${escapeHtml(String(det.http_status || det.http_erro || '---'))}${det.tempo_resposta_ms ? ` (${det.tempo_resposta_ms} ms)` : ''}</span>
        </div>
        <div class="p-2.5 rounded-xl bg-violet-50/70 dark:bg-[#1C1633] border border-violet-200/70 dark:border-violet-800/70">
          <span class="text-[10px] font-bold text-slate-400 uppercase block">Servidor / Tecnologia</span>
          <span class="font-bold text-slate-800 dark:text-white">${escapeHtml([det.servidor, det.x_powered_by].filter(Boolean).join(' • ') || 'Oculto (boa prática)')}</span>
        </div>
      </div>

      <div class="p-3.5 rounded-xl bg-violet-50/50 dark:bg-[#1C1633] border border-violet-200/80 dark:border-violet-800/70 space-y-2">
        <div class="font-extrabold text-violet-900 dark:text-violet-200">🔐 Certificado SSL/TLS</div>
        ${tls.erro ? `<p class="text-rose-600 font-bold">Erro TLS: ${escapeHtml(tls.erro)}</p>` : `
          <div class="grid sm:grid-cols-2 gap-2 text-[11px]">
            <div><b>Protocolo / Cifra:</b> ${escapeHtml(tls.versao || '---')} ${tls.cifra ? `(${escapeHtml(tls.cifra)}${tls.bits ? ` ${tls.bits} bits` : ''})` : ''}</div>
            <div><b>Titular (CN):</b> ${escapeHtml(tls.titular_cn || '---')}</div>
            <div><b>Emissor:</b> ${escapeHtml(tls.emissor || '---')}</div>
            <div><b>Validade:</b> ${escapeHtml(tls.valido_ate || '---')} ${tls.dias_para_expirar !== undefined ? `<b>(${tls.dias_para_expirar} dias restantes)</b>` : ''}</div>
            <div class="sm:col-span-2"><b>Domínios cobertos (SANs):</b> <span class="font-mono text-[10px]">${escapeHtml(sans)}</span></div>
          </div>
        `}
      </div>

      <div class="space-y-2">
        <div class="font-extrabold text-slate-900 dark:text-white">🛡️ Headers de Segurança OWASP</div>
        <div class="overflow-x-auto rounded-xl border border-violet-200 dark:border-violet-800">
          <table class="w-full text-left text-[11px] border-collapse">
            <thead class="bg-violet-50 dark:bg-[#1C1633] text-violet-900 dark:text-violet-200 font-bold">
              <tr><th class="p-2">Header</th><th class="p-2">Status</th><th class="p-2">Valor Detectado</th></tr>
            </thead>
            <tbody>${hdrRows || '<tr><td colspan="3" class="p-2 text-slate-400">Nenhum header coletado.</td></tr>'}</tbody>
          </table>
        </div>
      </div>

      ${todosHdrRows ? `
        <details class="p-3 rounded-xl bg-slate-50 dark:bg-[#0B0816] border border-violet-200/70 dark:border-violet-800/70">
          <summary class="cursor-pointer font-bold text-violet-800 dark:text-violet-300">📑 Ver todos os cabeçalhos HTTP recebidos (${Object.keys(todosHdrs).length})</summary>
          <div class="mt-2 space-y-1 max-h-48 overflow-y-auto custom-scrollbar">${todosHdrRows}</div>
        </details>
      ` : ''}
    `;
  } else {
    const hosts = Array.isArray(det.hosts) ? det.hosts : [];
    const hostsHtml = hosts.length === 0
      ? '<p class="text-slate-500 dark:text-violet-300/80">Nenhum host ativo com portas abertas encontrado na faixa varrida (se o servidor estiver na nuvem, use o Coletor LAN Local abaixo para redes privadas).</p>'
      : hosts.map(h => {
        const portas = Array.isArray(h.portas) ? h.portas : [];
        const pRows = portas.map(p => {
          const pacoteBlock = (p.pacote_texto || p.pacote_hex || p.tls_resumo) ? `
            <tr class="bg-slate-50/90 dark:bg-[#0B0816] border-b border-violet-100 dark:border-violet-900/50">
              <td colspan="5" class="p-2.5 space-y-1.5">
                <div class="flex flex-wrap gap-2 text-[10px] font-bold text-violet-700 dark:text-violet-300">
                  <span>📡 Protocolo: ${escapeHtml(p.protocolo_detectado || p.servico || '-')}</span>
                  <span>📦 Bytes: ${escapeHtml(String(p.bytes_recebidos ?? 0))}</span>
                  ${p.tls_resumo ? `<span>🔐 TLS: ${escapeHtml(p.tls_resumo)}</span>` : ''}
                </div>
                ${p.pacote_texto ? `<pre class="whitespace-pre-wrap break-all font-mono text-[10px] p-2 rounded-lg bg-slate-900 text-emerald-300 max-h-40 overflow-y-auto">${escapeHtml(p.pacote_texto)}</pre>` : ''}
                ${p.pacote_hex ? `<div class="font-mono text-[9.5px] text-slate-500 dark:text-violet-300/70 break-all"><b>HEX:</b> ${escapeHtml(p.pacote_hex)}</div>` : ''}
              </td>
            </tr>
          ` : '';
          return `
            <tr class="border-b border-violet-100 dark:border-violet-900/50">
              <td class="p-2 font-mono font-extrabold text-violet-700 dark:text-violet-300">${escapeHtml(String(p.porta ?? p))}/${escapeHtml(p.transporte || 'TCP')}</td>
              <td class="p-2 font-semibold text-slate-800 dark:text-white">${escapeHtml(p.servico || 'Desconhecido')}</td>
              <td class="p-2"><span class="px-2 py-0.5 rounded-full text-[10px] font-bold border ${corSev(p.severidade || 'Informativa')}">${escapeHtml(p.severidade || 'Info')}</span></td>
              <td class="p-2 font-mono text-[10px] text-slate-600 dark:text-violet-300">${escapeHtml(p.banner || (p.latencia_ms ? `${p.latencia_ms} ms` : '---'))}</td>
              <td class="p-2 text-[10px] text-slate-600 dark:text-violet-200">${escapeHtml(p.recomendacao || '')}</td>
            </tr>
            ${pacoteBlock}
          `;
        }).join('');
        return `
          <div class="p-3.5 rounded-xl bg-violet-50/50 dark:bg-[#1C1633] border border-violet-200 dark:border-violet-800 space-y-2">
            <div class="flex flex-wrap items-center justify-between gap-2">
              <div class="font-extrabold text-slate-900 dark:text-white">
                🖥️ Host: <span class="font-mono text-violet-700 dark:text-violet-300">${escapeHtml(h.ip || 'Host')}</span>
                ${h.hostname ? `<span class="ml-1.5 text-[11px] font-semibold text-slate-500">(${escapeHtml(h.hostname)})</span>` : ''}
              </div>
              <span class="px-2 py-0.5 rounded-full bg-violet-100 dark:bg-violet-900/60 text-violet-800 dark:text-violet-200 text-[10px] font-bold">
                ${portas.length} porta(s) aberta(s)${h.latencia_ms ? ` • ${h.latencia_ms} ms` : ''}
              </span>
            </div>
            <div class="overflow-x-auto rounded-lg border border-violet-200/80 dark:border-violet-800/70 bg-white dark:bg-[#141024]">
              <table class="w-full text-left text-[11px] border-collapse">
                <thead class="bg-violet-50 dark:bg-[#1C1633] text-violet-900 dark:text-violet-200 font-bold">
                  <tr><th class="p-2">Porta</th><th class="p-2">Serviço</th><th class="p-2">Risco</th><th class="p-2">Banner / Latência</th><th class="p-2">Recomendação</th></tr>
                </thead>
                <tbody>${pRows}</tbody>
              </table>
            </div>
          </div>
        `;
      }).join('');

    corpoHtml = `
      <div class="grid grid-cols-3 gap-2.5">
        <div class="p-2.5 rounded-xl bg-violet-50/70 dark:bg-[#1C1633] border border-violet-200/70 dark:border-violet-800/70">
          <span class="text-[10px] font-bold text-slate-400 uppercase block">IPs Verificados</span>
          <span class="text-base font-extrabold text-slate-900 dark:text-white">${escapeHtml(String(det.hosts_verificados ?? a.hosts_ativos ?? 0))}</span>
        </div>
        <div class="p-2.5 rounded-xl bg-violet-50/70 dark:bg-[#1C1633] border border-violet-200/70 dark:border-violet-800/70">
          <span class="text-[10px] font-bold text-slate-400 uppercase block">Hosts Ativos</span>
          <span class="text-base font-extrabold text-violet-700 dark:text-violet-300">${escapeHtml(String(a.hosts_ativos ?? hosts.length))}</span>
        </div>
        <div class="p-2.5 rounded-xl bg-violet-50/70 dark:bg-[#1C1633] border border-violet-200/70 dark:border-violet-800/70">
          <span class="text-[10px] font-bold text-slate-400 uppercase block">Portas Abertas</span>
          <span class="text-base font-extrabold text-rose-600 dark:text-rose-400">${escapeHtml(String(a.portas_expostas ?? 0))}</span>
        </div>
      </div>
      <div class="space-y-3">
        <div class="font-extrabold text-slate-900 dark:text-white">📡 Hosts &amp; Portas Encontradas na Varredura</div>
        ${hostsHtml}
      </div>
    `;
  }

  const achadosHtml = achados.length === 0
    ? '<p class="text-emerald-700 dark:text-emerald-300 font-semibold">✅ Nenhuma vulnerabilidade crítica ou pendência detectada nesta varredura.</p>'
    : achados.map(item => `
      <div class="p-2.5 rounded-xl bg-white dark:bg-[#1C1633] border border-violet-200/80 dark:border-violet-800/70 space-y-1">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <span class="font-bold text-slate-900 dark:text-white">${escapeHtml(item.titulo || 'Achado')}</span>
          <span class="px-2 py-0.5 rounded-full text-[10px] font-bold border ${corSev(item.severidade || 'Média')}">${escapeHtml(item.severidade || 'Média')}</span>
        </div>
        ${item.recomendacao ? `<p class="text-[11px] text-slate-600 dark:text-violet-200">🛠️ <b>Mitigação:</b> ${escapeHtml(item.recomendacao)}</p>` : ''}
      </div>
    `).join('');

  box.innerHTML = `
    <div class="flex flex-wrap items-center justify-between gap-2 border-b border-violet-100 dark:border-violet-900/60 pb-3">
      <div>
        <div class="flex flex-wrap items-center gap-2">
          <h3 class="text-sm font-extrabold text-slate-900 dark:text-white">🔎 Relatório Completo da Varredura: ${escapeHtml(a.alvo)}</h3>
          <span class="px-2.5 py-0.5 rounded-full text-xs font-extrabold ${a.score >= 80 ? 'bg-emerald-100 text-emerald-800' : (a.score >= 50 ? 'bg-amber-100 text-amber-800' : 'bg-rose-100 text-rose-800')}">Score ${a.score}/100</span>
        </div>
        <p class="text-[11px] text-slate-500 dark:text-violet-300/80">Modo: <b>${escapeHtml(a.tipo)}</b> • Executado por ${escapeHtml(a.executado_por || 'Sistema')} • ${formatarDataBR(a.executado_em)}</p>
      </div>
      <div class="flex items-center gap-1.5 flex-wrap">
        <button type="button" onclick="baixarDocumentoAuditoriaCyber('${escapeHtml(a.id)}', 'txt')" class="px-3 py-1.5 rounded-xl bg-sky-600 hover:bg-sky-700 text-white text-[11px] font-bold">📄 Baixar Documento (.txt)</button>
        <button type="button" onclick="baixarDocumentoAuditoriaCyber('${escapeHtml(a.id)}', 'md')" class="px-2.5 py-1.5 rounded-xl bg-violet-600 hover:bg-violet-700 text-white text-[11px] font-bold">📝 .md</button>
        <button type="button" onclick="copiarRelatorioAuditoriaCyber()" class="px-2.5 py-1.5 rounded-xl bg-emerald-600 hover:bg-emerald-700 text-white text-[11px] font-bold">📋 Copiar</button>
        <button type="button" onclick="fecharDetalheAuditoriaCyber()" class="px-2.5 py-1.5 rounded-xl bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-200 text-[11px] font-bold">✕ Fechar</button>
      </div>
    </div>
    ${corpoHtml}
    <div class="space-y-2 pt-2 border-t border-violet-100 dark:border-violet-900/60">
      <div class="font-extrabold text-slate-900 dark:text-white">🐞 Vulnerabilidades &amp; Pontos de Atenção Encontrados (${achados.length})</div>
      <div class="space-y-2">${achadosHtml}</div>
    </div>
  `;
  box.classList.remove('hidden');
  box.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

async function copiarRelatorioAuditoriaCyber() {
  const a = ultimaAuditoriaDetalhadaCyber;
  if (!a) return;
  const texto = JSON.stringify(a, null, 2);
  try {
    await navigator.clipboard.writeText(texto);
    mostrarToast('Relatório completo da varredura copiado!', 'sucesso');
  } catch (e) {
    mostrarToast('Não foi possível copiar automaticamente.', 'erro');
  }
}

function baixarDocumentoAuditoriaCyber(auditoriaId, formato = 'txt') {
  if (!auditoriaId) return;
  const ext = formato === 'md' ? 'md' : (formato === 'json' ? 'json' : 'txt');
  baixarArquivoAutenticado(
    `/cyber/auditorias/${encodeURIComponent(auditoriaId)}/documento?formato=${encodeURIComponent(ext)}`,
    `relatorio_portas_protocolos_${auditoriaId}.${ext}`
  );
}

function baixarColetorCyber(formato) {
  const nome = formato === 'py' ? 'sysfix_coletor_lan.py' : 'sysfix_coletor_lan.ps1';
  baixarArquivoAutenticado(`/cyber/coletor?formato=${encodeURIComponent(formato)}`, nome);
}

async function importarAuditoriaLanJson(event) {
  const file = event.target.files && event.target.files[0];
  if (!file) return;
  try {
    const texto = await file.text();
    const payload = JSON.parse(texto);
    const res = await apiFetch('/cyber/auditorias/importar', {
      method: 'POST',
      body: JSON.stringify({ payload }),
    });
    if (!res.ok) {
      mostrarToast(await extrairErroResposta(res, 'Erro ao importar JSON do coletor.'), 'erro');
      return;
    }
    const data = await res.json();
    mostrarToast(`Coleta local importada! Score ${data.auditoria.score}/100 (${data.achados_criados} achados).`, 'sucesso');
    event.target.value = '';
    renderizarDetalheAuditoriaCyber(data.auditoria);
    await Promise.all([carregarResumoCyber(), carregarAchadosCyber()]);
  } catch (e) {
    mostrarToast('Arquivo JSON de coleta inválido.', 'erro');
  }
}

// ==============================================================================
// LABORATÓRIO DIDÁTICO ATAQUES × BUSCA DE FALHAS × MITIGAÇÕES
// ==============================================================================

async function carregarLaboratorioCyber() {
  const res = await apiFetch('/cyber/laboratorio');
  if (!res.ok) return;
  cyberCenariosCache = await res.json();
  if (!cyberCenarioSelecionadoId && cyberCenariosCache.length > 0) {
    cyberCenarioSelecionadoId = cyberCenariosCache[0].id;
  }
  renderizarLaboratorioCyber();
}

function selecionarCenarioDidatico(id) {
  cyberCenarioSelecionadoId = id;
  document.getElementById('resultadoSimulacaoDidatica')?.classList.add('hidden');
  renderizarLaboratorioCyber();
}

function renderizarLaboratorioCyber() {
  const lista = document.getElementById('listaCenariosDidaticos');
  const det = document.getElementById('detalheCenarioDidatico');
  if (!lista || !det) return;

  lista.innerHTML = cyberCenariosCache.map(c => {
    const ativo = c.id === cyberCenarioSelecionadoId;
    return `
      <button type="button" onclick="selecionarCenarioDidatico('${escapeHtml(c.id)}')"
        class="w-full p-3 rounded-xl border text-left transition-all ${ativo ? 'bg-violet-600 text-white border-violet-500 shadow-md' : 'bg-violet-50/60 dark:bg-[#1C1633] text-slate-900 dark:text-white border-violet-200 dark:border-violet-800 hover:border-violet-400'}">
        <div class="flex items-center justify-between gap-2">
          <span class="font-extrabold text-xs">${escapeHtml(c.icone || '🎯')} ${escapeHtml(c.nome)}</span>
          <span class="px-1.5 py-0.5 rounded text-[9px] font-extrabold ${ativo ? 'bg-white/20 text-white' : 'bg-rose-100 text-rose-700'}">${escapeHtml(c.risco)}</span>
        </div>
        <div class="text-[10px] mt-1 ${ativo ? 'text-violet-100' : 'text-slate-500 dark:text-violet-300/80'}">${escapeHtml(c.categoria)}</div>
      </button>
    `;
  }).join('');

  const c = cyberCenariosCache.find(x => x.id === cyberCenarioSelecionadoId);
  if (!c) return;
  const podeGerir = temPermissao('gerenciar_auditoria_cyber');

  det.innerHTML = `
    <div class="flex flex-wrap items-center justify-between gap-2 border-b border-violet-100 dark:border-violet-900/60 pb-3">
      <div>
        <span class="px-2 py-0.5 rounded bg-violet-100 dark:bg-violet-900/60 text-violet-800 dark:text-violet-200 text-[10px] font-bold">${escapeHtml(c.categoria)}</span>
        <h3 class="text-base font-extrabold text-slate-900 dark:text-white mt-1">${escapeHtml(c.icone || '')} ${escapeHtml(c.nome)}</h3>
      </div>
      <div class="flex flex-wrap gap-2">
        <button type="button" onclick="executarSimulacaoDidatica(false)" class="px-3.5 py-2 rounded-xl bg-violet-600 hover:bg-violet-700 text-white font-bold shadow-sm">
          🧪 Testar Minha Rede / Empresa
        </button>
        ${podeGerir ? `
          <button type="button" onclick="executarSimulacaoDidatica(true)" class="px-3.5 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-700 text-white font-bold shadow-sm">
            🛡️ Testar e Gerar Plano de Mitigação
          </button>
        ` : ''}
      </div>
    </div>

    <div class="grid md:grid-cols-2 gap-3">
      <div class="p-3.5 rounded-xl bg-rose-50/70 dark:bg-rose-950/20 border border-rose-200 dark:border-rose-900/60 space-y-1.5">
        <div class="font-extrabold text-rose-700 dark:text-rose-300">🔴 1. Como o Ataque Funciona (Anatomia)</div>
        <pre class="whitespace-pre-wrap font-sans text-[11px] leading-relaxed text-slate-700 dark:text-rose-100/90">${escapeHtml(c.anatomia_ataque)}</pre>
      </div>
      <div class="p-3.5 rounded-xl bg-amber-50/70 dark:bg-amber-950/20 border border-amber-200 dark:border-amber-900/60 space-y-1.5">
        <div class="font-extrabold text-amber-800 dark:text-amber-300">🔎 2. Como Buscar essa Falha na Rede</div>
        <pre class="whitespace-pre-wrap font-sans text-[11px] leading-relaxed text-slate-700 dark:text-amber-100/90">${escapeHtml(c.como_buscar_falha)}</pre>
      </div>
    </div>

    <div class="p-4 rounded-xl bg-emerald-50/70 dark:bg-emerald-950/20 border border-emerald-200 dark:border-emerald-900/60 space-y-2">
      <div class="font-extrabold text-emerald-800 dark:text-emerald-300">🛡️ 3. Passo a Passo de Mitigação &amp; Blindagem (Blue Team)</div>
      <ul class="list-disc pl-5 space-y-1 text-[11px] text-slate-700 dark:text-emerald-100/90">
        ${(c.passo_a_passo_mitigacao || []).map(p => `<li>${escapeHtml(p)}</li>`).join('')}
      </ul>
      <div class="pt-1">
        <span class="text-[10px] font-bold uppercase text-slate-500 dark:text-violet-300">Comando / Verificação Defensiva:</span>
        <code class="block mt-1 px-3 py-2 rounded-lg bg-slate-900 text-emerald-300 font-mono text-[11px] overflow-x-auto">${escapeHtml(c.comando_diagnostico || '')}</code>
      </div>
    </div>
  `;
}

async function executarSimulacaoDidatica(registrarPlano = false) {
  if (!cyberCenarioSelecionadoId) return;
  renderizarCyberBotsDock(true);
  try {
    const res = await apiFetch('/cyber/laboratorio/simular', {
      method: 'POST',
      body: JSON.stringify({ cenario_id: cyberCenarioSelecionadoId, registrar_plano: registrarPlano }),
    });
    if (!res.ok) {
      mostrarToast(await extrairErroResposta(res, 'Erro ao simular diagnóstico.'), 'erro');
      return;
    }
    const d = await res.json();
    const box = document.getElementById('resultadoSimulacaoDidatica');
    if (box) {
      box.classList.remove('hidden');
      box.innerHTML = `
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div class="font-extrabold text-sm text-slate-900 dark:text-white">📊 Resultado do Diagnóstico Interno</div>
          <span class="px-3 py-1 rounded-full text-xs font-extrabold ${d.nivel_defesa_percent >= 80 ? 'bg-emerald-100 text-emerald-800' : 'bg-amber-100 text-amber-800'}">Nível de Proteção: ${d.nivel_defesa_percent}%</span>
        </div>
        <div class="space-y-1.5">
          ${(d.verificacoes || []).map(v => `
            <div class="flex items-center justify-between gap-2 p-2 rounded-lg bg-white dark:bg-[#141024] border border-violet-200/70 dark:border-violet-800">
              <span>${v.ok ? '✅' : '⚠️'} <b>${escapeHtml(v.item)}</b></span>
              <span class="text-[11px] text-slate-500 dark:text-violet-300">${escapeHtml(v.detalhe)}</span>
            </div>
          `).join('')}
        </div>
        ${d.achados_gerados > 0 ? `<div class="text-xs font-bold text-emerald-700 dark:text-emerald-300">✓ ${d.achados_gerados} plano(s) de mitigação enviado(s) para a aba Vulnerabilidades!</div>` : ''}
      `;
    }
    if (d.achados_gerados > 0) {
      await Promise.all([carregarAchadosCyber(), carregarResumoCyber()]);
    }
  } finally {
    renderizarCyberBotsDock(false);
  }
}

// ==============================================================================
// AUTOMAÇÕES DE DEFESA DE REDE & CAÇA POR FALHAS
// ==============================================================================

function ajustarPlaceholderAlvoAutomacao() {
  const tipo = document.getElementById('aut_cyb_tipo')?.value || '';
  const inp = document.getElementById('aut_cyb_alvo');
  if (!inp) return;
  if (tipo === 'monitor_ssl_headers') {
    inp.value = '';
    inp.placeholder = 'Ex: suaempresa.com.br';
  } else if (tipo === 'varredura_portas_criticas') {
    inp.value = '192.168.1.0/24';
    inp.placeholder = 'Ex: 192.168.1.0/24';
  } else {
    inp.value = 'Ambiente Interno';
    inp.placeholder = 'Ambiente Interno';
  }
}

async function carregarAutomacoesCyber() {
  const res = await apiFetch('/cyber/automacoes');
  if (!res.ok) return;
  cyberAutomacoesCache = await res.json();
  const box = document.getElementById('listaAutomacoesCyber');
  if (!box) return;

  if (!cyberAutomacoesCache.length) {
    box.innerHTML = '<p class="text-slate-400">Nenhuma automação defensiva criada ainda. Cadastre ao lado para buscar falhas continuamente.</p>';
    return;
  }

  const podeExec = temPermissao('executar_auditoria_rede') || temPermissao('gerenciar_auditoria_cyber');
  const podeExcluir = temPermissao('gerenciar_auditoria_cyber');

  box.innerHTML = cyberAutomacoesCache.map(t => {
    const stCls = t.ultimo_status === 'Protegido'
      ? 'bg-emerald-100 text-emerald-800'
      : (t.ultimo_status === 'Alerta' ? 'bg-amber-100 text-amber-800' : 'bg-violet-100 text-violet-800');
    return `
      <div class="p-3.5 rounded-xl bg-violet-50/70 dark:bg-[#1C1633] border border-violet-200/80 dark:border-violet-800/70 space-y-2">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div>
            <div class="font-extrabold text-xs text-slate-900 dark:text-white">${escapeHtml(t.nome)}</div>
            <div class="text-[11px] text-violet-700 dark:text-violet-300 font-semibold">${escapeHtml(t.tipo_label || t.tipo)} • Alvo: ${escapeHtml(t.alvo)} • ${escapeHtml(t.frequencia)}</div>
          </div>
          <span class="px-2.5 py-0.5 rounded-full text-[10px] font-extrabold ${stCls}">${escapeHtml(t.ultimo_status || 'Ativa')} (${t.ultimo_score ?? 100}/100)</span>
        </div>
        ${t.ultimo_resumo ? `<div class="text-[11px] text-slate-600 dark:text-violet-200 bg-white/80 dark:bg-[#141024] p-2 rounded-lg border border-violet-100 dark:border-violet-900/50">${escapeHtml(t.ultimo_resumo)}</div>` : ''}
        <div class="flex flex-wrap items-center justify-between gap-2 pt-0.5">
          <span class="text-[10px] text-slate-400">${t.ultima_execucao ? `Última verificação: ${formatarDataBR(t.ultima_execucao)}` : 'Nunca executada'}</span>
          <div class="flex gap-1.5">
            ${podeExec ? `<button type="button" onclick="executarAutomacaoCyber('${escapeHtml(t.id)}')" class="px-3 py-1 rounded-lg bg-violet-600 hover:bg-violet-700 text-white text-[11px] font-bold">▶️ Rodar Busca Agora</button>` : ''}
            ${podeExcluir ? `<button type="button" onclick="excluirAutomacaoCyber('${escapeHtml(t.id)}')" class="px-2 py-1 rounded-lg bg-rose-50 text-rose-600 text-[11px] font-bold">✕</button>` : ''}
          </div>
        </div>
      </div>
    `;
  }).join('');
}

async function criarAutomacaoCyber(event) {
  event.preventDefault();
  const payload = {
    nome: document.getElementById('aut_cyb_nome').value.trim(),
    tipo: document.getElementById('aut_cyb_tipo').value,
    alvo: document.getElementById('aut_cyb_alvo').value.trim() || 'Ambiente Interno',
    frequencia: document.getElementById('aut_cyb_freq').value,
    auto_registrar_achados: document.getElementById('aut_cyb_auto_achados').checked,
  };
  const res = await apiFetch('/cyber/automacoes', { method: 'POST', body: JSON.stringify(payload) });
  if (!res.ok) {
    mostrarToast(await extrairErroResposta(res, 'Erro ao criar automação.'), 'erro');
    return;
  }
  event.target.reset();
  ajustarPlaceholderAlvoAutomacao();
  mostrarToast('Automação de segurança criada!', 'sucesso');
  await Promise.all([carregarAutomacoesCyber(), carregarResumoCyber()]);
}

async function executarAutomacaoCyber(id) {
  renderizarCyberBotsDock(true);
  try {
    const res = await apiFetch(`/cyber/automacoes/${encodeURIComponent(id)}/executar`, { method: 'POST' });
    if (!res.ok) {
      mostrarToast(await extrairErroResposta(res, 'Execução bloqueada ou inválida.'), 'erro');
      return;
    }
    const d = await res.json();
    mostrarToast(d.resumo || 'Busca automática de falhas concluída!', 'sucesso');
    await Promise.all([carregarAutomacoesCyber(), carregarAchadosCyber(), carregarResumoCyber()]);
  } finally {
    renderizarCyberBotsDock(false);
  }
}

async function excluirAutomacaoCyber(id) {
  const res = await apiFetch(`/cyber/automacoes/${encodeURIComponent(id)}`, { method: 'DELETE' });
  if (res.ok) await Promise.all([carregarAutomacoesCyber(), carregarResumoCyber()]);
}

function baixarScriptProtecaoCyber(tipo) {
  const mapa = {
    firewall_windows_ps1: 'sysfix_blindagem_windows.ps1',
    hardening_linux_ufw_sh: 'sysfix_hardening_linux.sh',
    tarefa_agendada_monitor_ps1: 'sysfix_monitor_diario.ps1',
  };
  baixarArquivoAutenticado(`/cyber/automacoes/script?tipo=${encodeURIComponent(tipo)}`, mapa[tipo] || 'script_defesa.txt');
}

// ==============================================================================
// FILA DE VULNERABILIDADES & CHECKLIST CIS / NIST / LGPD
// ==============================================================================

async function carregarAchadosCyber() {
  const res = await apiFetch('/cyber/achados');
  if (!res.ok) return;
  cyberAchadosCache = await res.json();
  renderizarAchadosCyber();
}

function renderizarAchadosCyber() {
  const box = document.getElementById('listaAchadosCyber');
  const kpiBox = document.getElementById('patchBotKpis');
  if (!box) return;
  const podeGerir = temPermissao('gerenciar_auditoria_cyber');

  const todos = Array.isArray(cyberAchadosCache) ? cyberAchadosCache : [];
  if (kpiBox) {
    const total = todos.length;
    const crit = todos.filter(a => a.severidade === 'Crítica' && a.status !== 'Resolvido').length;
    const altas = todos.filter(a => a.severidade === 'Alta' && a.status !== 'Resolvido').length;
    const mitig = todos.filter(a => a.status === 'Em Mitigação').length;
    const resolv = todos.filter(a => a.status === 'Resolvido').length;
    kpiBox.innerHTML = `
      <div class="p-2.5 rounded-xl bg-violet-50/70 dark:bg-[#1C1633] border border-violet-200/70 dark:border-violet-800/70">
        <div class="text-[10px] font-bold text-slate-400 uppercase">Total</div>
        <div class="text-base font-extrabold text-slate-900 dark:text-white">${total}</div>
      </div>
      <div class="p-2.5 rounded-xl bg-rose-50/80 dark:bg-rose-950/30 border border-rose-200/80 dark:border-rose-900/60">
        <div class="text-[10px] font-bold text-rose-600 uppercase">Críticas</div>
        <div class="text-base font-extrabold text-rose-700 dark:text-rose-400">${crit}</div>
      </div>
      <div class="p-2.5 rounded-xl bg-orange-50/80 dark:bg-orange-950/30 border border-orange-200/80 dark:border-orange-900/60">
        <div class="text-[10px] font-bold text-orange-600 uppercase">Altas</div>
        <div class="text-base font-extrabold text-orange-700 dark:text-orange-400">${altas}</div>
      </div>
      <div class="p-2.5 rounded-xl bg-amber-50/80 dark:bg-amber-950/30 border border-amber-200/80 dark:border-amber-900/60">
        <div class="text-[10px] font-bold text-amber-700 uppercase">Em Mitigação</div>
        <div class="text-base font-extrabold text-amber-800 dark:text-amber-300">${mitig}</div>
      </div>
      <div class="p-2.5 rounded-xl bg-emerald-50/80 dark:bg-emerald-950/30 border border-emerald-200/80 dark:border-emerald-900/60">
        <div class="text-[10px] font-bold text-emerald-700 uppercase">Resolvidas</div>
        <div class="text-base font-extrabold text-emerald-700 dark:text-emerald-300">${resolv}</div>
      </div>
    `;
  }

  const busca = (document.getElementById('ach_filtro_busca')?.value || '').trim().toLowerCase();
  const fSev = document.getElementById('ach_filtro_sev')?.value || '';
  const fStatus = document.getElementById('ach_filtro_status')?.value || '';

  const filtrados = todos.filter(a => {
    if (fSev && a.severidade !== fSev) return false;
    if (fStatus && a.status !== fStatus) return false;
    if (busca) {
      const hay = [
        a.titulo, a.alvo, a.categoria, a.cve_cwe, a.owasp, a.cis_control,
        a.mitre_attack, a.evidencia, a.descricao_tecnica, a.recomendacao, a.responsavel, a.origem,
      ].join(' ').toLowerCase();
      if (!hay.includes(busca)) return false;
    }
    return true;
  });

  if (!filtrados.length) {
    box.innerHTML = '<p class="text-slate-400">Nenhuma vulnerabilidade encontrada para os filtros atuais.</p>';
    return;
  }

  box.innerHTML = filtrados.map(a => {
    const sevCls = a.severidade === 'Crítica'
      ? 'bg-rose-600 text-white'
      : (a.severidade === 'Alta'
        ? 'bg-orange-500 text-white'
        : (a.severidade === 'Média'
          ? 'bg-amber-400 text-slate-900'
          : (a.severidade === 'Baixa' ? 'bg-sky-100 text-sky-800' : 'bg-violet-100 text-violet-800')));
    const stCls = a.status === 'Resolvido'
      ? 'bg-emerald-100 text-emerald-800 border-emerald-200'
      : (a.status === 'Em Mitigação'
        ? 'bg-amber-100 text-amber-800 border-amber-200'
        : (a.status === 'Risco Aceito'
          ? 'bg-slate-200 text-slate-700 border-slate-300'
          : 'bg-rose-100 text-rose-800 border-rose-200'));
    const passos = Array.isArray(a.passos_remediacao) ? a.passos_remediacao : [];
    const safeId = escapeHtml(a.id);

    return `
      <div class="p-4 rounded-2xl bg-violet-50/70 dark:bg-[#1C1633] border border-violet-200/90 dark:border-violet-800/80 space-y-2.5">
        <div class="flex flex-wrap justify-between items-start gap-2">
          <div class="space-y-1 flex-1 min-w-[220px]">
            <div class="flex flex-wrap items-center gap-1.5">
              <span class="px-2 py-0.5 rounded text-[10px] font-extrabold ${sevCls}">${escapeHtml(a.severidade)}</span>
              ${a.cvss_score !== undefined && a.cvss_score !== null ? `<span class="px-2 py-0.5 rounded bg-slate-900 text-amber-300 text-[10px] font-mono font-bold">CVSS ${escapeHtml(String(a.cvss_score))}</span>` : ''}
              ${a.cve_cwe ? `<span class="px-2 py-0.5 rounded bg-violet-100 dark:bg-violet-900/70 text-violet-800 dark:text-violet-200 text-[10px] font-bold">${escapeHtml(a.cve_cwe)}</span>` : ''}
              <span class="px-2 py-0.5 rounded bg-white dark:bg-[#141024] border border-violet-200 dark:border-violet-700 text-[10px] font-semibold text-slate-600 dark:text-violet-200">⏱️ SLA: ${escapeHtml(a.sla_recomendado || '30 dias')}</span>
            </div>
            <div class="font-extrabold text-xs sm:text-sm text-slate-900 dark:text-white">${escapeHtml(a.titulo)}</div>
            <div class="text-[11px] text-slate-500 dark:text-violet-300/80 flex flex-wrap gap-x-3 gap-y-0.5">
              <span>📂 <b>${escapeHtml(a.categoria || 'Geral')}</b></span>
              <span>🎯 Alvo: <b>${escapeHtml(a.alvo || '-')}</b></span>
              <span>🛰️ Origem: <b>${escapeHtml(a.origem || 'Sistema')}</b></span>
              ${a.responsavel ? `<span>👤 Resp.: <b>${escapeHtml(a.responsavel)}</b></span>` : ''}
              <span>🕒 ${formatarDataBR(a.criado_em)}</span>
            </div>
          </div>
          <span class="px-2.5 py-1 rounded-full text-[10px] font-extrabold border ${stCls}">${escapeHtml(a.status)}</span>
        </div>

        <div class="grid sm:grid-cols-3 gap-1.5 text-[10px]">
          <div class="px-2.5 py-1.5 rounded-lg bg-white/90 dark:bg-[#141024] border border-violet-200/60 dark:border-violet-800/70">
            <span class="font-bold text-violet-700 dark:text-violet-300 block">OWASP / Norma</span>
            <span class="text-slate-700 dark:text-violet-100">${escapeHtml(a.owasp || '-')}</span>
          </div>
          <div class="px-2.5 py-1.5 rounded-lg bg-white/90 dark:bg-[#141024] border border-violet-200/60 dark:border-violet-800/70">
            <span class="font-bold text-emerald-700 dark:text-emerald-300 block">CIS Control</span>
            <span class="text-slate-700 dark:text-violet-100">${escapeHtml(a.cis_control || '-')}</span>
          </div>
          <div class="px-2.5 py-1.5 rounded-lg bg-white/90 dark:bg-[#141024] border border-violet-200/60 dark:border-violet-800/70">
            <span class="font-bold text-rose-700 dark:text-rose-300 block">MITRE ATT&amp;CK</span>
            <span class="text-slate-700 dark:text-violet-100">${escapeHtml(a.mitre_attack || '-')}</span>
          </div>
        </div>

        ${a.evidencia ? `
          <div class="p-2.5 rounded-xl bg-slate-900 text-emerald-300 font-mono text-[10px] break-all">
            <span class="text-slate-400 font-sans font-bold uppercase mr-1">Evidência:</span>${escapeHtml(a.evidencia)}
          </div>
        ` : ''}

        <details class="rounded-xl bg-white/90 dark:bg-[#141024] border border-violet-200/80 dark:border-violet-800/80 p-3 space-y-2.5">
          <summary class="cursor-pointer font-extrabold text-violet-700 dark:text-violet-300 text-[11px] select-none">
            🔎 Ver Dossiê Técnico Completo, Impacto, Passo a Passo &amp; Comando de Validação
          </summary>

          <div class="pt-2 space-y-2.5 border-t border-violet-100 dark:border-violet-900/60 text-[11px]">
            <div class="grid md:grid-cols-2 gap-2.5">
              <div class="p-2.5 rounded-lg bg-violet-50/70 dark:bg-[#1C1633] border border-violet-200/60 dark:border-violet-800/60">
                <div class="font-extrabold text-violet-900 dark:text-violet-200 mb-1">📘 Descrição Técnica &amp; Causa Raiz</div>
                <p class="text-slate-700 dark:text-violet-100 leading-relaxed">${escapeHtml(a.descricao_tecnica || '-')}</p>
              </div>
              <div class="p-2.5 rounded-lg bg-rose-50/70 dark:bg-rose-950/20 border border-rose-200/70 dark:border-rose-900/50">
                <div class="font-extrabold text-rose-800 dark:text-rose-300 mb-1">⚠️ Impacto no Negócio &amp; Segurança</div>
                <p class="text-slate-700 dark:text-rose-100/90 leading-relaxed">${escapeHtml(a.impacto || '-')}</p>
              </div>
            </div>

            ${a.recomendacao ? `
              <div class="p-2.5 rounded-lg bg-amber-50/70 dark:bg-amber-950/20 border border-amber-200/70 dark:border-amber-900/50">
                <span class="font-extrabold text-amber-800 dark:text-amber-300">💡 Recomendação Direta:</span>
                <span class="text-slate-700 dark:text-amber-100/90"> ${escapeHtml(a.recomendacao)}</span>
              </div>
            ` : ''}

            ${passos.length ? `
              <div class="p-2.5 rounded-lg bg-emerald-50/70 dark:bg-emerald-950/20 border border-emerald-200/70 dark:border-emerald-900/50 space-y-1">
                <div class="font-extrabold text-emerald-800 dark:text-emerald-300">🛠️ Plano de Remediação Passo a Passo (Patch-Bot)</div>
                <ol class="list-decimal pl-5 space-y-1 text-slate-700 dark:text-emerald-100/90">
                  ${passos.map(p => `<li>${escapeHtml(p)}</li>`).join('')}
                </ol>
              </div>
            ` : ''}

            ${a.comando_validacao ? `
              <div class="space-y-1">
                <div class="flex items-center justify-between gap-2">
                  <span class="text-[10px] font-bold uppercase text-slate-500 dark:text-violet-300">💻 Comando / Verificação Defensiva</span>
                  <button type="button" onclick="copiarTextoPatchBot('${safeId}', 'comando')" class="px-2 py-0.5 rounded bg-violet-100 dark:bg-violet-900/60 text-violet-800 dark:text-violet-200 text-[10px] font-bold">📋 Copiar Comando</button>
                </div>
                <code class="block px-3 py-2 rounded-lg bg-slate-900 text-emerald-300 font-mono text-[10px] overflow-x-auto">${escapeHtml(a.comando_validacao)}</code>
              </div>
            ` : ''}

            <div class="text-[10px] text-slate-500 dark:text-violet-300/80 flex flex-wrap gap-x-4 gap-y-1">
              <span><b>Vetor CVSS:</b> <span class="font-mono">${escapeHtml(a.cvss_vetor || '-')}</span></span>
              <span><b>Criado por:</b> ${escapeHtml(a.criado_por || 'Sistema')}</span>
              <span><b>Atualizado:</b> ${formatarDataBR(a.atualizado_em)}</span>
              ${a.auditoria_id ? `<span><b>ID Varredura:</b> <span class="font-mono">${escapeHtml(a.auditoria_id)}</span></span>` : ''}
            </div>

            ${podeGerir ? `
              <div class="pt-2 border-t border-violet-100 dark:border-violet-900/60 space-y-2">
                <div class="font-extrabold text-slate-800 dark:text-white text-[11px]">📝 Gestão da Correção (Responsável, SLA &amp; Notas de Patch)</div>
                <div class="grid sm:grid-cols-2 gap-2">
                  <input type="text" id="ach_edit_resp_${safeId}" value="${escapeHtml(a.responsavel || '')}" placeholder="Responsável pela correção" class="px-2.5 py-1.5 rounded-lg border border-violet-200 dark:border-violet-800 bg-white dark:bg-[#1C1633] text-[11px] outline-none">
                  <input type="text" id="ach_edit_prazo_${safeId}" value="${escapeHtml(a.prazo_sla || a.sla_recomendado || '')}" placeholder="Prazo / SLA" class="px-2.5 py-1.5 rounded-lg border border-violet-200 dark:border-violet-800 bg-white dark:bg-[#1C1633] text-[11px] outline-none">
                </div>
                <textarea id="ach_edit_notas_${safeId}" rows="2" placeholder="Registre aqui o que foi feito no patch, número da O.S., evidência pós-correção..." class="w-full px-2.5 py-1.5 rounded-lg border border-violet-200 dark:border-violet-800 bg-white dark:bg-[#1C1633] text-[11px] outline-none resize-none">${escapeHtml(a.notas_remediacao || '')}</textarea>
                <button type="button" onclick="salvarDetalhesAchadoCyber('${safeId}')" class="px-3 py-1.5 rounded-lg bg-violet-600 hover:bg-violet-700 text-white text-[11px] font-bold">💾 Salvar Responsável &amp; Notas</button>
              </div>
            ` : (a.notas_remediacao ? `<div class="p-2 rounded-lg bg-slate-50 dark:bg-[#1C1633] text-[11px]"><b>Notas de Patch:</b> ${escapeHtml(a.notas_remediacao)}</div>` : '')}
          </div>
        </details>

        <div class="flex flex-wrap items-center justify-between gap-2 pt-1">
          <button type="button" onclick="copiarTextoPatchBot('${safeId}', 'dossie')" class="px-2.5 py-1 rounded-lg bg-white dark:bg-[#141024] border border-violet-200 dark:border-violet-700 text-violet-700 dark:text-violet-200 text-[10px] font-bold">📋 Copiar Dossiê</button>
          ${podeGerir ? `
            <div class="flex flex-wrap gap-1.5">
              <button onclick="atualizarStatusAchadoCyber('${safeId}', 'Aberto')" class="px-2 py-1 rounded-lg bg-rose-50 text-rose-700 text-[10px] font-bold border border-rose-200">Aberto</button>
              <button onclick="atualizarStatusAchadoCyber('${safeId}', 'Em Mitigação')" class="px-2 py-1 rounded-lg bg-amber-50 text-amber-800 text-[10px] font-bold border border-amber-200">Em Mitigação</button>
              <button onclick="atualizarStatusAchadoCyber('${safeId}', 'Resolvido')" class="px-2 py-1 rounded-lg bg-emerald-50 text-emerald-700 text-[10px] font-bold border border-emerald-200">✓ Resolvido</button>
              <button onclick="atualizarStatusAchadoCyber('${safeId}', 'Risco Aceito')" class="px-2 py-1 rounded-lg bg-slate-100 text-slate-700 text-[10px] font-bold border border-slate-200">Risco Aceito</button>
              <button onclick="excluirAchadoCyber('${safeId}')" class="px-2 py-1 rounded-lg bg-rose-50 text-rose-600 text-[10px] font-semibold">✕</button>
            </div>
          ` : ''}
        </div>
      </div>
    `;
  }).join('');
}

function formatarDossieAchadoTexto(a) {
  const passos = (Array.isArray(a.passos_remediacao) ? a.passos_remediacao : [])
    .map((p, i) => `  ${i + 1}. ${p}`).join('\n');
  return [
    `=== DOSSIÊ PATCH-BOT (SYSFIX) ===`,
    `Título: ${a.titulo || '-'}`,
    `Severidade: ${a.severidade || '-'} | CVSS: ${a.cvss_score ?? '-'} (${a.cvss_vetor || '-'})`,
    `Status: ${a.status || '-'} | SLA: ${a.sla_recomendado || '-'}`,
    `Alvo: ${a.alvo || '-'} | Categoria: ${a.categoria || '-'} | Origem: ${a.origem || '-'}`,
    `CWE/CVE: ${a.cve_cwe || '-'}`,
    `OWASP: ${a.owasp || '-'}`,
    `CIS Control: ${a.cis_control || '-'}`,
    `MITRE ATT&CK: ${a.mitre_attack || '-'}`,
    `Responsável: ${a.responsavel || 'Não atribuído'}`,
    `Evidência: ${a.evidencia || '-'}`,
    `Descrição Técnica: ${a.descricao_tecnica || '-'}`,
    `Impacto: ${a.impacto || '-'}`,
    `Recomendação: ${a.recomendacao || '-'}`,
    `Passo a Passo de Remediação:\n${passos || '  -'}`,
    `Comando de Validação: ${a.comando_validacao || '-'}`,
    a.notas_remediacao ? `Notas de Patch: ${a.notas_remediacao}` : '',
  ].filter(Boolean).join('\n');
}

async function copiarTextoPatchBot(id, modo) {
  const a = (cyberAchadosCache || []).find(x => x.id === id);
  if (!a) return;
  const txt = modo === 'comando' ? (a.comando_validacao || '') : formatarDossieAchadoTexto(a);
  try {
    await navigator.clipboard.writeText(txt);
    mostrarToast(modo === 'comando' ? 'Comando copiado!' : 'Dossiê completo do achado copiado!', 'sucesso');
  } catch (e) {
    mostrarToast('Não foi possível copiar automaticamente.', 'erro');
  }
}

async function copiarTodosAchadosPatchBot() {
  const todos = Array.isArray(cyberAchadosCache) ? cyberAchadosCache : [];
  if (!todos.length) {
    mostrarToast('Nenhum achado na fila para copiar.', 'info');
    return;
  }
  const txt = todos.map(formatarDossieAchadoTexto).join('\n\n----------------------------------------\n\n');
  try {
    await navigator.clipboard.writeText(txt);
    mostrarToast(`Relatório completo com ${todos.length} achado(s) copiado!`, 'sucesso');
  } catch (e) {
    mostrarToast('Não foi possível copiar automaticamente.', 'erro');
  }
}

async function salvarDetalhesAchadoCyber(id) {
  const responsavel = document.getElementById(`ach_edit_resp_${id}`)?.value?.trim() || '';
  const prazo_sla = document.getElementById(`ach_edit_prazo_${id}`)?.value?.trim() || '';
  const notas_remediacao = document.getElementById(`ach_edit_notas_${id}`)?.value?.trim() || '';
  const res = await apiFetch(`/cyber/achados/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify({ responsavel, prazo_sla, notas_remediacao }),
  });
  if (!res.ok) {
    mostrarToast(await extrairErroResposta(res, 'Erro ao salvar detalhes do achado.'), 'erro');
    return;
  }
  mostrarToast('Detalhes e notas de correção salvos!', 'sucesso');
  await carregarAchadosCyber();
}

async function criarAchadoCyber(event) {
  event.preventDefault();
  const payload = {
    titulo: document.getElementById('ach_titulo').value.trim(),
    severidade: document.getElementById('ach_sev').value,
    categoria: document.getElementById('ach_cat').value.trim(),
    alvo: document.getElementById('ach_alvo').value.trim(),
    cve_cwe: document.getElementById('ach_cve_cwe')?.value.trim() || '',
    responsavel: document.getElementById('ach_resp')?.value.trim() || '',
    prazo_sla: document.getElementById('ach_prazo')?.value.trim() || '',
    evidencia: document.getElementById('ach_evidencia')?.value.trim() || '',
    impacto: document.getElementById('ach_impacto')?.value.trim() || '',
    recomendacao: document.getElementById('ach_rec').value.trim(),
    origem: 'Registro Manual',
  };
  const res = await apiFetch('/cyber/achados', { method: 'POST', body: JSON.stringify(payload) });
  if (!res.ok) {
    mostrarToast(await extrairErroResposta(res, 'Erro ao registrar achado.'), 'erro');
    return;
  }
  event.target.reset();
  mostrarToast('Achado registrado e enriquecido pelo Patch-Bot!', 'sucesso');
  await Promise.all([carregarAchadosCyber(), carregarResumoCyber()]);
}

async function atualizarStatusAchadoCyber(id, status) {
  const res = await apiFetch(`/cyber/achados/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify({ status }),
  });
  if (res.ok) await Promise.all([carregarAchadosCyber(), carregarResumoCyber()]);
}

async function excluirAchadoCyber(id) {
  const res = await apiFetch(`/cyber/achados/${encodeURIComponent(id)}`, { method: 'DELETE' });
  if (res.ok) await Promise.all([carregarAchadosCyber(), carregarResumoCyber()]);
}

async function carregarChecklistCyber() {
  const res = await apiFetch('/cyber/checklist');
  if (!res.ok) return;
  cyberChecklistCache = await res.json();
  const box = document.getElementById('listaChecklistCyber');
  if (!box) return;
  const podeGerir = temPermissao('gerenciar_auditoria_cyber');

  box.innerHTML = cyberChecklistCache.map((c, idx) => `
    <div class="p-3 rounded-xl bg-violet-50/70 dark:bg-[#1C1633] border border-violet-200/80 dark:border-violet-800/70 flex flex-wrap justify-between items-center gap-2 text-xs">
      <div class="flex-1 min-w-[240px]">
        <span class="px-2 py-0.5 rounded bg-violet-100 dark:bg-violet-900/60 text-violet-700 dark:text-violet-200 text-[10px] font-bold">${escapeHtml(c.framework)}</span>
        <div class="font-semibold text-slate-900 dark:text-white mt-1">${escapeHtml(c.controle)}</div>
      </div>
      <select ${podeGerir ? '' : 'disabled'} onchange="atualizarItemChecklistCyber(${idx}, this.value)" class="px-2.5 py-1.5 rounded-lg border border-violet-200 dark:border-violet-800 bg-white dark:bg-[#141024] text-xs font-semibold outline-none">
        ${['Pendente', 'Parcial', 'Conforme', 'Não Aplicável'].map(st => `<option value="${st}" ${c.status === st ? 'selected' : ''}>${st}</option>`).join('')}
      </select>
    </div>
  `).join('');
}

async function atualizarItemChecklistCyber(idx, status) {
  if (!cyberChecklistCache[idx]) return;
  cyberChecklistCache[idx].status = status;
  const res = await apiFetch('/cyber/checklist', {
    method: 'PUT',
    body: JSON.stringify({ itens: cyberChecklistCache }),
  });
  if (res.ok) {
    mostrarToast('Controle de conformidade atualizado!', 'sucesso');
    await carregarResumoCyber();
  }
}

// ==============================================================================
// WIKI-CORE: NOTÍCIAS REAIS DO MUNDO CIBERNÉTICO SEPARADAS DOS INTERNOS
// ==============================================================================

function alternarWikiSecao(secao) {
  wikiSecaoAtual = secao === 'internos' ? 'internos' : 'noticias';
  document.getElementById('blocoWikiNoticiasReais')?.classList.toggle('hidden', wikiSecaoAtual !== 'noticias');
  document.getElementById('blocoWikiInternos')?.classList.toggle('hidden', wikiSecaoAtual !== 'internos');

  const act = 'px-3.5 py-2 rounded-xl font-extrabold bg-violet-600 text-white shadow-sm';
  const inact = 'px-3.5 py-2 rounded-xl font-bold bg-violet-50 dark:bg-[#1C1633] text-violet-800 dark:text-violet-200 border border-violet-200 dark:border-violet-700';
  if (typeof aplicarEstadoAbaBotao === 'function') {
    aplicarEstadoAbaBotao(document.getElementById('btnWikiSecao_noticias'), wikiSecaoAtual === 'noticias', act, inact);
    aplicarEstadoAbaBotao(document.getElementById('btnWikiSecao_internos'), wikiSecaoAtual === 'internos', act, inact);
  }
}

async function carregarConhecimentoCyber(forcarNoticias = false) {
  const busca = document.getElementById('kb_busca')?.value || '';
  const url = `/cyber/wiki?busca=${encodeURIComponent(busca)}${forcarNoticias ? '&atualizar_noticias=true' : ''}`;
  const res = await apiFetch(url);
  if (!res.ok) return;
  cyberWikiCache = await res.json();
  renderizarWikiSeparado();
}

function renderizarWikiSeparado() {
  const podeGerir = temPermissao('gerenciar_auditoria_cyber');
  const feed = cyberWikiCache.noticias_mundo_cyber || {};
  const noticias = Array.isArray(feed.itens) ? feed.itens : [];
  const internos = Array.isArray(cyberWikiCache.publicados_internos) ? cyberWikiCache.publicados_internos : [];
  const referencia = Array.isArray(cyberWikiCache.guias_referencia) ? cyberWikiCache.guias_referencia : [];

  const badge = document.getElementById('badgeStatusNoticiasCyber');
  if (badge) {
    badge.textContent = feed.ao_vivo
      ? `🟢 Feed Ao Vivo (${(feed.fontes_ativas || []).join(', ')})`
      : '🟡 Alertas Oficiais em Cache Local';
    badge.className = feed.ao_vivo
      ? 'px-2.5 py-1 rounded-full text-[10px] font-bold bg-emerald-50 text-emerald-700 border border-emerald-200'
      : 'px-2.5 py-1 rounded-full text-[10px] font-bold bg-amber-50 text-amber-800 border border-amber-200';
  }

  const boxNews = document.getElementById('listaNoticiasMundoCyber');
  if (boxNews) {
    boxNews.innerHTML = noticias.length === 0
      ? '<p class="text-slate-400 col-span-2">Nenhuma notícia encontrada para este filtro.</p>'
      : noticias.map((n, i) => `
        <article class="p-4 rounded-xl bg-violet-50/60 dark:bg-[#1C1633] border border-violet-200/80 dark:border-violet-800/70 flex flex-col justify-between gap-2.5">
          <div class="space-y-1.5">
            <div class="flex flex-wrap items-center justify-between gap-1.5">
              <span class="px-2 py-0.5 rounded bg-indigo-600 text-white text-[10px] font-extrabold">${escapeHtml(n.fonte || 'Cyber Feed')}</span>
              <span class="text-[10px] font-semibold text-slate-500 dark:text-violet-300">${escapeHtml(n.categoria || '')} ${n.data ? '• ' + escapeHtml(n.data) : ''}</span>
            </div>
            <h4 class="text-xs font-extrabold text-slate-900 dark:text-white leading-snug">${escapeHtml(n.titulo)}</h4>
            <p class="text-[11px] text-slate-600 dark:text-violet-200/90 leading-relaxed">${escapeHtml(n.resumo || '')}</p>
          </div>
          <div class="flex flex-wrap items-center justify-between gap-2 pt-1 border-t border-violet-200/60 dark:border-violet-800/60">
            ${n.url ? `<a href="${escapeHtml(n.url)}" target="_blank" rel="noopener noreferrer" class="text-[11px] font-extrabold text-violet-700 dark:text-violet-300 hover:underline">Ler notícia oficial ↗</a>` : '<span></span>'}
            ${podeGerir ? `<button type="button" onclick="transformarNoticiaEmAchado(${i})" class="px-2.5 py-1 rounded-lg bg-white dark:bg-[#141024] border border-violet-200 dark:border-violet-700 text-[10px] font-bold text-violet-800 dark:text-violet-200 hover:bg-violet-100">➕ Virar Tarefa Interna</button>` : ''}
          </div>
        </article>
      `).join('');
  }

  const cntInt = document.getElementById('contadorInternosWiki');
  if (cntInt) cntInt.textContent = `${internos.length} publicado${internos.length === 1 ? '' : 's'}`;

  const boxInt = document.getElementById('listaPlaybooksInternosCyber');
  if (boxInt) {
    boxInt.innerHTML = internos.length === 0
      ? '<p class="text-slate-400">Nenhum playbook interno publicado pela sua equipe ainda. Use o formulário ao lado para criar o primeiro!</p>'
      : internos.map(art => `
        <article class="p-4 rounded-xl bg-emerald-50/50 dark:bg-[#1C1633] border border-emerald-200/80 dark:border-violet-800 space-y-2">
          <div class="flex flex-wrap justify-between items-start gap-2">
            <div>
              <span class="px-2 py-0.5 rounded bg-emerald-600 text-white text-[10px] font-bold">🏢 Interno • ${escapeHtml(art.categoria)}</span>
              <span class="text-[10px] text-slate-500 dark:text-violet-300 ml-1">Por ${escapeHtml(art.criado_por || 'Equipe')} (${escapeHtml(art.atualizado_em || '')})</span>
              <h4 class="text-sm font-extrabold text-slate-900 dark:text-white mt-1">${escapeHtml(art.titulo)}</h4>
              <p class="text-[11px] text-slate-500 dark:text-violet-300/80">${escapeHtml(art.resumo || '')}</p>
            </div>
            ${podeGerir ? `<button onclick="excluirArtigoKbCyber('${escapeHtml(art.id)}')" class="px-2 py-1 rounded bg-rose-50 text-rose-600 text-[10px] font-bold">✕</button>` : ''}
          </div>
          <pre class="whitespace-pre-wrap font-sans text-xs bg-white dark:bg-[#141024] p-3 rounded-lg border border-violet-100 dark:border-violet-900/60 text-slate-700 dark:text-violet-100">${escapeHtml(art.conteudo)}</pre>
        </article>
      `).join('');
  }

  const boxRef = document.getElementById('listaConhecimentoCyber');
  if (boxRef) {
    boxRef.innerHTML = referencia.map(art => `
      <article class="p-4 rounded-xl bg-violet-50/70 dark:bg-[#1C1633] border border-violet-200/80 dark:border-violet-800/70 space-y-2">
        <div>
          <span class="px-2 py-0.5 rounded bg-violet-600 text-white text-[10px] font-bold">${escapeHtml(art.categoria)}</span>
          <span class="px-2 py-0.5 rounded bg-white dark:bg-[#141024] border border-violet-200 dark:border-violet-700 text-[10px] font-semibold ml-1">${escapeHtml(art.nivel)}</span>
          <h4 class="text-sm font-extrabold text-slate-900 dark:text-white mt-1.5">${escapeHtml(art.titulo)}</h4>
          <p class="text-[11px] text-slate-500 dark:text-violet-300/80">${escapeHtml(art.resumo)}</p>
        </div>
        <pre class="whitespace-pre-wrap font-sans text-xs bg-white/80 dark:bg-[#141024] p-3 rounded-lg border border-violet-100 dark:border-violet-900/60 text-slate-700 dark:text-violet-100">${escapeHtml(art.conteudo)}</pre>
      </article>
    `).join('');
  }
}

async function transformarNoticiaEmAchado(idx) {
  const itens = cyberWikiCache?.noticias_mundo_cyber?.itens || [];
  const n = itens[idx];
  if (!n) return;
  const res = await apiFetch('/cyber/achados', {
    method: 'POST',
    body: JSON.stringify({
      titulo: `[Radar Cyber] ${n.titulo}`.slice(0, 175),
      severidade: n.destaque === 'Crítica' ? 'Crítica' : 'Alta',
      categoria: `Threat Intel (${n.fonte || 'Externo'})`,
      alvo: 'Verificar Parque / Rede',
      recomendacao: `${n.resumo || ''} Fonte: ${n.url || n.fonte || ''}`.slice(0, 580),
    }),
  });
  if (res.ok) {
    mostrarToast('Notícia convertida em tarefa na fila de Vulnerabilidades!', 'sucesso');
    await Promise.all([carregarAchadosCyber(), carregarResumoCyber()]);
  }
}

async function criarArtigoKbCyber(event) {
  event.preventDefault();
  const payload = {
    titulo: document.getElementById('kb_titulo').value.trim(),
    categoria: document.getElementById('kb_cat').value.trim(),
    resumo: document.getElementById('kb_resumo').value.trim(),
    conteudo: document.getElementById('kb_conteudo').value.trim(),
    tags: document.getElementById('kb_tags').value.trim(),
  };
  const res = await apiFetch('/cyber/conhecimento', { method: 'POST', body: JSON.stringify(payload) });
  if (!res.ok) {
    mostrarToast(await extrairErroResposta(res, 'Erro ao publicar guia interno.'), 'erro');
    return;
  }
  event.target.reset();
  mostrarToast('Playbook publicado na área interna da empresa!', 'sucesso');
  await carregarConhecimentoCyber(false);
}

async function excluirArtigoKbCyber(id) {
  const res = await apiFetch(`/cyber/conhecimento/${encodeURIComponent(id)}`, { method: 'DELETE' });
  if (res.ok) await carregarConhecimentoCyber(false);
}

function baixarRelatorioCyberPdf() {
  abrirPdfAutenticado('/cyber/relatorio/pdf', 'Erro ao gerar relatório PDF de Cibersegurança.');
}
