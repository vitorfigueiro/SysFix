// ==============================================================================
// MÓDULO TI, PROVEDORES (ISP), CHECKLIST, LAUDOS, HISTÓRICO E OPERAÇÕES
// ==============================================================================

let presetsTiCache = null;
let modelosEmpresaDraft = [];
let modeloIndiceEmEdicao = -1;
let logoEmpresaBase64Draft = '';
let laudoEmEdicaoId = null;
let movEstoqueItemId = null;
let assinaturaDesenhada = false;

async function carregarPresetsTI() {
  try {
    const res = await apiFetch('/empresa/presets-ti');
    if (res.ok) {
      presetsTiCache = await res.json();
    }
  } catch (e) {
    console.warn('Não foi possível carregar presets TI:', e);
  }
}

function renderizarExtrasFicha(d) {
  const box = document.getElementById('f_extra_resumo');
  if (!box) return;
  const tags = [];
  if (d.orcamento && d.orcamento.status) {
    const valTxt = temPermissao('ver_custos') && d.orcamento.valor !== undefined
      ? ` — R$ ${formatarMoedaBR(d.orcamento.valor)}`
      : '';
    tags.push(`<span class="px-2.5 py-1 rounded-lg bg-amber-50 dark:bg-amber-950/50 text-amber-800 dark:text-amber-300 border border-amber-200 dark:border-amber-800 font-semibold">💵 Orçamento: ${escapeHtml(d.orcamento.status)}${escapeHtml(valTxt)}</span>`);
  }
  if (d.assinatura_entrega && d.assinatura_entrega.nome) {
    tags.push(`<span class="px-2.5 py-1 rounded-lg bg-emerald-50 dark:bg-emerald-950/50 text-emerald-700 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-800 font-semibold">✍️ Assinado por ${escapeHtml(d.assinatura_entrega.nome)}</span>`);
  }
  box.innerHTML = tags.join('');
}

// ==============================================================================
// CHECKLIST TÉCNICO DE BANCADA NA FICHA
// ==============================================================================

function renderizarChecklistFicha(d) {
  const container = document.getElementById('fichaChecklistItens');
  if (!container) return;
  const itens = Array.isArray(d.checklist) ? d.checklist : [];
  const podeEditar = temPermissao('editar_entrada');

  if (itens.length === 0) {
    container.innerHTML = '<p class="col-span-2 text-slate-400 dark:text-violet-300/60">Nenhum checklist aplicado a esta O.S. Clique em "Carregar Checklist Padrão" para iniciar.</p>';
    return;
  }

  container.innerHTML = itens.map((c, idx) => `
    <label class="flex items-center gap-2 p-2.5 rounded-xl bg-violet-50/70 dark:bg-[#1C1633] border border-violet-200/80 dark:border-violet-800/70 ${podeEditar ? 'cursor-pointer hover:bg-violet-100/70' : ''}">
      <input type="checkbox" ${c.concluido ? 'checked' : ''} ${podeEditar ? '' : 'disabled'} onchange="alternarItemChecklistOs(${idx}, this.checked)" class="rounded border-violet-300 text-violet-600 focus:ring-violet-400">
      <span class="${c.concluido ? 'line-through text-slate-400 dark:text-violet-300/60' : 'text-slate-800 dark:text-white font-medium'}">${escapeHtml(c.item)}</span>
    </label>
  `).join('');
}

async function alternarItemChecklistOs(idx, concluido) {
  if (!registroSelecionadoDados) return;
  const novaLista = (registroSelecionadoDados.checklist || []).map((c, i) =>
    i === idx ? { ...c, concluido } : c
  );
  await salvarChecklistOs(novaLista);
}

async function aplicarChecklistPadraoNaOs() {
  if (!registroSelecionadoId || !sessaoAtual) return;
  const padrao = sessaoAtual.tenant?.checklist_padrao || [];
  if (padrao.length === 0) {
    mostrarToast('Nenhum checklist padrão configurado na empresa.', 'erro');
    return;
  }
  const existentes = registroSelecionadoDados?.checklist || [];
  const mapaExist = new Map(existentes.map(c => [String(c.item).toLowerCase(), Boolean(c.concluido)]));
  const combinado = padrao.map(texto => ({
    item: texto,
    concluido: mapaExist.get(String(texto).toLowerCase()) || false
  }));
  await salvarChecklistOs(combinado);
}

async function salvarChecklistOs(lista) {
  if (!registroSelecionadoId) return;
  try {
    const res = await apiFetch(`/equipamentos/${registroSelecionadoId}/checklist`, {
      method: 'PUT',
      body: JSON.stringify({ checklist: lista })
    });
    if (!res.ok) {
      mostrarToast(await extrairErroResposta(res, 'Erro ao salvar checklist.'), 'erro');
      return;
    }
    const data = await res.json();
    registroSelecionadoDados = data.registro;
    renderizarChecklistFicha(registroSelecionadoDados);
    mostrarToast('Checklist técnico atualizado!', 'sucesso');
  } catch (e) {
    mostrarToast('Falha ao atualizar checklist.', 'erro');
  }
}

// ==============================================================================
// COMPROVANTE PDF, LINK PÚBLICO, WHATSAPP, ASSINATURA, ORÇAMENTO E HISTÓRICO
// ==============================================================================

async function abrirPdfAutenticado(url, fallbackErro) {
  try {
    const res = await apiFetch(url);
    if (!res.ok) {
      mostrarToast(await extrairErroResposta(res, fallbackErro), 'erro');
      return;
    }
    const blob = await res.blob();
    const blobUrl = URL.createObjectURL(blob);
    window.open(blobUrl, '_blank');
    setTimeout(() => URL.revokeObjectURL(blobUrl), 60000);
  } catch (e) {
    mostrarToast(fallbackErro, 'erro');
  }
}

function baixarComprovanteOsPdf() {
  if (!registroSelecionadoId) return;
  abrirPdfAutenticado(`/equipamentos/${registroSelecionadoId}/comprovante/pdf`, 'Erro ao gerar comprovante da O.S.');
}

async function obterLinkPublicoAtual() {
  if (!registroSelecionadoId) return null;
  const res = await apiFetch(`/equipamentos/${registroSelecionadoId}/link-publico`, { method: 'POST' });
  if (!res.ok) {
    mostrarToast(await extrairErroResposta(res, 'Não foi possível gerar o link público.'), 'erro');
    return null;
  }
  const data = await res.json();
  if (registroSelecionadoDados) {
    registroSelecionadoDados.codigo_acompanhamento = data.codigo;
    const fCod = document.getElementById('f_codigo');
    if (fCod) fCod.innerText = data.codigo;
  }
  return data;
}

async function copiarLinkPublicoOs() {
  const data = await obterLinkPublicoAtual();
  if (!data) return;
  try {
    await navigator.clipboard.writeText(data.url);
    mostrarToast(`Link copiado: ${data.url}`, 'sucesso');
  } catch (_) {
    window.prompt('Copie o link público de acompanhamento:', data.url);
  }
}

async function enviarWhatsappOs(textoExtra = '') {
  if (!registroSelecionadoDados) return;
  const linkData = await obterLinkPublicoAtual();
  const d = registroSelecionadoDados;
  const empresa = sessaoAtual?.tenant?.nome || 'SysFix';
  const statusTxt = String(d.status || 'Pendente');
  const linhas = [
    `*${empresa}* — Atualização da O.S. *#${d.id}*`,
    `Equipamento: *${d.equipamento}* (Tomb: ${d.tombamento || '-'})`,
    `Status atual: *${statusTxt}*`,
  ];
  if (d.resolucao) linhas.push(`Parecer/Resolução: ${d.resolucao}`);
  if (textoExtra) linhas.push(textoExtra);
  if (linkData?.url) linhas.push(`Acompanhe online (timeline, laudos e orçamento): ${linkData.url}`);

  const url = `https://wa.me/?text=${encodeURIComponent(linhas.join('\n'))}`;
  window.open(url, '_blank');
}

