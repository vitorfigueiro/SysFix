// ==============================================================================
// SETOR AUTOMAÇÃO (CÂMERAS, INFRA, IOT, ALARMES) & SETOR CIBERSEGURANÇA
// ==============================================================================

let automacaoPilarAtual = 'todas';
let automacaoAtivosCache = [];
let cyberSubTabAtual = 'auditoria';
let cyberChecklistCache = [];
let cyberAchadosCache = [];
let cyberKbCache = [];
let cyberBotsDockMinimizado = false;

const CYBER_BOTS_DEF = [
  {
    id: 'bot_net',
    tab: 'auditoria',
    nome: 'Byte-Scanner',
    pacote: 'NET',
    cor: '#7C3AED',
    status: 'Carregando pacotes de auditoria TLS/Rede',
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
    pacote: 'DOCS',
    cor: '#6366F1',
    status: 'Sincronizando playbooks e hardening',
  },
  {
    id: 'bot_gov',
    tab: 'governanca',
    nome: 'Gov-Key',
    pacote: 'GOV',
    cor: '#F59E0B',
    status: 'Protegendo escopo autorizado do gestor',
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

  body.innerHTML = CYBER_BOTS_DEF.map(bot => {
    const ativo = cyberSubTabAtual === bot.tab;
    return `
      <button type="button" onclick="alternarCyberTab('${bot.tab}')" title="${escapeHtml(bot.status)}"
        class="cyber-bot-card flex items-center gap-2.5 px-3 py-2 rounded-xl border text-left transition-all ${ativo ? 'bg-violet-600 text-white border-violet-500 shadow-lg shadow-violet-600/25' : 'bg-white/95 dark:bg-[#141024]/95 text-slate-800 dark:text-white border-violet-200 dark:border-violet-800 hover:border-violet-400'}">
        <svg class="cyber-bot-svg w-9 h-9 shrink-0" viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
          <rect class="cyber-packet" x="28" y="4" width="16" height="11" rx="3" fill="${bot.cor}" stroke="#FFFFFF" stroke-width="1.5"/>
          <text x="36" y="11.5" text-anchor="middle" font-size="5.5" font-weight="bold" fill="#FFFFFF">${bot.pacote}</text>
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
    if (el) el.className = k === pilar ? act : inact;
  });
  if (pilar !== 'todas' && document.getElementById('aut_pilar')) {
    document.getElementById('aut_pilar').value = pilar;
  }
  renderizarTabelaAutomacao();
}

async function carregarPainelAutomacao() {
  if (!temPermissao('ver_automacao')) return;
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
// SETOR CIBERSEGURANÇA (AUDITORIA AUTORIZADA, ACHADOS, CHECKLISTS & KB)
// ==============================================================================

function alternarCyberTab(tab) {
  cyberSubTabAtual = tab;
  ['auditoria', 'achados', 'checklists', 'conhecimento', 'governanca'].forEach(key => {
    const el = document.getElementById(`cyberTab_${key}`);
    if (el) el.classList.toggle('hidden', key !== tab);
    const btn = document.getElementById(`btnCyberTab_${key}`);
    if (btn) {
      btn.className = key === tab
        ? 'px-3 py-1.5 text-xs font-bold rounded-lg bg-violet-600 text-white'
        : 'px-3 py-1.5 text-xs font-semibold rounded-lg text-violet-800 dark:text-violet-200';
    }
  });
  renderizarCyberBotsDock(false);
}

async function carregarPainelCyber() {
  if (!temPermissao('ver_ciberseguranca')) return;
  renderizarCyberBotsDock(false);
  alternarCyberTab(cyberSubTabAtual);

  await Promise.all([
    carregarResumoCyber(),
    carregarAchadosCyber(),
    carregarChecklistCyber(),
    carregarConhecimentoCyber(),
  ]);
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
  setTxt('kpiCybAuditorias', d.total_auditorias ?? 0);

  const govBadge = document.getElementById('kpiCybGovBadge');
  if (govBadge) {
    govBadge.textContent = gov.permitir_auditoria_rede ? '🟢 Autorizada pelo Gestor' : '🔒 Bloqueada (Aguarda Gestor)';
    govBadge.className = gov.permitir_auditoria_rede
      ? 'text-xs font-extrabold text-emerald-600 dark:text-emerald-400 mt-1.5'
      : 'text-xs font-extrabold text-amber-600 dark:text-amber-400 mt-1.5';
  }

  // Preenche seção de Governança
  const chkGov = document.getElementById('gov_permitir_auditoria');
  const txtEscopo = document.getElementById('gov_escopo');
  const txtObs = document.getElementById('gov_termo');
  const infoGov = document.getElementById('gov_info_auditoria');
  if (chkGov) chkGov.checked = Boolean(gov.permitir_auditoria_rede);
  if (txtEscopo) txtEscopo.value = (gov.escopo_autorizado || []).join('\n');
  if (txtObs) txtObs.value = gov.observacao_termo || '';
  if (infoGov) {
    infoGov.textContent = gov.permitir_auditoria_rede
      ? `Autorizada por ${gov.autorizado_por || 'Admin'} (${formatarDataBR(gov.autorizado_em)}). Escopo: ${(gov.escopo_autorizado || []).join(', ')}`
      : 'A auditoria ativa de rede está desabilitada por padrão. Somente administradores podem liberar.';
  }

  // Lista de auditorias recentes
  const listaAud = document.getElementById('listaAuditoriasCyber');
  const auds = Array.isArray(d.ultimas_auditorias) ? d.ultimas_auditorias : [];
  if (listaAud) {
    listaAud.innerHTML = auds.length === 0
      ? '<p class="text-slate-400">Nenhuma auditoria executada ou importada ainda.</p>'
      : auds.map(a => `
        <div class="p-3 rounded-xl bg-violet-50/70 dark:bg-[#1C1633] border border-violet-200/80 dark:border-violet-800/70 flex flex-wrap justify-between items-center gap-2">
          <div>
            <div class="font-bold text-slate-900 dark:text-white">${escapeHtml(a.alvo)} <span class="text-[10px] font-semibold text-violet-600">(${escapeHtml(a.tipo)})</span></div>
            <div class="text-[11px] text-slate-500 dark:text-violet-300/80">Hosts: ${a.hosts_ativos} • Portas/Itens: ${a.portas_expostas} • Achados: ${a.achados_count} • Por ${escapeHtml(a.executado_por)}</div>
          </div>
          <span class="px-2.5 py-1 rounded-full text-xs font-extrabold ${a.score >= 80 ? 'bg-emerald-100 text-emerald-800' : (a.score >= 50 ? 'bg-amber-100 text-amber-800' : 'bg-rose-100 text-rose-800')}">Score ${a.score}/100</span>
        </div>
      `).join('');
  }
}

async function executarAuditoriaCyber(event) {
  event.preventDefault();
  const alvo = document.getElementById('cyb_aud_alvo').value.trim();
  const tipo = document.getElementById('cyb_aud_tipo').value;
  const registrarAchados = document.getElementById('cyb_aud_gerar_achados').checked;
  const btn = document.getElementById('btnExecutarAuditoriaCyber');

  if (btn) btn.disabled = true;
  renderizarCyberBotsDock(true);
  try {
    const res = await apiFetch('/cyber/auditorias/executar', {
      method: 'POST',
      body: JSON.stringify({ alvo, tipo_auditoria: tipo, registrar_achados: registrarAchados }),
    });
    if (!res.ok) {
      mostrarToast(await extrairErroResposta(res, 'Auditoria bloqueada ou inválida.'), 'erro');
      return;
    }
    const data = await res.json();
    mostrarToast(`Auditoria concluída! Score ${data.auditoria.score}/100 (${data.achados_criados} achados registrados).`, 'sucesso');
    await Promise.all([carregarResumoCyber(), carregarAchadosCyber()]);
  } catch (e) {
    mostrarToast('Falha ao executar auditoria defensiva.', 'erro');
  } finally {
    if (btn) btn.disabled = false;
    renderizarCyberBotsDock(false);
  }
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
    await Promise.all([carregarResumoCyber(), carregarAchadosCyber()]);
  } catch (e) {
    mostrarToast('Arquivo JSON de coleta inválido.', 'erro');
  }
}

async function salvarGovernancaCyber(event) {
  event.preventDefault();
  const permitir = document.getElementById('gov_permitir_auditoria').checked;
  const escopo = document.getElementById('gov_escopo').value
    .split(/\r?\n|,/)
    .map(s => s.trim())
    .filter(Boolean);
  const termo = document.getElementById('gov_termo').value.trim();

  const res = await apiFetch('/cyber/governanca', {
    method: 'PUT',
    body: JSON.stringify({
      permitir_auditoria_rede: permitir,
      escopo_autorizado: escopo,
      observacao_termo: termo,
    }),
  });
  if (!res.ok) {
    mostrarToast(await extrairErroResposta(res, 'Erro ao salvar governança.'), 'erro');
    return;
  }
  const data = await res.json();
  sessaoAtual.tenant = data.tenant;
  mostrarToast('Política de governança e autorização atualizada!', 'sucesso');
  await carregarResumoCyber();
}

async function carregarAchadosCyber() {
  const res = await apiFetch('/cyber/achados');
  if (!res.ok) return;
  cyberAchadosCache = await res.json();
  const box = document.getElementById('listaAchadosCyber');
  if (!box) return;
  const podeGerir = temPermissao('gerenciar_auditoria_cyber');

  if (!cyberAchadosCache.length) {
    box.innerHTML = '<p class="text-slate-400">Nenhum achado ou vulnerabilidade registrada.</p>';
    return;
  }

  box.innerHTML = cyberAchadosCache.map(a => {
    const sevCls = a.severidade === 'Crítica'
      ? 'bg-rose-600 text-white'
      : (a.severidade === 'Alta' ? 'bg-orange-500 text-white' : (a.severidade === 'Média' ? 'bg-amber-400 text-slate-900' : 'bg-violet-100 text-violet-800'));
    return `
      <div class="p-3.5 rounded-xl bg-violet-50/70 dark:bg-[#1C1633] border border-violet-200/80 dark:border-violet-800/70 space-y-1.5">
        <div class="flex flex-wrap justify-between items-center gap-2">
          <div class="flex items-center gap-2">
            <span class="px-2 py-0.5 rounded text-[10px] font-extrabold ${sevCls}">${escapeHtml(a.severidade)}</span>
            <span class="font-bold text-xs text-slate-900 dark:text-white">${escapeHtml(a.titulo)}</span>
          </div>
          <span class="text-[11px] font-bold text-violet-700 dark:text-violet-300">${escapeHtml(a.status)}</span>
        </div>
        <div class="text-[11px] text-slate-500 dark:text-violet-300/80">${escapeHtml(a.categoria)} • Alvo: <b>${escapeHtml(a.alvo || '-')}</b></div>
        ${a.recomendacao ? `<div class="text-[11px] text-slate-700 dark:text-violet-100">💡 ${escapeHtml(a.recomendacao)}</div>` : ''}
        ${podeGerir ? `
          <div class="flex flex-wrap gap-1.5 pt-1">
            <button onclick="atualizarStatusAchadoCyber('${escapeHtml(a.id)}', 'Em Mitigação')" class="px-2 py-0.5 rounded bg-amber-50 text-amber-800 text-[10px] font-bold border border-amber-200">Em Mitigação</button>
            <button onclick="atualizarStatusAchadoCyber('${escapeHtml(a.id)}', 'Resolvido')" class="px-2 py-0.5 rounded bg-emerald-50 text-emerald-700 text-[10px] font-bold border border-emerald-200">✓ Resolvido</button>
            <button onclick="excluirAchadoCyber('${escapeHtml(a.id)}')" class="px-2 py-0.5 rounded bg-rose-50 text-rose-600 text-[10px] font-semibold">✕</button>
          </div>
        ` : ''}
      </div>
    `;
  }).join('');
}

async function criarAchadoCyber(event) {
  event.preventDefault();
  const payload = {
    titulo: document.getElementById('ach_titulo').value.trim(),
    severidade: document.getElementById('ach_sev').value,
    categoria: document.getElementById('ach_cat').value.trim(),
    alvo: document.getElementById('ach_alvo').value.trim(),
    recomendacao: document.getElementById('ach_rec').value.trim(),
  };
  const res = await apiFetch('/cyber/achados', { method: 'POST', body: JSON.stringify(payload) });
  if (!res.ok) {
    mostrarToast(await extrairErroResposta(res, 'Erro ao registrar achado.'), 'erro');
    return;
  }
  event.target.reset();
  mostrarToast('Achado registrado!', 'sucesso');
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

async function carregarConhecimentoCyber() {
  const busca = document.getElementById('kb_busca')?.value || '';
  const res = await apiFetch(`/cyber/conhecimento?busca=${encodeURIComponent(busca)}`);
  if (!res.ok) return;
  cyberKbCache = await res.json();
  const box = document.getElementById('listaConhecimentoCyber');
  if (!box) return;
  const podeGerir = temPermissao('gerenciar_auditoria_cyber');

  box.innerHTML = cyberKbCache.map(art => `
    <article class="p-4 rounded-xl bg-violet-50/70 dark:bg-[#1C1633] border border-violet-200/80 dark:border-violet-800/70 space-y-2 text-xs">
      <div class="flex flex-wrap justify-between items-start gap-2">
        <div>
          <span class="px-2 py-0.5 rounded bg-violet-600 text-white text-[10px] font-bold">${escapeHtml(art.categoria)}</span>
          <span class="px-2 py-0.5 rounded bg-white dark:bg-[#141024] border border-violet-200 dark:border-violet-700 text-[10px] font-semibold">${escapeHtml(art.nivel)} • ${escapeHtml(art.atualizado_em || '')}</span>
          <h4 class="text-sm font-extrabold text-slate-900 dark:text-white mt-1.5">${escapeHtml(art.titulo)}</h4>
          <p class="text-[11px] text-slate-500 dark:text-violet-300/80">${escapeHtml(art.resumo)}</p>
        </div>
        ${(!art.padrao_sistema && podeGerir) ? `<button onclick="excluirArtigoKbCyber('${escapeHtml(art.id)}')" class="px-2 py-1 rounded bg-rose-50 text-rose-600 text-[10px] font-bold">✕</button>` : ''}
      </div>
      <pre class="whitespace-pre-wrap font-sans text-xs bg-white/80 dark:bg-[#141024] p-3 rounded-lg border border-violet-100 dark:border-violet-900/60 text-slate-700 dark:text-violet-100">${escapeHtml(art.conteudo)}</pre>
    </article>
  `).join('');
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
    mostrarToast(await extrairErroResposta(res, 'Erro ao publicar guia.'), 'erro');
    return;
  }
  event.target.reset();
  mostrarToast('Playbook adicionado à base de conhecimento!', 'sucesso');
  await carregarConhecimentoCyber();
}

async function excluirArtigoKbCyber(id) {
  const res = await apiFetch(`/cyber/conhecimento/${encodeURIComponent(id)}`, { method: 'DELETE' });
  if (res.ok) await carregarConhecimentoCyber();
}

function baixarRelatorioCyberPdf() {
  abrirPdfAutenticado('/cyber/relatorio/pdf', 'Erro ao gerar relatório PDF de Cibersegurança.');
}