async function abrirHistoricoAtivoModal() {
  if (!registroSelecionadoId) return;
  const box = document.getElementById('conteudoHistoricoAtivo');
  box.innerHTML = '<p class="text-slate-400">Carregando histórico e timeline...</p>';
  document.getElementById('modalHistoricoAtivo').classList.remove('hidden');

  try {
    const res = await apiFetch(`/equipamentos/${registroSelecionadoId}/historico`);
    if (!res.ok) {
      box.innerHTML = '<p class="text-rose-500">Erro ao carregar histórico do equipamento.</p>';
      return;
    }
    const h = await res.json();
    const podeCustos = temPermissao('ver_custos') && h.custo_acumulado !== undefined;
    const alertaReinc = h.reincidente
      ? `<div class="p-3 rounded-xl bg-amber-50 dark:bg-amber-950/50 border border-amber-300 dark:border-amber-700 text-amber-800 dark:text-amber-200 font-bold">
           ⚠️ Alerta de Reincidência: Este equipamento já possui ${h.total_passagens} O.S. registradas nesta empresa!${podeCustos ? ` Custo acumulado: R$ ${formatarMoedaBR(h.custo_acumulado)}.` : ''}
         </div>`
      : `<div class="p-3 rounded-xl bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-800 text-emerald-700 dark:text-emerald-300 font-semibold">
           ✅ Primeira passagem registrada para este tombamento/serial.${podeCustos ? ` Custo acumulado: R$ ${formatarMoedaBR(h.custo_acumulado)}.` : ''}
         </div>`;

    const passagensHtml = (h.registros || []).map(r => `
      <div class="p-3 rounded-xl bg-violet-50/70 dark:bg-[#1C1633] border border-violet-200/80 dark:border-violet-800/70 flex justify-between gap-2">
        <div>
          <div class="font-bold text-slate-900 dark:text-white">#${r.id} — ${escapeHtml(r.equipamento)} (${escapeHtml(r.status)})</div>
          <div class="text-[11px] text-slate-600 dark:text-violet-200">Entrada: ${formatarDataBR(r.data_coleta)} • Problema: ${escapeHtml(r.problema || '-')}</div>
          ${r.resolucao ? `<div class="text-[11px] text-emerald-700 dark:text-emerald-300">Resolução: ${escapeHtml(r.resolucao)}</div>` : ''}
        </div>
        ${r.id !== registroSelecionadoId ? `<button onclick="fecharModalHistorico(); selecionarItem(${r.id})" class="px-2.5 py-1 h-fit rounded-lg bg-violet-600 text-white text-[11px] font-bold">Abrir</button>` : '<span class="text-[10px] font-bold text-violet-600">Atual</span>'}
      </div>
    `).join('');

    const timeline = (h.timeline_atual || []).slice().reverse();
    const timelineHtml = timeline.length === 0
      ? '<p class="text-slate-400">Sem eventos registrados.</p>'
      : timeline.map(ev => `
        <div class="p-2.5 rounded-xl bg-white dark:bg-[#141024] border border-violet-100 dark:border-violet-900/60 flex justify-between gap-2">
          <div>
            <div class="font-semibold text-slate-800 dark:text-white">${escapeHtml(ev.descricao)}</div>
            <div class="text-[10px] text-slate-500 dark:text-violet-300/70">Por ${escapeHtml(ev.autor || 'Sistema')} • ${ev.publico ? 'Visível ao cliente' : 'Interno'}</div>
          </div>
          <span class="text-[10px] text-slate-400 shrink-0">${formatarDataBR(ev.data_hora)}</span>
        </div>
      `).join('');

    box.innerHTML = `
      ${alertaReinc}
      <div class="space-y-2">
        <h4 class="font-bold text-violet-800 dark:text-violet-300 uppercase">Passagens do Equipamento (${h.total_passagens})</h4>
        <div class="space-y-2">${passagensHtml}</div>
      </div>
      <div class="space-y-2">
        <h4 class="font-bold text-violet-800 dark:text-violet-300 uppercase">Linha do Tempo desta O.S.</h4>
        <div class="space-y-1.5">${timelineHtml}</div>
      </div>
    `;
  } catch (e) {
    box.innerHTML = '<p class="text-rose-500">Falha ao carregar histórico.</p>';
  }
}

function fecharModalHistorico() {
  document.getElementById('modalHistoricoAtivo').classList.add('hidden');
}

// --- Assinatura Digital Canvas ---
function inicializarCanvasAssinatura() {
  const canvas = document.getElementById('canvasAssinatura');
  if (!canvas || canvas.dataset.init === '1') return;
  canvas.dataset.init = '1';
  const ctx = canvas.getContext('2d');
  let desenhando = false;

  const pos = (ev) => {
    const rect = canvas.getBoundingClientRect();
    const touch = ev.touches ? ev.touches[0] : ev;
    return {
      x: ((touch.clientX - rect.left) / rect.width) * canvas.width,
      y: ((touch.clientY - rect.top) / rect.height) * canvas.height
    };
  };

  const iniciar = (ev) => {
    ev.preventDefault();
    desenhando = true;
    assinaturaDesenhada = true;
    const p = pos(ev);
    ctx.beginPath();
    ctx.moveTo(p.x, p.y);
  };
  const mover = (ev) => {
    if (!desenhando) return;
    ev.preventDefault();
    const p = pos(ev);
    ctx.lineWidth = 2.4;
    ctx.lineCap = 'round';
    ctx.strokeStyle = '#1E1B4B';
    ctx.lineTo(p.x, p.y);
    ctx.stroke();
  };
  const parar = () => { desenhando = false; };

  canvas.addEventListener('mousedown', iniciar);
  canvas.addEventListener('mousemove', mover);
  window.addEventListener('mouseup', parar);
  canvas.addEventListener('touchstart', iniciar, { passive: false });
  canvas.addEventListener('touchmove', mover, { passive: false });
  canvas.addEventListener('touchend', parar);
}

function limparCanvasAssinatura() {
  const canvas = document.getElementById('canvasAssinatura');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  assinaturaDesenhada = false;
}

function abrirModalAssinatura() {
  if (!registroSelecionadoId) return;
  inicializarCanvasAssinatura();
  limparCanvasAssinatura();
  document.getElementById('ass_nome').value = registroSelecionadoDados?.assinatura_entrega?.nome || '';
  document.getElementById('ass_doc').value = registroSelecionadoDados?.assinatura_entrega?.documento || '';
  document.getElementById('modalAssinatura').classList.remove('hidden');
}

function fecharModalAssinatura() {
  document.getElementById('modalAssinatura').classList.add('hidden');
}

async function salvarAssinaturaOs() {
  if (!registroSelecionadoId) return;
  const nome = document.getElementById('ass_nome').value.trim();
  const documento = document.getElementById('ass_doc').value.trim();
  if (!nome) {
    mostrarToast('Informe o nome do assinante.', 'erro');
    return;
  }
  if (!assinaturaDesenhada) {
    mostrarToast('Desenhe a assinatura no quadro.', 'erro');
    return;
  }
  const canvas = document.getElementById('canvasAssinatura');
  const imagemBase64 = canvas.toDataURL('image/png');
  const res = await apiFetch(`/equipamentos/${registroSelecionadoId}/assinatura`, {
    method: 'PUT',
    body: JSON.stringify({ nome, documento, imagem_base64: imagemBase64 })
  });
  if (!res.ok) {
    mostrarToast(await extrairErroResposta(res, 'Erro ao salvar assinatura.'), 'erro');
    return;
  }
  const data = await res.json();
  registroSelecionadoDados = data.registro;
  renderizarExtrasFicha(registroSelecionadoDados);
  fecharModalAssinatura();
  mostrarToast('Assinatura salva e vinculada ao comprovante/laudo!', 'sucesso');
}

// --- Orçamento ---
function abrirModalOrcamento() {
  if (!registroSelecionadoId) return;
  const orc = registroSelecionadoDados?.orcamento || {};
  document.getElementById('orc_desc_input').value = orc.descricao || '';
  document.getElementById('orc_valor_input').value = orc.valor || '';
  document.getElementById('modalOrcamento').classList.remove('hidden');
}

function fecharModalOrcamento() {
  document.getElementById('modalOrcamento').classList.add('hidden');
}

async function salvarOrcamentoOs() {
  if (!registroSelecionadoId) return;
  const descricao = document.getElementById('orc_desc_input').value.trim();
  const valor = document.getElementById('orc_valor_input').value;
  const res = await apiFetch(`/equipamentos/${registroSelecionadoId}/orcamento`, {
    method: 'PUT',
    body: JSON.stringify({ descricao, valor })
  });
  if (!res.ok) {
    mostrarToast(await extrairErroResposta(res, 'Erro ao salvar orçamento.'), 'erro');
    return;
  }
  const data = await res.json();
  registroSelecionadoDados = data.registro;
  fecharModalOrcamento();
  await selecionarItem(registroSelecionadoId);
  mostrarToast('Orçamento salvo! Envie o link público ou WhatsApp para o cliente aprovar.', 'sucesso');
}

// ==============================================================================
// LAUDOS & PARECERES TÉCNICOS ANEXADOS + PERSONALIZAÇÃO DE MODELOS
// ==============================================================================

function renderizarLaudosFicha(d) {
  const container = document.getElementById('fichaListaLaudos');
  if (!container) return;
  const laudos = Array.isArray(d.laudos) ? d.laudos : [];
  const podeGerenciar = temPermissao('gerenciar_laudos');
  const podeCompartilhar = temPermissao('compartilhar_os');

  if (laudos.length === 0) {
    container.innerHTML = '<p class="text-slate-400 dark:text-violet-300/60">Nenhum laudo ou parecer técnico anexado a este equipamento ainda.</p>';
    return;
  }

  container.innerHTML = laudos.slice().reverse().map(l => `
    <div class="p-3.5 rounded-xl bg-violet-50/70 dark:bg-[#1C1633] border border-violet-200/80 dark:border-violet-800/70 space-y-2">
      <div class="flex flex-wrap justify-between items-start gap-2">
        <div>
          <span class="text-[10px] font-extrabold text-violet-600 dark:text-violet-400 uppercase">${escapeHtml(l.numero)} • ${formatarDataBR(l.emitido_em)}</span>
          <h4 class="text-xs font-bold text-slate-900 dark:text-white">${escapeHtml(l.titulo)}</h4>
          <p class="text-[11px] text-slate-500 dark:text-violet-300/80">Conclusão: <b>${escapeHtml(l.conclusao)}</b> • Técnico: ${escapeHtml(l.tecnico_responsavel)}</p>
        </div>
        <span class="px-2 py-0.5 text-[10px] font-bold rounded-full ${l.visivel_cliente ? 'bg-emerald-50 dark:bg-emerald-950/60 text-emerald-700 dark:text-emerald-300' : 'bg-slate-200 dark:bg-slate-800 text-slate-600 dark:text-slate-300'}">
          ${l.visivel_cliente ? 'Visível ao cliente' : 'Uso interno'}
        </span>
      </div>
      <p class="text-[11px] text-slate-700 dark:text-violet-100 line-clamp-2 whitespace-pre-line">${escapeHtml(l.parecer)}</p>
      <div class="flex flex-wrap gap-1.5 pt-1">
        ${podeGerenciar ? `
          <button type="button" onclick="abrirLaudoPdf('${escapeHtml(l.id)}')" class="px-2.5 py-1 rounded-lg bg-violet-600 hover:bg-violet-700 text-white text-[11px] font-bold">🖨️ Imprimir / PDF</button>
          <button type="button" onclick="editarLaudoAnexado('${escapeHtml(l.id)}')" class="px-2.5 py-1 rounded-lg bg-white dark:bg-[#141024] border border-violet-200 dark:border-violet-700 text-violet-800 dark:text-violet-200 text-[11px] font-semibold">✏️ Editar</button>
          <button type="button" onclick="excluirLaudoAnexado('${escapeHtml(l.id)}')" class="px-2.5 py-1 rounded-lg bg-rose-50 dark:bg-rose-950/50 text-rose-600 dark:text-rose-300 border border-rose-200 dark:border-rose-800 text-[11px] font-semibold">🗑️ Excluir</button>
        ` : ''}
        ${podeCompartilhar ? `
          <button type="button" onclick="enviarWhatsappOs('Laudo Técnico emitido: *${escapeHtml(l.numero)}* (${escapeHtml(l.conclusao)}). Baixe o PDF pelo link abaixo:')" class="px-2.5 py-1 rounded-lg bg-emerald-50 dark:bg-emerald-950/50 text-emerald-700 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-800 text-[11px] font-semibold">💬 Enviar no WhatsApp</button>
        ` : ''}
      </div>
    </div>
  `).join('');
}

function substituirVariaveisLaudoNoCliente(texto, reg) {
  if (!texto) return '';
  const emp = sessaoAtual?.tenant?.nome || 'SysFix';
  const mapa = {
    empresa: emp,
    id: reg?.id ?? '-',
    equipamento: reg?.equipamento || '-',
    tombamento: reg?.tombamento || '-',
    serial_mac: reg?.serial_mac || '-',
    categoria: reg?.categoria || 'Geral',
    tipo_fluxo: reg?.tipo_fluxo || 'Manutenção',
    origem: reg?.origem || '-',
    localizacao: reg?.localizacao || '-',
    problema: reg?.problema || 'Não informado',
    resolucao: reg?.resolucao || 'Em análise técnica',
    tecnico: sessaoAtual?.user?.nome || reg?.tecnico_entrega || reg?.tecnico_coleta || '-',
    data_coleta: formatarDataBR(reg?.data_coleta),
    data_entrega: reg?.data_entrega ? formatarDataBR(reg.data_entrega) : formatarDataBR(obterDataHojeISO()),
    garantia_ate: reg?.garantia_ate ? formatarDataBR(reg.garantia_ate) : (Number(reg?.garantia_dias) > 0 ? `${reg.garantia_dias} dias` : 'Sem garantia'),
    data_atual: formatarDataBR(obterDataHojeISO()),
  };
  return String(texto).replace(/\{\{\s*([a-zA-Z0-9_]+)\s*\}\}/g, (_, k) => mapa[k.toLowerCase()] ?? '-');
}

function popularSelectModelosLaudo() {
  const select = document.getElementById('ld_modelo_select');
  if (!select) return;
  const modelos = sessaoAtual?.tenant?.modelos_laudo || presetsTiCache?.modelos_laudo_padrao || [];
  select.innerHTML = modelos.map(m => `<option value="${escapeHtml(m.id)}">${escapeHtml(m.nome)}</option>`).join('');
}

function _limparTombamentoPadrao(reg) {
  if (!reg) return '';
  if (String(reg.tombamento_tipo || '').toLowerCase() === 'sem_tombamento') return '';
  const t = String(reg.tombamento || '').trim();
  if (!t || ['-', 'sem tombamento', 's/t', '0000'].includes(t.toLowerCase())) return '';
  return t;
}

function abrirModalLaudo(laudoExistente = null) {
  if (!registroSelecionadoId || !registroSelecionadoDados) {
    mostrarToast('Selecione um equipamento primeiro.', 'erro');
    return;
  }
  const reg = registroSelecionadoDados;
  laudoEmEdicaoId = laudoExistente ? laudoExistente.id : null;
  document.getElementById('modalLaudoHeader').textContent = laudoExistente
    ? `✏️ Editar Laudo ${laudoExistente.numero}`
    : `📋 Emitir Parecer / Laudo — O.S. #${registroSelecionadoId}`;

  if (laudoExistente) {
    document.getElementById('ld_data').value = laudoExistente.data_laudo || converterDataParaInput(laudoExistente.emitido_em) || obterDataHojeISO();
    document.getElementById('ld_cliente').value = laudoExistente.cliente_para || reg.origem || '';
    document.getElementById('ld_equipamento').value = laudoExistente.equipamento_laudo || reg.equipamento || '';
    document.getElementById('ld_tombamento').value = laudoExistente.tombamento_laudo || _limparTombamentoPadrao(reg);
    document.getElementById('ld_titulo').value = laudoExistente.titulo || 'Parecer Técnico';
    document.getElementById('ld_conclusao').value = laudoExistente.conclusao || 'Reparado / Aprovado';
    document.getElementById('ld_diagnostico').value = laudoExistente.diagnostico || '';
    document.getElementById('ld_parecer').value = laudoExistente.parecer || '';
    document.getElementById('ld_recomendacoes').value = laudoExistente.recomendacoes || '';
    document.getElementById('ld_tecnico').value = laudoExistente.tecnico_responsavel || 'Vitor Figueiró da Silva';
    document.getElementById('ld_reg_prof').value = laudoExistente.registro_profissional || 'T.I';
    document.getElementById('ld_inc_checklist').checked = Boolean(laudoExistente.incluir_checklist);
    document.getElementById('ld_visivel_cliente').checked = laudoExistente.visivel_cliente !== false;
  } else {
    document.getElementById('ld_data').value = obterDataHojeISO();
    document.getElementById('ld_cliente').value = reg.origem || '';
    document.getElementById('ld_equipamento').value = reg.equipamento || '';
    document.getElementById('ld_tombamento').value = _limparTombamentoPadrao(reg);
    document.getElementById('ld_titulo').value = 'Parecer Técnico';
    document.getElementById('ld_conclusao').value = 'Reparado / Aprovado';
    document.getElementById('ld_diagnostico').value = reg.problema || '';
    document.getElementById('ld_parecer').value = '';
    document.getElementById('ld_recomendacoes').value = '';
    document.getElementById('ld_tecnico').value = 'Vitor Figueiró da Silva';
    document.getElementById('ld_reg_prof').value = 'T.I';
    document.getElementById('ld_inc_checklist').checked = false;
    document.getElementById('ld_visivel_cliente').checked = true;
  }
  document.getElementById('modalLaudo').classList.remove('hidden');
}

function fecharModalLaudo() {
  laudoEmEdicaoId = null;
  document.getElementById('modalLaudo').classList.add('hidden');
}

function aplicarObjetoModeloNoFormularioLaudo(modelo) {
  if (!modelo || !registroSelecionadoDados) return;
  const reg = registroSelecionadoDados;
  document.getElementById('ld_titulo').value = substituirVariaveisLaudoNoCliente(modelo.titulo || 'Parecer Técnico', reg);
  document.getElementById('ld_conclusao').value = modelo.conclusao_padrao || 'Reparado / Aprovado';
  document.getElementById('ld_diagnostico').value = substituirVariaveisLaudoNoCliente(modelo.diagnostico || '', reg);
  document.getElementById('ld_parecer').value = substituirVariaveisLaudoNoCliente(modelo.corpo || '', reg);
  document.getElementById('ld_recomendacoes').value = substituirVariaveisLaudoNoCliente(modelo.recomendacoes || '', reg);
}

function aplicarModeloSelecionadoNoLaudo() {
  return;
}

async function importarModeloDiretoNoLaudo(event) {
  if (event?.target) event.target.value = '';
}

async function salvarLaudoModal(abrirPdfDepois = false) {
  if (!registroSelecionadoId) return;
  const payload = {
    data_laudo: document.getElementById('ld_data')?.value || obterDataHojeISO(),
    cliente_para: document.getElementById('ld_cliente')?.value.trim() || '',
    equipamento_laudo: document.getElementById('ld_equipamento')?.value.trim() || '',
    tombamento_laudo: document.getElementById('ld_tombamento')?.value.trim() || '',
    titulo: document.getElementById('ld_titulo').value.trim() || 'Parecer Técnico',
    conclusao: document.getElementById('ld_conclusao').value,
    diagnostico: document.getElementById('ld_diagnostico').value.trim(),
    parecer: document.getElementById('ld_parecer').value.trim(),
    recomendacoes: document.getElementById('ld_recomendacoes').value.trim(),
    tecnico_responsavel: document.getElementById('ld_tecnico').value.trim() || 'Vitor Figueiró da Silva',
    registro_profissional: document.getElementById('ld_reg_prof').value.trim() || 'T.I',
    email_tecnico: sessaoAtual?.user?.email || 'vitor.figueiro@centralinformatica.com.br',
    incluir_checklist: document.getElementById('ld_inc_checklist').checked,
    visivel_cliente: document.getElementById('ld_visivel_cliente').checked,
  };
  if (!payload.parecer) {
    mostrarToast('Preencha o campo "2. Problema Identificado".', 'erro');
    return;
  }

  const path = laudoEmEdicaoId
    ? `/equipamentos/${registroSelecionadoId}/laudos/${encodeURIComponent(laudoEmEdicaoId)}`
    : `/equipamentos/${registroSelecionadoId}/laudos`;
  const res = await apiFetch(path, {
    method: laudoEmEdicaoId ? 'PUT' : 'POST',
    body: JSON.stringify(payload)
  });
  if (!res.ok) {
    mostrarToast(await extrairErroResposta(res, 'Não foi possível salvar o laudo.'), 'erro');
    return;
  }
  const data = await res.json();
  const idReg = registroSelecionadoId;
  const laudoId = data.laudo.id;
  fecharModalLaudo();
  await carregarLista();
  await selecionarItem(idReg);
  mostrarToast(data.mensagem || 'Laudo anexado à O.S.!', 'sucesso');

  if (abrirPdfDepois) {
    abrirLaudoPdf(laudoId);
  }
}

function editarLaudoAnexado(laudoId) {
  const laudo = (registroSelecionadoDados?.laudos || []).find(l => l.id === laudoId);
  if (laudo) abrirModalLaudo(laudo);
}

async function excluirLaudoAnexado(laudoId) {
  if (!registroSelecionadoId) return;
  if (!window.confirm('Remover este laudo técnico anexado ao equipamento?')) return;
  const res = await apiFetch(`/equipamentos/${registroSelecionadoId}/laudos/${encodeURIComponent(laudoId)}`, {
    method: 'DELETE'
  });
  if (!res.ok) {
    mostrarToast(await extrairErroResposta(res, 'Erro ao excluir laudo.'), 'erro');
    return;
  }
  await selecionarItem(registroSelecionadoId);
  mostrarToast('Laudo removido.', 'info');
}

function abrirLaudoPdf(laudoId) {
  if (!registroSelecionadoId) return;
  abrirPdfAutenticado(
    `/equipamentos/${registroSelecionadoId}/laudos/${encodeURIComponent(laudoId)}/pdf`,
    'Erro ao gerar PDF do laudo técnico.'
  );
}

// ==============================================================================
// PERSONALIZAÇÃO DE TIMBRE, CHECKLIST E MODELOS NA TELA DA EMPRESA
// ==============================================================================

function carregarBrandingEModelosNaUI() {
  const tenant = sessaoAtual?.tenant;
  if (!tenant) return;
  document.getElementById('brand_cnpj').value = tenant.cnpj || '';
  document.getElementById('brand_tel').value = tenant.telefone || '';
  document.getElementById('brand_end').value = tenant.endereco || '';
  document.getElementById('brand_rodape').value = tenant.rodape_documento || '';
  const cor = tenant.cor_documento || '#4C1D95';
  document.getElementById('brand_cor').value = cor;
  document.getElementById('brand_cor_label').textContent = cor;
  document.getElementById('brand_cor').oninput = (e) => {
    document.getElementById('brand_cor_label').textContent = e.target.value.toUpperCase();
  };

  logoEmpresaBase64Draft = tenant.logo_base64 || '';
  document.getElementById('brand_logo_preview').src = logoEmpresaBase64Draft || '/static/favicon.svg';
  document.getElementById('brand_checklist').value = (tenant.checklist_padrao || []).join('\n');

  modelosEmpresaDraft = Array.isArray(tenant.modelos_laudo)
    ? tenant.modelos_laudo.map(m => ({ ...m }))
    : [];
  renderizarListaModelosEmpresa();
  if (modelosEmpresaDraft.length > 0) {
    selecionarModeloEmpresa(0);
  }
}

function selecionarLogoEmpresa(event) {
  const file = event.target.files && event.target.files[0];
  if (!file) return;
  if (file.size > 300 * 1024) {
    mostrarToast('A logo deve ter no máximo 300 KB.', 'erro');
    return;
  }
  const reader = new FileReader();
  reader.onload = () => {
    logoEmpresaBase64Draft = String(reader.result || '');
    document.getElementById('brand_logo_preview').src = logoEmpresaBase64Draft;
  };
  reader.readAsDataURL(file);
}

function limparLogoEmpresa() {
  logoEmpresaBase64Draft = '';
  document.getElementById('brand_logo_file').value = '';
  document.getElementById('brand_logo_preview').src = '/static/favicon.svg';
}

function aplicarPresetChecklist(presetKey) {
  const preset = presetsTiCache?.checklist_presets?.[presetKey];
  if (!preset) return;
  document.getElementById('brand_checklist').value = (preset.itens || []).join('\n');
  mostrarToast(`Preset "${preset.nome}" aplicado! Clique em Salvar Personalizações.`, 'info');
}

function renderizarListaModelosEmpresa() {
  const box = document.getElementById('listaModelosEmpresa');
  if (!box) return;
  box.innerHTML = modelosEmpresaDraft.map((m, idx) => `
    <button type="button" onclick="selecionarModeloEmpresa(${idx})" class="w-full text-left p-2.5 rounded-xl border ${idx === modeloIndiceEmEdicao ? 'bg-violet-600 text-white border-violet-600' : 'bg-violet-50/70 dark:bg-[#1C1633] text-slate-800 dark:text-white border-violet-200 dark:border-violet-800'} transition-all">
      <div class="font-bold text-xs truncate">${m.tem_design_docx || m.docx_base64 ? '📄 ' : ''}${escapeHtml(m.nome)}</div>
      <div class="text-[10px] opacity-80 truncate">${m.tem_design_docx || m.docx_base64 ? `Design Word (.docx): ${escapeHtml(m.docx_filename || 'anexado')}` : escapeHtml(m.conclusao_padrao || '')}</div>
    </button>
  `).join('');
}

function selecionarModeloEmpresa(idx) {
  const m = modelosEmpresaDraft[idx];
  if (!m) return;
  modeloIndiceEmEdicao = idx;
  document.getElementById('lblEditandoModelo').textContent = `Editando: ${m.nome}`;
  document.getElementById('tpl_nome').value = m.nome || '';
  document.getElementById('tpl_titulo').value = m.titulo || '';
  document.getElementById('tpl_conclusao').value = m.conclusao_padrao || 'Reparado / Aprovado';
  document.getElementById('tpl_diagnostico').value = m.diagnostico || '';
  document.getElementById('tpl_corpo').value = m.corpo || '';
  document.getElementById('tpl_recomendacoes').value = m.recomendacoes || '';
  const badge = document.getElementById('tpl_docx_badge');
  if (badge) {
    if (m.docx_base64 || m.tem_design_docx) {
      badge.textContent = `✅ Design original Word (.docx) preservado: ${m.docx_filename || 'modelo.docx'} — Ao gerar o Word no Criador de Laudos, o layout deste arquivo será mantido 100% igual!`;
      badge.classList.remove('hidden');
    } else {
      badge.classList.add('hidden');
    }
  }
  renderizarListaModelosEmpresa();
}

function novoModeloLaudoEmpresa() {
  modeloIndiceEmEdicao = -1;
  document.getElementById('lblEditandoModelo').textContent = 'Novo Modelo de Laudo';
  document.getElementById('tpl_nome').value = '';
  document.getElementById('tpl_titulo').value = 'LAUDO TÉCNICO PERSONALIZADO';
  document.getElementById('tpl_conclusao').value = 'Reparado / Aprovado';
  document.getElementById('tpl_diagnostico').value = '';
  document.getElementById('tpl_corpo').value = '';
  document.getElementById('tpl_recomendacoes').value = '';
  document.getElementById('tpl_docx_badge')?.classList.add('hidden');
  renderizarListaModelosEmpresa();
}

function inserirVariavelModelo(variavel) {
  const textarea = document.getElementById('tpl_corpo');
  if (!textarea) return;
  const start = textarea.selectionStart ?? textarea.value.length;
  const end = textarea.selectionEnd ?? textarea.value.length;
  textarea.value = textarea.value.slice(0, start) + variavel + textarea.value.slice(end);
  textarea.focus();
}

function aplicarEdicaoModeloNaLista() {
  const nome = document.getElementById('tpl_nome').value.trim();
  const corpo = document.getElementById('tpl_corpo').value.trim();
  if (!nome || !corpo) {
    mostrarToast('Informe o nome e o texto do parecer do modelo.', 'erro');
    return;
  }
  const atual = modeloIndiceEmEdicao >= 0 ? modelosEmpresaDraft[modeloIndiceEmEdicao] : null;
  const obj = {
    id: atual ? atual.id : `tpl_${Date.now()}`,
    nome,
    categoria_sugerida: 'Geral',
    titulo: document.getElementById('tpl_titulo').value.trim() || nome.toUpperCase(),
    conclusao_padrao: document.getElementById('tpl_conclusao').value,
    diagnostico: document.getElementById('tpl_diagnostico').value.trim(),
    corpo,
    recomendacoes: document.getElementById('tpl_recomendacoes').value.trim(),
    docx_base64: atual?.docx_base64 || '',
    docx_filename: atual?.docx_filename || '',
    tem_design_docx: Boolean(atual?.docx_base64 || atual?.tem_design_docx),
  };
  if (modeloIndiceEmEdicao >= 0) {
    modelosEmpresaDraft[modeloIndiceEmEdicao] = obj;
  } else {
    modelosEmpresaDraft.push(obj);
    modeloIndiceEmEdicao = modelosEmpresaDraft.length - 1;
  }
  renderizarListaModelosEmpresa();
  mostrarToast('Modelo atualizado na lista! Clique em "Salvar Personalizações" para gravar.', 'info');
}

function excluirModeloAtualEmpresa() {
  if (modeloIndiceEmEdicao < 0 || modelosEmpresaDraft.length <= 1) {
    mostrarToast('Mantenha pelo menos um modelo cadastrado.', 'erro');
    return;
  }
  modelosEmpresaDraft.splice(modeloIndiceEmEdicao, 1);
  modeloIndiceEmEdicao = 0;
  renderizarListaModelosEmpresa();
  selecionarModeloEmpresa(0);
}

async function importarModeloParaEmpresa(event) {
  const file = event.target.files && event.target.files[0];
  if (!file) return;
  if (file.size > 5 * 1024 * 1024) {
    mostrarToast('O arquivo de modelo excede o limite de 5 MB.', 'erro');
    event.target.value = '';
    return;
  }
  try {
    const b64 = await lerArquivoBase64(file);
    const res = await apiFetch('/laudos/importar-modelo', {
      method: 'POST',
      body: JSON.stringify({ filename: file.name, conteudo_base64: b64 })
    });
    if (!res.ok) {
      mostrarToast(await extrairErroResposta(res, 'Erro ao importar modelo.'), 'erro');
      return;
    }
    const data = await res.json();
    modelosEmpresaDraft.push(data.modelo);
    selecionarModeloEmpresa(modelosEmpresaDraft.length - 1);
    mostrarToast(`Modelo "${data.modelo.nome}" adicionado! Clique em Salvar Personalizações.`, 'sucesso');
  } catch (e) {
    mostrarToast('Falha ao importar arquivo de modelo.', 'erro');
  } finally {
    event.target.value = '';
  }
}

async function salvarBrandingEModelos() {
  const checklist = document.getElementById('brand_checklist').value
    .split(/\r?\n/)
    .map(s => s.trim())
    .filter(Boolean);

  const payload = {
    cnpj: document.getElementById('brand_cnpj').value.trim(),
    telefone: document.getElementById('brand_tel').value.trim(),
    endereco: document.getElementById('brand_end').value.trim(),
    rodape_documento: document.getElementById('brand_rodape').value.trim(),
    cor_documento: document.getElementById('brand_cor').value,
    logo_base64: logoEmpresaBase64Draft,
    checklist_padrao: checklist,
    modelos_laudo: modelosEmpresaDraft,
  };

  const res = await apiFetch('/empresa/modelos', {
    method: 'PUT',
    body: JSON.stringify(payload)
  });
  if (!res.ok) {
    mostrarToast(await extrairErroResposta(res, 'Erro ao salvar personalizações.'), 'erro');
    return;
  }
  const data = await res.json();
  sessaoAtual.tenant = data.tenant;
  carregarBrandingEModelosNaUI();
  if (typeof popularSelectModelosCriador === 'function') popularSelectModelosCriador();
  if (typeof atualizarPreviewCriadorLaudo === 'function') atualizarPreviewCriadorLaudo();
  mostrarToast('Timbre, checklist e modelos de laudo salvos!', 'sucesso');
}

// ==============================================================================
// SETOR ESTOQUE & ALMOXARIFADO
// ==============================================================================

let estoqueItensCache = [];

async function carregarEstoqueOps() {
  const res = await apiFetch('/ops/estoque');
  if (!res.ok) return;
  const itens = await res.json();
  estoqueItensCache = Array.isArray(itens) ? itens : [];

  const totalSkus = estoqueItensCache.length;
  const totalUnidades = estoqueItensCache.reduce((acc, i) => acc + Number(i.quantidade || 0), 0);
  const totalBaixo = estoqueItensCache.filter(i => i.estoque_baixo).length;

  document.getElementById('kpiEstSkus').textContent = totalSkus;
  document.getElementById('kpiEstUnidades').textContent = totalUnidades;
  document.getElementById('kpiEstBaixo').textContent = totalBaixo;
  if (temPermissao('ver_custos')) {
    const valorTotal = estoqueItensCache.reduce(
      (acc, i) => acc + (Number(i.custo_unitario || 0) * Number(i.quantidade || 0)),
      0
    );
    document.getElementById('kpiEstValor').textContent = `R$ ${formatarMoedaBR(valorTotal)}`;
  }

  renderizarTabelaEstoqueOps();
}

function renderizarTabelaEstoqueOps() {
  const tbody = document.getElementById('tabelaEstoqueOps');
  if (!tbody) return;

  const busca = (document.getElementById('est_busca')?.value || '').trim().toLowerCase();
  const almoxFiltro = document.getElementById('est_filtro_almox')?.value || '';
  const soCriticos = Boolean(document.getElementById('est_filtro_baixo')?.checked);

  const filtrados = estoqueItensCache.filter(i => {
    if (almoxFiltro && (i.almoxarifado || 'Almoxarifado Central') !== almoxFiltro) return false;
    if (soCriticos && !i.estoque_baixo) return false;
    if (busca) {
      return [i.nome, i.codigo, i.categoria, i.localizacao, i.almoxarifado]
        .some(v => String(v || '').toLowerCase().includes(busca));
    }
    return true;
  });

  const podeCustos = temPermissao('ver_custos');
  const podeMov = temPermissao('movimentar_estoque');
  const podeGerir = temPermissao('gerenciar_estoque');

  if (filtrados.length === 0) {
    tbody.innerHTML = '<tr><td colspan="5" class="p-4 text-center text-slate-400">Nenhum item encontrado neste filtro.</td></tr>';
    return;
  }

  tbody.innerHTML = filtrados.map(i => `
    <tr>
      <td class="p-3">
        <div class="font-bold text-slate-900 dark:text-white">${escapeHtml(i.nome)}</div>
        <div class="text-[10px] text-slate-400">${escapeHtml(i.codigo || 'Sem SKU')} • ${escapeHtml(i.categoria || '')} • ${escapeHtml(i.localizacao || '')}</div>
      </td>
      <td class="p-3 text-slate-600 dark:text-violet-200">${escapeHtml(i.almoxarifado || 'Almoxarifado Central')}</td>
      <td class="p-3">
        <span class="px-2 py-0.5 rounded-full text-[11px] font-bold ${i.estoque_baixo ? 'bg-rose-100 text-rose-700' : 'bg-emerald-50 text-emerald-700'}">${i.quantidade} ${escapeHtml(i.unidade || 'un')} ${i.estoque_baixo ? '⚠️' : ''}</span>
      </td>
      ${podeCustos ? `<td class="p-3 text-slate-700 dark:text-violet-100">R$ ${formatarMoedaBR(i.custo_unitario)}</td>` : ''}
      <td class="p-3 text-right space-x-1">
        ${podeMov ? `<button onclick="abrirModalMovEstoque('${escapeHtml(i.id)}', '${escapeHtml(i.nome)}')" class="px-2.5 py-1 rounded-lg bg-violet-600 text-white text-[11px] font-bold">Movimentar</button>` : ''}
        <button onclick="abrirHistoricoEstoqueItem('${escapeHtml(i.id)}')" class="px-2 py-1 rounded-lg bg-violet-50 dark:bg-[#1C1633] text-violet-700 dark:text-violet-200 border border-violet-200 dark:border-violet-800 text-[11px] font-semibold" title="Histórico">📜</button>
        ${podeGerir ? `<button onclick="excluirItemEstoqueOps('${escapeHtml(i.id)}')" class="px-2 py-1 rounded-lg bg-rose-50 text-rose-600 text-[11px] font-semibold">✕</button>` : ''}
      </td>
    </tr>
  `).join('');
}

function abrirHistoricoEstoqueItem(itemId) {
  const item = estoqueItensCache.find(i => i.id === itemId);
  if (!item) return;
  document.getElementById('histEstTitulo').textContent = `📜 Histórico: ${item.nome}`;
  const hist = Array.isArray(item.historico_movimentacoes) ? item.historico_movimentacoes : [];
  const box = document.getElementById('histEstLista');
  if (hist.length === 0) {
    box.innerHTML = '<p class="text-slate-400">Nenhuma movimentação registrada.</p>';
  } else {
    box.innerHTML = hist.map(m => `
      <div class="p-2.5 rounded-xl bg-violet-50/70 dark:bg-[#1C1633] border border-violet-200/80 dark:border-violet-800/70 flex justify-between gap-2">
        <div>
          <div class="font-bold text-slate-900 dark:text-white">${m.tipo === 'saida' ? '📤 Saída' : (m.tipo === 'entrada' ? '📥 Entrada' : '🆕 Cadastro')} (${m.quantidade} ${escapeHtml(item.unidade || 'un')})</div>
          <div class="text-[11px] text-slate-500 dark:text-violet-300/80">Saldo: ${m.saldo_anterior} → <b>${m.saldo_atual}</b> ${m.os_id ? `• O.S. #${m.os_id}` : ''}</div>
          ${m.observacao ? `<div class="text-[11px] text-slate-600 dark:text-violet-200">${escapeHtml(m.observacao)}</div>` : ''}
        </div>
        <div class="text-right text-[10px] text-slate-400 shrink-0">
          <div>${formatarDataBR(m.data_hora)}</div>
          <div>${escapeHtml(m.autor || '')}</div>
        </div>
      </div>
    `).join('');
  }
  document.getElementById('modalHistoricoEstoque').classList.remove('hidden');
}

function fecharModalHistoricoEstoque() {
  const el = document.getElementById('modalHistoricoEstoque');
  if (el) el.classList.add('hidden');
}

async function criarItemEstoque(event) {
  event.preventDefault();
  const payload = {
    nome: document.getElementById('est_nome').value.trim(),
    codigo: document.getElementById('est_codigo').value.trim(),
    categoria: document.getElementById('est_categoria').value,
    almoxarifado: document.getElementById('est_almoxarifado').value,
    unidade: document.getElementById('est_unidade').value,
    quantidade: Number.parseInt(document.getElementById('est_qtd').value, 10) || 0,
    estoque_minimo: Number.parseInt(document.getElementById('est_min').value, 10) || 0,
    custo_unitario: temPermissao('ver_custos') ? (Number.parseFloat(document.getElementById('est_custo').value) || 0) : 0,
    localizacao: document.getElementById('est_loc').value.trim(),
  };
  const res = await apiFetch('/ops/estoque', { method: 'POST', body: JSON.stringify(payload) });
  if (!res.ok) {
    mostrarToast(await extrairErroResposta(res, 'Erro ao cadastrar item.'), 'erro');
    return;
  }
  event.target.reset();
  mostrarToast('Item salvo no estoque!', 'sucesso');
  await carregarEstoqueOps();
}

function abrirModalMovEstoque(itemId, nome) {
  movEstoqueItemId = itemId;
  document.getElementById('movEstTitulo').textContent = `📦 Movimentar: ${nome}`;
  document.getElementById('mov_est_os_id').value = registroSelecionadoId || '';
  document.getElementById('mov_est_obs').value = '';
  document.getElementById('modalMovEstoque').classList.remove('hidden');
}

function fecharModalMovEstoque() {
  movEstoqueItemId = null;
  document.getElementById('modalMovEstoque').classList.add('hidden');
}

async function confirmarMovimentoEstoque() {
  if (!movEstoqueItemId) return;
  const osIdRaw = document.getElementById('mov_est_os_id').value.trim();
  const payload = {
    tipo: document.getElementById('mov_est_tipo').value,
    quantidade: Number.parseInt(document.getElementById('mov_est_qtd').value, 10) || 1,
    os_id: osIdRaw ? Number.parseInt(osIdRaw, 10) : null,
    observacao: document.getElementById('mov_est_obs').value.trim(),
  };
  const res = await apiFetch(`/ops/estoque/${encodeURIComponent(movEstoqueItemId)}/movimentar`, {
    method: 'POST',
    body: JSON.stringify(payload)
  });
  if (!res.ok) {
    mostrarToast(await extrairErroResposta(res, 'Erro ao movimentar estoque.'), 'erro');
    return;
  }
  fecharModalMovEstoque();
  mostrarToast('Movimentação registrada!', 'sucesso');
  await carregarEstoqueOps();
}

async function excluirItemEstoqueOps(itemId) {
  const res = await apiFetch(`/ops/estoque/${encodeURIComponent(itemId)}`, { method: 'DELETE' });
  if (res.ok) await carregarEstoqueOps();
}

// ==============================================================================
// VISÃO DEDICADA: CRIADOR DE LAUDOS, PARECERES & MODELOS (SEPARADO)
// ==============================================================================

let laudosEmitidosCache = [];
let laudosEquipamentosCache = [];
let criadorLaudoEditando = null;
let laudoViewTabAtual = 'criar';
let criadorDocxBase64 = '';
let criadorDocxFilename = '';
let criadorModeloIdAtivo = '';

function atualizarBannerDesignDocxCriador() {
  const banner = document.getElementById('cl_docx_design_banner');
  const lbl = document.getElementById('cl_docx_design_nome');
  if (!banner) return;
  if (criadorDocxBase64 || criadorDocxFilename) {
    if (lbl) lbl.textContent = criadorDocxFilename || 'modelo_word.docx';
    banner.classList.remove('hidden');
  } else {
    banner.classList.add('hidden');
  }
}

function limparDesignDocxCriador() {
  criadorDocxBase64 = '';
  criadorDocxFilename = '';
  criadorModeloIdAtivo = '';
  atualizarBannerDesignDocxCriador();
}

function alternarLaudoViewTab(tab) {
  laudoViewTabAtual = tab;
  ['criar', 'emitidos', 'modelos'].forEach(key => {
    const sec = document.getElementById(`laudoTab_${key}`);
    if (sec) sec.classList.toggle('hidden', key !== tab);
    const btn = document.getElementById(`btnLaudoTab_${key}`);
    if (btn) {
      btn.className = key === tab
        ? 'px-3 py-1.5 text-xs font-bold rounded-lg bg-violet-600 text-white'
        : 'px-3 py-1.5 text-xs font-semibold rounded-lg text-violet-800 dark:text-violet-200';
    }
  });
  if (tab === 'modelos' && typeof carregarBrandingEModelosNaUI === 'function') {
    carregarBrandingEModelosNaUI();
  }
}

async function carregarPainelLaudos() {
  const podeCriar = temPermissao('acessar_criador_laudos');
  const podeEmitidos = podeCriar || temPermissao('gerenciar_laudos');
  const podeModelos = temPermissao('gerenciar_modelos') || temPermissao('gerenciar_equipe');

  let tabInicial = laudoViewTabAtual;
  if (tabInicial === 'criar' && !podeCriar) {
    tabInicial = podeEmitidos ? 'emitidos' : 'modelos';
  } else if (tabInicial === 'emitidos' && !podeEmitidos) {
    tabInicial = podeCriar ? 'criar' : 'modelos';
  } else if (tabInicial === 'modelos' && !podeModelos) {
    tabInicial = podeCriar ? 'criar' : 'emitidos';
  }
  alternarLaudoViewTab(tabInicial);

  const modelosCount = (sessaoAtual?.tenant?.modelos_laudo || presetsTiCache?.modelos_laudo_padrao || []).length;
  const setTxt = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  setTxt('kpiLaudoModelos', modelosCount);

  if (podeModelos && typeof carregarBrandingEModelosNaUI === 'function') {
    carregarBrandingEModelosNaUI();
  }
  popularSelectModelosCriador();

  if (podeEmitidos) {
    const res = await apiFetch('/laudos');
    if (res.ok) {
      const data = await res.json();
      const r = data.resumo || {};
      laudosEmitidosCache = Array.isArray(data.laudos) ? data.laudos : [];
      laudosEquipamentosCache = Array.isArray(data.equipamentos) ? data.equipamentos : [];
      setTxt('kpiLaudoTotal', r.total_laudos ?? laudosEmitidosCache.length);
      setTxt('kpiLaudoAprovados', r.aprovados ?? 0);
      setTxt('kpiLaudoVisiveis', r.visiveis_cliente ?? 0);
      popularSelectEquipamentosCriador();
      renderizarListaLaudosEmitidos();
    }
  }

  const dataInp = document.getElementById('cl_data');
  if (dataInp && !dataInp.value) dataInp.value = obterDataHojeISO();
  const tecInp = document.getElementById('cl_tecnico');
  if (tecInp && !tecInp.value) tecInp.value = 'Vitor Figueiró da Silva';
  const regInp = document.getElementById('cl_reg_prof');
  if (regInp && !regInp.value) regInp.value = 'T.I';
  if (!criadorLaudoEditando) {
    preencherDadosOsNoCriadorLaudo();
  }
  atualizarPreviewCriadorLaudo();
}

function preencherDadosOsNoCriadorLaudo() {
  const reg = _obterEquipamentoSelecionadoCriador();
  if (!reg) {
    atualizarPreviewCriadorLaudo();
    return;
  }
  const elCli = document.getElementById('cl_cliente');
  const elEq = document.getElementById('cl_equipamento');
  const elTomb = document.getElementById('cl_tombamento');
  const elDiag = document.getElementById('cl_diagnostico');
  if (elCli) elCli.value = reg.origem || '';
  if (elEq) elEq.value = reg.equipamento || '';
  if (elTomb) elTomb.value = _limparTombamentoPadrao(reg);
  if (elDiag && !elDiag.value.trim() && reg.problema) elDiag.value = reg.problema;
  atualizarPreviewCriadorLaudo();
}

function popularSelectEquipamentosCriador() {
  const select = document.getElementById('cl_os_select');
  if (!select) return;
  const termo = (document.getElementById('cl_os_busca')?.value || '').trim().toLowerCase();
  const prevVal = select.value;

  const filtrados = laudosEquipamentosCache.filter(e => {
    if (!termo) return true;
    return [e.id, e.equipamento, e.tombamento, e.serial_mac, e.origem, e.localizacao]
      .some(v => String(v || '').toLowerCase().includes(termo));
  });

  if (filtrados.length === 0) {
    select.innerHTML = '<option value="">Nenhuma O.S. encontrada</option>';
  } else {
    select.innerHTML = filtrados.map(e => {
      const label = `#${e.id} • ${e.equipamento} (${e.tombamento || 'S/N'}) — ${e.origem || 'Interno'}`;
      return `<option value="${e.id}">${escapeHtml(label)}</option>`;
    }).join('');
    if (prevVal && filtrados.some(e => String(e.id) === String(prevVal))) {
      select.value = prevVal;
    }
  }
  atualizarPreviewCriadorLaudo();
}

function popularSelectModelosCriador() {
  const select = document.getElementById('cl_modelo_select');
  if (!select) return;
  const modelos = sessaoAtual?.tenant?.modelos_laudo || presetsTiCache?.modelos_laudo_padrao || [];
  select.innerHTML = modelos.map(m => `<option value="${escapeHtml(m.id)}">${escapeHtml(m.nome)}</option>`).join('');
  const elCount = document.getElementById('kpiLaudoModelos');
  if (elCount) elCount.textContent = modelos.length;
}

function _obterEquipamentoSelecionadoCriador() {
  const idSel = Number(document.getElementById('cl_os_select')?.value || 0);
  return laudosEquipamentosCache.find(e => Number(e.id) === idSel) || null;
}

function _aplicarModeloObjetoNoCriador(modelo) {
  if (!modelo) return;
  const reg = _obterEquipamentoSelecionadoCriador() || {};
  document.getElementById('cl_titulo').value = substituirVariaveisLaudoNoCliente(modelo.titulo || 'LAUDO TÉCNICO', reg);
  document.getElementById('cl_conclusao').value = modelo.conclusao_padrao || 'Reparado / Aprovado';
  document.getElementById('cl_diagnostico').value = substituirVariaveisLaudoNoCliente(modelo.diagnostico || '', reg);
  document.getElementById('cl_parecer').value = substituirVariaveisLaudoNoCliente(modelo.corpo || '', reg);
  document.getElementById('cl_recomendacoes').value = substituirVariaveisLaudoNoCliente(modelo.recomendacoes || '', reg);
  atualizarPreviewCriadorLaudo();
}

function aplicarModeloNoCriadorLaudo() {
  const idSel = document.getElementById('cl_modelo_select')?.value;
  const modelos = sessaoAtual?.tenant?.modelos_laudo || presetsTiCache?.modelos_laudo_padrao || [];
  const modelo = modelos.find(m => m.id === idSel) || modelos[0];
  if (!modelo) {
    mostrarToast('Nenhum modelo disponível.', 'erro');
    return;
  }
  criadorModeloIdAtivo = modelo.id || '';
  if (modelo.docx_base64 || modelo.tem_design_docx) {
    criadorDocxBase64 = modelo.docx_base64 || '';
    criadorDocxFilename = modelo.docx_filename || `${modelo.nome || 'modelo'}.docx`;
    atualizarBannerDesignDocxCriador();
    const reg = _obterEquipamentoSelecionadoCriador() || {};
    if (!document.getElementById('cl_diagnostico').value.trim() && reg.problema) {
      document.getElementById('cl_diagnostico').value = reg.problema;
    }
    if (!document.getElementById('cl_parecer').value.trim() && reg.resolucao) {
      document.getElementById('cl_parecer').value = reg.resolucao;
    }
    atualizarPreviewCriadorLaudo();
    mostrarToast(`Design Word "${criadorDocxFilename}" ativo! Preencha os 3 campos e clique em Baixar Word (.docx).`, 'sucesso');
    return;
  }
  criadorDocxBase64 = '';
  criadorDocxFilename = '';
  atualizarBannerDesignDocxCriador();
  _aplicarModeloObjetoNoCriador(modelo);
  mostrarToast(`Modelo "${modelo.nome}" aplicado!`, 'sucesso');
}

async function importarModeloNoCriadorLaudo(event) {
  const file = event.target.files && event.target.files[0];
  if (!file) return;
  if (file.size > 5 * 1024 * 1024) {
    mostrarToast('O arquivo de modelo excede o limite de 5 MB.', 'erro');
    event.target.value = '';
    return;
  }
  try {
    const b64 = await lerArquivoBase64(file);
    const isDocx = file.name.toLowerCase().endsWith('.docx');
    const res = await apiFetch('/laudos/importar-modelo', {
      method: 'POST',
      body: JSON.stringify({ filename: file.name, conteudo_base64: b64 })
    });
    if (!res.ok) {
      mostrarToast(await extrairErroResposta(res, 'Erro ao ler modelo enviado.'), 'erro');
      return;
    }
    const data = await res.json();
    if (isDocx) {
      criadorDocxBase64 = b64;
      criadorDocxFilename = file.name;
      criadorModeloIdAtivo = '';
      atualizarBannerDesignDocxCriador();
      const reg = _obterEquipamentoSelecionadoCriador() || {};
      if (!document.getElementById('cl_diagnostico').value.trim() && reg.problema) {
        document.getElementById('cl_diagnostico').value = reg.problema;
      }
      if (!document.getElementById('cl_parecer').value.trim() && reg.resolucao) {
        document.getElementById('cl_parecer').value = reg.resolucao;
      }
      atualizarPreviewCriadorLaudo();
      mostrarToast(`Design Word "${file.name}" carregado! Preencha os 3 campos e clique em "Baixar Word (.docx)".`, 'sucesso');
    } else {
      criadorDocxBase64 = '';
      criadorDocxFilename = '';
      atualizarBannerDesignDocxCriador();
      _aplicarModeloObjetoNoCriador(data.modelo);
      mostrarToast(`Modelo "${data.modelo.nome}" carregado!`, 'sucesso');
    }
  } catch (e) {
    mostrarToast('Falha ao importar arquivo de modelo.', 'erro');
  } finally {
    event.target.value = '';
  }
}

async function baixarLaudoDocxCriador() {
  const reg = _obterEquipamentoSelecionadoCriador();
  const idSel = criadorModeloIdAtivo || document.getElementById('cl_modelo_select')?.value || '';
  const payload = {
    registro_id: reg ? Number(reg.id) : null,
    modelo_id: idSel,
    docx_base64: criadorDocxBase64 || '',
    docx_filename: criadorDocxFilename || (reg ? `Laudo_OS_${reg.id}.docx` : 'Laudo_Preenchido.docx'),
    para: reg?.origem || '',
    assunto: document.getElementById('cl_titulo')?.value.trim() || 'Parecer Técnico',
    diagnostico: document.getElementById('cl_diagnostico')?.value.trim() || '',
    parecer: document.getElementById('cl_parecer')?.value.trim() || '',
    recomendacoes: document.getElementById('cl_recomendacoes')?.value.trim() || '',
    conclusao: document.getElementById('cl_conclusao')?.value || '',
    tecnico_responsavel: document.getElementById('cl_tecnico')?.value.trim() || '',
    numero_laudo: criadorLaudoEditando?.numero || (reg ? `OS${reg.id}` : '01'),
  };
  try {
    const res = await apiFetch('/laudos/gerar-docx', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      mostrarToast(await extrairErroResposta(res, 'Anexe seu arquivo .docx primeiro para baixar com o design original.'), 'erro');
      return;
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = payload.docx_filename.toLowerCase().endsWith('.docx') ? payload.docx_filename : `${payload.docx_filename}.docx`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 15000);
    mostrarToast('Documento Word (.docx) gerado mantendo 100% do design original!', 'sucesso');
  } catch (e) {
    mostrarToast('Falha ao gerar documento Word (.docx).', 'erro');
  }
}

async function baixarLaudoDocxSalvo(registroId, laudoId, numero) {
  try {
    const res = await apiFetch(`/equipamentos/${registroId}/laudos/${encodeURIComponent(laudoId)}/docx`);
    if (!res.ok) {
      mostrarToast(await extrairErroResposta(res, 'Nenhum design Word (.docx) vinculado a este laudo.'), 'erro');
      return;
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `Laudo_${numero || laudoId}.docx`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 15000);
    mostrarToast('Word (.docx) baixado com o design original!', 'sucesso');
  } catch (e) {
    mostrarToast('Falha ao baixar Word (.docx).', 'erro');
  }
}

function _formatarDataExtensoBR(isoStr) {
  const meses = ['', 'Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho', 'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro'];
  const raw = String(isoStr || obterDataHojeISO()).split('T')[0];
  const p = raw.split('-');
  if (p.length === 3) {
    const ano = p[0];
    const mes = Number(p[1]) || 1;
    const dia = String(Number(p[2]) || 1).padStart(2, '0');
    return {
      hifen: `${dia}-${String(mes).padStart(2, '0')}-${ano}`,
      extenso: `${dia} de ${meses[mes] || 'Janeiro'} de ${ano}`,
    };
  }
  return { hifen: raw, extenso: raw };
}

function atualizarPreviewCriadorLaudo() {
  const box = document.getElementById('cl_preview_box');
  if (!box) return;

  const tenant = sessaoAtual?.tenant || {};
  const reg = _obterEquipamentoSelecionadoCriador();
  const dtInfo = _formatarDataExtensoBR(document.getElementById('cl_data')?.value || obterDataHojeISO());
  const cliente = (document.getElementById('cl_cliente')?.value || reg?.origem || '-').trim();
  const assunto = (document.getElementById('cl_titulo')?.value || 'Parecer Técnico').trim();
  const equip = (document.getElementById('cl_equipamento')?.value || reg?.equipamento || '-').trim();
  const tomb = (document.getElementById('cl_tombamento')?.value || '').trim();
  const equipLinha = tomb ? `${equip} — Tombamento: ${tomb}` : equip;
  const probRel = (document.getElementById('cl_diagnostico')?.value || '').trim();
  const probId = (document.getElementById('cl_parecer')?.value || '').trim();
  const concRec = (document.getElementById('cl_recomendacoes')?.value || '').trim();
  const tec = (document.getElementById('cl_tecnico')?.value || 'Vitor Figueiró da Silva').trim();
  const cargo = (document.getElementById('cl_reg_prof')?.value || 'T.I').trim();
  const emailTec = sessaoAtual?.user?.email || 'vitor.figueiro@centralinformatica.com.br';
  const foneRaw = (tenant.telefone || '').trim();
  const foneLinha = foneRaw ? (foneRaw.toLowerCase().startsWith('fone') ? foneRaw : `Fone: ${foneRaw}`) : 'Fone: 69 3481-2185 | Cel.: 69 9926-2829';

  box.innerHTML = `
    <div class="-m-5 rounded-xl overflow-hidden bg-white text-slate-900 flex flex-col justify-between min-h-[540px] relative">
      <!-- Cabeçalho oficial idêntico ao DOCX -->
      <div class="relative w-full overflow-hidden" style="min-height: 92px; background: url('/static/laudo_assets/header_bg.png?v=1') top left / 100% auto no-repeat;">
        <div class="px-4 pt-3 pb-4 pr-16 text-white">
          <div class="text-sm font-extrabold tracking-wide leading-tight">CENTRAL INFORMÁTICA</div>
          <div class="text-[9.5px] font-medium opacity-95 leading-tight mt-0.5">H &amp; F SOLUÇÕES TECNOLÓGICAS LTDA EPP</div>
        </div>
        <img src="/static/laudo_assets/logo_central.png?v=1" alt="Central Informática" class="absolute top-2 right-3 h-16 w-auto object-contain">
      </div>

      <!-- Corpo do documento -->
      <div class="px-6 py-4 space-y-2.5 text-[11px] font-sans leading-relaxed flex-1">
        <div>
          <div class="text-xs font-extrabold">Laudo ${escapeHtml(dtInfo.hifen)} 01</div>
          <div class="text-[11px] text-slate-700">Espigão do Oeste, ${escapeHtml(dtInfo.extenso)}.</div>
        </div>

        <div class="space-y-0.5 pt-1">
          <div><b>Para:</b> ${escapeHtml(cliente)}</div>
          <div><b>Assunto:</b> ${escapeHtml(assunto)}</div>
          <div><b>Equipamento:</b> ${escapeHtml(equipLinha)}</div>
        </div>

        <div class="space-y-2 pt-1">
          <div>
            <div class="font-bold">Problema Relatado:</div>
            <div class="whitespace-pre-line text-slate-700 mt-0.5">${escapeHtml(probRel || '...')}</div>
          </div>
          <div>
            <div class="font-bold">Problema Identificado:</div>
            <div class="whitespace-pre-line text-slate-700 mt-0.5">${escapeHtml(probId || '...')}</div>
          </div>
          <div>
            <div class="font-bold">Conclusão e Recomendação:</div>
            <div class="whitespace-pre-line text-slate-700 mt-0.5">${escapeHtml(concRec || '...')}</div>
          </div>
        </div>

        <div class="pt-3 space-y-1.5 text-slate-800">
          <div>Estamos à disposição para sanar qualquer dúvida.</div>
          <div>Atenciosamente,</div>
          <div class="pt-4 mx-auto max-w-[230px] border-t border-slate-700 text-center text-[10px] leading-snug">
            <div class="font-bold">${escapeHtml(tec)}</div>
            <div>${escapeHtml(cargo)}</div>
            <div>E-mail: ${escapeHtml(emailTec)}</div>
            <div>Fone: 69 3481-2185 | Cel.: 69 9926-2829</div>
          </div>
        </div>
      </div>

      <!-- Rodapé oficial idêntico ao DOCX -->
      <div class="w-full px-4 py-2 text-right text-white text-[9px] leading-tight" style="min-height: 34px; background: url('/static/laudo_assets/footer_bg.png?v=1') bottom left / 100% 100% no-repeat;">
        <div>Rua: Paraná, 2509 - Centro – Espigão do Oeste– RO</div>
        <div>Fone: (69) 3481-2185 ou 3481-2828</div>
      </div>
    </div>
  `;
}

function limparCriadorLaudo() {
  criadorLaudoEditando = null;
  document.getElementById('cl_edit_badge')?.classList.add('hidden');
  const elData = document.getElementById('cl_data');
  if (elData) elData.value = obterDataHojeISO();
  document.getElementById('cl_titulo').value = 'Parecer Técnico';
  document.getElementById('cl_conclusao').value = 'Reparado / Aprovado';
  document.getElementById('cl_diagnostico').value = '';
  document.getElementById('cl_parecer').value = '';
  document.getElementById('cl_recomendacoes').value = '';
  document.getElementById('cl_tecnico').value = 'Vitor Figueiró da Silva';
  document.getElementById('cl_reg_prof').value = 'T.I';
  document.getElementById('cl_inc_checklist').checked = false;
  document.getElementById('cl_visivel_cliente').checked = true;
  preencherDadosOsNoCriadorLaudo();
}

async function salvarLaudoCriador(abrirPdfDepois = false) {
  const regId = Number(document.getElementById('cl_os_select')?.value || 0);
  if (!regId) {
    mostrarToast('Selecione um equipamento / O.S. para vincular o laudo.', 'erro');
    return;
  }
  const payload = {
    data_laudo: document.getElementById('cl_data')?.value || obterDataHojeISO(),
    cliente_para: document.getElementById('cl_cliente')?.value.trim() || '',
    equipamento_laudo: document.getElementById('cl_equipamento')?.value.trim() || '',
    tombamento_laudo: document.getElementById('cl_tombamento')?.value.trim() || '',
    titulo: document.getElementById('cl_titulo').value.trim() || 'Parecer Técnico',
    conclusao: document.getElementById('cl_conclusao').value,
    diagnostico: document.getElementById('cl_diagnostico').value.trim(),
    parecer: document.getElementById('cl_parecer').value.trim(),
    recomendacoes: document.getElementById('cl_recomendacoes').value.trim(),
    tecnico_responsavel: document.getElementById('cl_tecnico').value.trim() || 'Vitor Figueiró da Silva',
    registro_profissional: document.getElementById('cl_reg_prof').value.trim() || 'T.I',
    email_tecnico: sessaoAtual?.user?.email || 'vitor.figueiro@centralinformatica.com.br',
    incluir_checklist: document.getElementById('cl_inc_checklist').checked,
    visivel_cliente: document.getElementById('cl_visivel_cliente').checked,
  };
  if (!payload.parecer) {
    mostrarToast('Preencha o campo "2. Problema Identificado".', 'erro');
    return;
  }

  const editandoMesmaOs = criadorLaudoEditando && Number(criadorLaudoEditando.registroId) === regId;
  const path = editandoMesmaOs
    ? `/equipamentos/${regId}/laudos/${encodeURIComponent(criadorLaudoEditando.laudoId)}`
    : `/equipamentos/${regId}/laudos`;

  const res = await apiFetch(path, {
    method: editandoMesmaOs ? 'PUT' : 'POST',
    body: JSON.stringify(payload)
  });
  if (!res.ok) {
    mostrarToast(await extrairErroResposta(res, 'Não foi possível salvar o laudo.'), 'erro');
    return;
  }
  const data = await res.json();
  const laudoId = data.laudo?.id;
  mostrarToast(data.mensagem || 'Laudo salvo e anexado à O.S.!', 'sucesso');
  limparCriadorLaudo();
  await carregarPainelLaudos();

  if (abrirPdfDepois && laudoId) {
    abrirLaudoPdfDireto(regId, laudoId);
  }
}

function renderizarListaLaudosEmitidos() {
  const box = document.getElementById('listaLaudosEmitidos');
  if (!box) return;
  const termo = (document.getElementById('cl_busca_emitidos')?.value || '').trim().toLowerCase();
  const podeCriar = temPermissao('acessar_criador_laudos');

  const filtrados = laudosEmitidosCache.filter(l => {
    if (!termo) return true;
    return [l.numero, l.titulo, l.conclusao, l.equipamento, l.tombamento, l.origem, l.tecnico_responsavel]
      .some(v => String(v || '').toLowerCase().includes(termo));
  });

  if (filtrados.length === 0) {
    box.innerHTML = '<p class="text-slate-400 md:col-span-2">Nenhum laudo técnico emitido encontrado.</p>';
    return;
  }

  box.innerHTML = filtrados.map(l => `
    <div class="p-4 rounded-xl bg-violet-50/70 dark:bg-[#1C1633] border border-violet-200/80 dark:border-violet-800/70 space-y-2">
      <div class="flex flex-wrap justify-between items-start gap-2">
        <div>
          <span class="text-[10px] font-extrabold text-violet-600 dark:text-violet-400 uppercase">${escapeHtml(l.numero)} • O.S. #${l.registro_id} • ${formatarDataBR(l.emitido_em)}</span>
          <h4 class="text-xs font-extrabold text-slate-900 dark:text-white">${escapeHtml(l.titulo)} — ${escapeHtml(l.equipamento)}</h4>
          <p class="text-[11px] text-slate-500 dark:text-violet-300/80">${escapeHtml(l.conclusao)} • Técnico: ${escapeHtml(l.tecnico_responsavel || '-')}</p>
        </div>
        <span class="px-2 py-0.5 rounded-full text-[10px] font-bold ${l.visivel_cliente ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-200 text-slate-700'}">
          ${l.visivel_cliente ? 'Público' : 'Interno'}
        </span>
      </div>
      <p class="text-[11px] text-slate-700 dark:text-violet-100 line-clamp-2 whitespace-pre-line">${escapeHtml(l.parecer)}</p>
      <div class="flex flex-wrap gap-1.5 pt-1">
        <button type="button" onclick="abrirLaudoPdfDireto(${l.registro_id}, '${escapeHtml(l.id)}')" class="px-2.5 py-1 rounded-lg bg-violet-600 hover:bg-violet-700 text-white text-[11px] font-bold">🖨️ Imprimir / PDF</button>
        <button type="button" onclick="baixarLaudoDocxSalvo(${l.registro_id}, '${escapeHtml(l.id)}', '${escapeHtml(l.numero || '')}')" class="px-2.5 py-1 rounded-lg bg-sky-600 hover:bg-sky-700 text-white text-[11px] font-bold">📄 Word (.docx)</button>
        ${podeCriar ? `<button type="button" onclick="editarLaudoNoCriador(${l.registro_id}, '${escapeHtml(l.id)}')" class="px-2.5 py-1 rounded-lg bg-white dark:bg-[#141024] border border-violet-200 dark:border-violet-700 text-violet-800 dark:text-violet-200 text-[11px] font-semibold">✏️ Editar no Criador</button>` : ''}
        <button type="button" onclick="excluirLaudoDireto(${l.registro_id}, '${escapeHtml(l.id)}')" class="px-2.5 py-1 rounded-lg bg-rose-50 dark:bg-rose-950/50 text-rose-600 dark:text-rose-300 border border-rose-200 dark:border-rose-800 text-[11px] font-semibold">🗑️ Excluir</button>
      </div>
    </div>
  `).join('');
}

function editarLaudoNoCriador(registroId, laudoId) {
  const item = laudosEmitidosCache.find(l => Number(l.registro_id) === Number(registroId) && l.id === laudoId);
  if (!item) return;
  criadorLaudoEditando = { registroId: Number(registroId), laudoId: item.id, numero: item.numero };
  const badge = document.getElementById('cl_edit_badge');
  if (badge) {
    badge.textContent = `Editando ${item.numero}`;
    badge.classList.remove('hidden');
  }
  document.getElementById('cl_os_busca').value = '';
  popularSelectEquipamentosCriador();
  document.getElementById('cl_os_select').value = String(registroId);
  const elData = document.getElementById('cl_data');
  if (elData) elData.value = item.data_laudo || converterDataParaInput(item.emitido_em) || obterDataHojeISO();
  const elCli = document.getElementById('cl_cliente');
  if (elCli) elCli.value = item.cliente_para || item.origem || '';
  const elEq = document.getElementById('cl_equipamento');
  if (elEq) elEq.value = item.equipamento_laudo || item.equipamento || '';
  const elTomb = document.getElementById('cl_tombamento');
  if (elTomb) elTomb.value = item.tombamento_laudo || _limparTombamentoPadrao(item);
  document.getElementById('cl_titulo').value = item.titulo || 'Parecer Técnico';
  document.getElementById('cl_conclusao').value = item.conclusao || 'Reparado / Aprovado';
  document.getElementById('cl_diagnostico').value = item.diagnostico || '';
  document.getElementById('cl_parecer').value = item.parecer || '';
  document.getElementById('cl_recomendacoes').value = item.recomendacoes || '';
  document.getElementById('cl_tecnico').value = item.tecnico_responsavel || 'Vitor Figueiró da Silva';
  document.getElementById('cl_reg_prof').value = item.registro_profissional || 'T.I';
  document.getElementById('cl_inc_checklist').checked = Boolean(item.incluir_checklist);
  document.getElementById('cl_visivel_cliente').checked = item.visivel_cliente !== false;
  alternarLaudoViewTab('criar');
  atualizarPreviewCriadorLaudo();
}

function abrirLaudoPdfDireto(registroId, laudoId) {
  abrirPdfAutenticado(
    `/equipamentos/${registroId}/laudos/${encodeURIComponent(laudoId)}/pdf`,
    'Erro ao gerar PDF do laudo técnico.'
  );
}

async function excluirLaudoDireto(registroId, laudoId) {
  if (!window.confirm('Remover este laudo técnico anexado ao equipamento?')) return;
  const res = await apiFetch(`/equipamentos/${registroId}/laudos/${encodeURIComponent(laudoId)}`, {
    method: 'DELETE'
  });
  if (!res.ok) {
    mostrarToast(await extrairErroResposta(res, 'Erro ao excluir laudo.'), 'erro');
    return;
  }
  mostrarToast('Laudo removido.', 'info');
  await carregarPainelLaudos();
}
