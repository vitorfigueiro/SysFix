// ==============================================================================
// SETOR LOJA DE TI (PDV, ORÇAMENTOS, CAIXA, GARANTIAS/RMA & CLIENTES B2B/PF)
// ==============================================================================

let lojaTabAtual = 'pdv';
let lojaCatalogoCache = [];
let lojaClientesCache = [];
let lojaVendasCache = [];
let lojaOrcamentosCache = [];
let lojaRmasCache = [];
let lojaCaixaAtual = null;
let lojaCarrinhoPdv = [];
let lojaItensOrcamento = [];

function _lojaBtnTab(btn, ativo) {
  if (typeof aplicarEstadoAbaBotao === 'function') {
    aplicarEstadoAbaBotao(
      btn,
      ativo,
      'px-3 py-1.5 text-xs font-bold rounded-lg bg-violet-600 text-white shadow-sm',
      'px-3 py-1.5 text-xs font-semibold rounded-lg text-violet-800 dark:text-violet-200 hover:bg-violet-100 dark:hover:bg-violet-900/40'
    );
  } else if (btn) {
    const estavaOculto = btn.classList.contains('hidden');
    btn.className = ativo
      ? 'px-3 py-1.5 text-xs font-bold rounded-lg bg-violet-600 text-white shadow-sm'
      : 'px-3 py-1.5 text-xs font-semibold rounded-lg text-violet-800 dark:text-violet-200';
    if (estavaOculto) btn.classList.add('hidden');
  }
}

function alternarLojaTab(tab) {
  const podePdv = temPermissao('operar_pdv_vendas') || temPermissao('ver_loja');
  const podeOrc = temPermissao('gerenciar_orcamentos_loja') || temPermissao('ver_loja');
  const podeCaixa = temPermissao('gerenciar_caixa_loja');
  const podeRma = temPermissao('gerenciar_garantias_rma') || temPermissao('ver_loja');
  const podeCli = temPermissao('gerenciar_clientes_loja') || temPermissao('ver_loja');

  const mapaPermitido = { pdv: podePdv, orcamentos: podeOrc, caixa: podeCaixa, rma: podeRma, clientes: podeCli };
  if (!mapaPermitido[tab]) {
    tab = ['pdv', 'orcamentos', 'rma', 'clientes', 'caixa'].find(k => mapaPermitido[k]) || 'pdv';
  }
  lojaTabAtual = tab;

  ['pdv', 'orcamentos', 'caixa', 'rma', 'clientes'].forEach(key => {
    const sec = document.getElementById(`lojaTab_${key}`);
    if (sec) sec.classList.toggle('hidden', key !== tab || !mapaPermitido[key]);
    _lojaBtnTab(document.getElementById(`btnLojaTab_${key}`), key === tab);
  });

  if (tab === 'caixa' && podeCaixa) {
    carregarCaixaLoja();
  }
}

async function carregarPainelLoja() {
  if (!temAcessoSetorLoja()) return;

  const podeDash = temPermissao('ver_dashboard_loja');
  const podeCustos = temPermissao('ver_custos');
  const podePdv = temPermissao('operar_pdv_vendas') || temPermissao('ver_loja');
  const podeOrc = temPermissao('gerenciar_orcamentos_loja') || temPermissao('ver_loja');
  const podeCaixa = temPermissao('gerenciar_caixa_loja');
  const podeRma = temPermissao('gerenciar_garantias_rma') || temPermissao('ver_loja');
  const podeCli = temPermissao('gerenciar_clientes_loja') || temPermissao('ver_loja');

  document.getElementById('lojaKpiStrip')?.classList.toggle('hidden', !podeDash);
  document.getElementById('lojaCurvaAbcCard')?.classList.toggle('hidden', !podeDash);

  const tarefas = [carregarResumoLoja(), carregarCatalogoLoja()];
  if (podePdv) tarefas.push(carregarVendasLoja());
  if (podeOrc) tarefas.push(carregarOrcamentosLoja());
  if (podeCaixa) tarefas.push(carregarCaixaLoja());
  if (podeRma) tarefas.push(carregarRmasLoja());
  if (podeCli || podePdv || podeOrc) tarefas.push(carregarClientesLoja());

  await Promise.all(tarefas);

  const vendInp = document.getElementById('pdv_vendedor');
  if (vendInp && !vendInp.value && sessaoAtual?.user?.nome) {
    vendInp.value = sessaoAtual.user.nome;
  }

  document.getElementById('lojaLucroKpiCard')?.classList.toggle('hidden', !podeCustos);
  document.getElementById('lojaFatMesKpiCard')?.classList.toggle('hidden', !podeCustos);
  document.getElementById('lojaCaixaKpiCard')?.classList.toggle('hidden', !podeCustos);

  alternarLojaTab(lojaTabAtual);
}

async function carregarResumoLoja() {
  const res = await apiFetch('/loja/resumo');
  if (!res.ok) return;
  const r = await res.json();
  const podeCustos = !r.ocultar_custos && temPermissao('ver_custos');
  const setTxt = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };

  setTxt('kpiLojaVendasMes', r.total_vendas_mes ?? 0);
  setTxt('kpiLojaOrcAbertos', r.orcamentos_abertos ?? 0);
  setTxt('kpiLojaOrcConv', `${r.taxa_conversao_orc ?? 0}% conv.`);
  setTxt('kpiLojaRmas', r.rmas_abertos ?? 0);
  setTxt('kpiLojaCaixaStatus', r.caixa_aberto ? '🟢 Caixa Aberto' : '🔒 Caixa Fechado');

  if (podeCustos) {
    setTxt('kpiLojaFatMes', `R$ ${formatarMoedaBR(r.faturamento_mes ?? 0)}`);
    setTxt('kpiLojaFatHoje', `Hoje: R$ ${formatarMoedaBR(r.faturamento_hoje ?? 0)}`);
    setTxt('kpiLojaLucroMes', `R$ ${formatarMoedaBR(r.lucro_mes ?? 0)}`);
    setTxt('kpiLojaMargem', `Margem ${r.margem_media_percent ?? 0}%`);
    setTxt('kpiLojaSaldoCaixa', `R$ ${formatarMoedaBR(r.saldo_caixa ?? 0)}`);
  }

  const abcBox = document.getElementById('listaCurvaAbcLoja');
  if (abcBox) {
    const curva = Array.isArray(r.curva_abc) ? r.curva_abc : [];
    abcBox.innerHTML = curva.length === 0
      ? '<p class="text-slate-400">Registre vendas no PDV para gerar a Curva ABC de giro e receita.</p>'
      : curva.map(item => `
        <div class="p-2.5 rounded-xl bg-violet-50/70 dark:bg-[#1C1633] border border-violet-200/80 dark:border-violet-800/70 flex items-center justify-between gap-2 text-xs">
          <div class="flex items-center gap-2 min-w-0">
            <span class="px-2 py-0.5 rounded font-extrabold text-[10px] ${item.classe === 'A' ? 'bg-emerald-600 text-white' : (item.classe === 'B' ? 'bg-violet-600 text-white' : 'bg-slate-200 text-slate-800')}">Classe ${escapeHtml(item.classe)}</span>
            <span class="font-bold text-slate-900 dark:text-white truncate">${escapeHtml(item.nome)}</span>
          </div>
          <span class="text-[11px] font-semibold text-slate-600 dark:text-violet-200 shrink-0">${item.quantidade} un • ${item.participacao_percent}%${podeCustos ? ` (R$ ${formatarMoedaBR(item.receita)})` : ''}</span>
        </div>
      `).join('');
  }
}

async function carregarCatalogoLoja() {
  const res = await apiFetch('/loja/catalogo');
  if (!res.ok) return;
  const itens = await res.json();
  lojaCatalogoCache = Array.isArray(itens) ? itens : [];

  ['pdv_produto_select', 'orc_produto_select'].forEach(selId => {
    const sel = document.getElementById(selId);
    if (!sel) return;
    const opts = lojaCatalogoCache.map(i => {
      const preco = Number(i.preco_venda || 0);
      const b2b = Number(i.preco_atacado_b2b || 0);
      const disp = Number(i.quantidade || 0);
      return `<option value="${escapeHtml(i.id)}">${escapeHtml(i.nome)} (${disp} ${escapeHtml(i.unidade || 'un')}) — R$ ${formatarMoedaBR(preco)}${b2b > 0 ? ` / B2B R$ ${formatarMoedaBR(b2b)}` : ''}</option>`;
    }).join('');
    sel.innerHTML = `<option value="">-- Item Avulso / Serviço --</option>${opts}`;
  });
}

function aoSelecionarProdutoPdv() {
  const id = document.getElementById('pdv_produto_select')?.value || '';
  const tabela = document.getElementById('pdv_tabela_preco')?.value || 'varejo';
  const item = lojaCatalogoCache.find(i => i.id === id);
  if (!item) return;

  document.getElementById('pdv_item_nome').value = item.nome || '';
  const preco = (tabela === 'b2b' && Number(item.preco_atacado_b2b || 0) > 0)
    ? Number(item.preco_atacado_b2b)
    : Number(item.preco_venda || 0);
  document.getElementById('pdv_item_preco').value = preco.toFixed(2);
  document.getElementById('pdv_item_garantia').value = item.garantia_meses ?? 3;
}

function adicionarItemCarrinhoPdv() {
  const estId = document.getElementById('pdv_produto_select')?.value || '';
  const nome = (document.getElementById('pdv_item_nome')?.value || '').trim();
  const qtd = Math.max(1, Number.parseInt(document.getElementById('pdv_item_qtd')?.value || '1', 10) || 1);
  const preco = Math.max(0, Number.parseFloat(document.getElementById('pdv_item_preco')?.value || '0') || 0);
  const serial = (document.getElementById('pdv_item_serial')?.value || '').trim();
  const garantia = Math.max(0, Number.parseInt(document.getElementById('pdv_item_garantia')?.value || '3', 10) || 0);

  if (!nome) {
    mostrarToast('Informe o nome do produto ou serviço para adicionar ao carrinho.', 'erro');
    return;
  }

  lojaCarrinhoPdv.push({
    estoque_item_id: estId,
    nome,
    quantidade: qtd,
    preco_unitario: preco,
    serial_imei: serial,
    garantia_meses: garantia,
  });

  document.getElementById('pdv_produto_select').value = '';
  document.getElementById('pdv_item_nome').value = '';
  document.getElementById('pdv_item_qtd').value = '1';
  document.getElementById('pdv_item_preco').value = '';
  document.getElementById('pdv_item_serial').value = '';
  renderizarCarrinhoPdv();
}

function removerItemCarrinhoPdv(idx) {
  lojaCarrinhoPdv.splice(idx, 1);
  renderizarCarrinhoPdv();
}

function renderizarCarrinhoPdv() {
  const box = document.getElementById('pdv_carrinho_lista');
  if (!box) return;
  if (lojaCarrinhoPdv.length === 0) {
    box.innerHTML = '<p class="text-slate-400">Nenhum item no carrinho do PDV.</p>';
  } else {
    box.innerHTML = lojaCarrinhoPdv.map((it, idx) => `
      <div class="p-2 rounded-lg bg-violet-50/70 dark:bg-[#1C1633] border border-violet-200/80 dark:border-violet-800/70 flex justify-between items-center gap-2 text-xs">
        <div class="min-w-0">
          <div class="font-bold text-slate-900 dark:text-white truncate">${it.quantidade}x ${escapeHtml(it.nome)}</div>
          <div class="text-[10px] text-slate-500 dark:text-violet-300/80">Unit: R$ ${formatarMoedaBR(it.preco_unitario)} • Garantia: ${it.garantia_meses}m${it.serial_imei ? ` • S/N: ${escapeHtml(it.serial_imei)}` : ''}</div>
        </div>
        <div class="flex items-center gap-2 shrink-0">
          <span class="font-extrabold text-violet-700 dark:text-violet-300">R$ ${formatarMoedaBR(it.quantidade * it.preco_unitario)}</span>
          <button type="button" onclick="removerItemCarrinhoPdv(${idx})" class="text-rose-500 font-bold text-xs">✕</button>
        </div>
      </div>
    `).join('');
  }
  atualizarTotalCarrinhoPdv();
}

function atualizarTotalCarrinhoPdv() {
  const subtotal = lojaCarrinhoPdv.reduce((acc, it) => acc + (Number(it.quantidade) * Number(it.preco_unitario)), 0);
  const desconto = Math.max(0, Number.parseFloat(document.getElementById('pdv_desconto')?.value || '0') || 0);
  const total = Math.max(0, subtotal - desconto);
  const el = document.getElementById('pdv_total_txt');
  if (el) el.textContent = `R$ ${formatarMoedaBR(total)}`;
}

async function finalizarVendaPdv(event) {
  event.preventDefault();
  if (lojaCarrinhoPdv.length === 0) {
    mostrarToast('Adicione pelo menos 1 item ao carrinho antes de finalizar.', 'erro');
    return;
  }

  const cliId = document.getElementById('pdv_cliente_select')?.value || '';
  const cli = lojaClientesCache.find(c => c.id === cliId);
  const payload = {
    cliente_id: cliId,
    cliente_nome: document.getElementById('pdv_cliente_nome')?.value.trim() || cli?.nome || 'Consumidor Final',
    cliente_doc: document.getElementById('pdv_cliente_doc')?.value.trim() || cli?.documento || '',
    tabela_preco: document.getElementById('pdv_tabela_preco')?.value || 'varejo',
    forma_pagamento: document.getElementById('pdv_forma_pgto')?.value || 'Pix',
    parcelas: Number.parseInt(document.getElementById('pdv_parcelas')?.value || '1', 10) || 1,
    desconto: Number.parseFloat(document.getElementById('pdv_desconto')?.value || '0') || 0,
    tipo_documento: document.getElementById('pdv_tipo_doc')?.value || 'Recibo Interno',
    numero_nf: document.getElementById('pdv_numero_nf')?.value.trim() || '',
    filial: document.getElementById('pdv_filial')?.value.trim() || 'Matriz',
    vendedor: document.getElementById('pdv_vendedor')?.value.trim() || sessaoAtual?.user?.nome || '',
    comissao_percent: Number.parseFloat(document.getElementById('pdv_comissao')?.value || '0') || 0,
    observacoes: document.getElementById('pdv_obs')?.value.trim() || '',
    itens: lojaCarrinhoPdv,
  };

  const res = await apiFetch('/loja/vendas', { method: 'POST', body: JSON.stringify(payload) });
  if (!res.ok) {
    mostrarToast(await extrairErroResposta(res, 'Erro ao finalizar venda.'), 'erro');
    return;
  }
  const data = await res.json();
  lojaCarrinhoPdv = [];
  document.getElementById('pdv_desconto').value = '0';
  document.getElementById('pdv_numero_nf').value = '';
  document.getElementById('pdv_obs').value = '';
  renderizarCarrinhoPdv();
  mostrarToast(`Venda ${data.venda?.codigo || ''} registrada com sucesso!`, 'sucesso');
  await carregarPainelLoja();
}

async function carregarVendasLoja() {
  const res = await apiFetch('/loja/vendas');
  if (!res.ok) return;
  const vendas = await res.json();
  lojaVendasCache = Array.isArray(vendas) ? vendas : [];
  const tbody = document.getElementById('tabelaVendasLoja');
  if (!tbody) return;
  const podeCustos = temPermissao('ver_custos');
  const podePdf = temPermissao('ver_loja') || temPermissao('operar_pdv_vendas') || temPermissao('exportar_pdf');
  const podeCancelar = temPermissao('operar_pdv_vendas') || temPermissao('gerenciar_caixa_loja');

  if (lojaVendasCache.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" class="p-4 text-center text-slate-400">Nenhuma venda registrada no PDV ainda.</td></tr>';
    return;
  }

  tbody.innerHTML = lojaVendasCache.map(v => {
    const cancelada = v.status === 'Cancelada';
    return `
      <tr class="${cancelada ? 'opacity-60' : ''}">
        <td class="p-3 font-bold text-violet-600 dark:text-violet-400">${escapeHtml(v.codigo)}<div class="text-[10px] text-slate-400">${escapeHtml(v.tipo_documento || '')} ${escapeHtml(v.numero_nf || '')}</div></td>
        <td class="p-3"><div class="font-bold text-slate-900 dark:text-white">${escapeHtml(v.cliente_nome)}</div><div class="text-[10px] text-slate-400">${escapeHtml(v.vendedor || '')} • ${formatarDataBR(v.data_venda)}</div></td>
        <td class="p-3 text-slate-700 dark:text-violet-200">${(v.itens || []).length} item(ns)<div class="text-[10px] text-slate-400 truncate max-w-[180px]">${escapeHtml((v.itens || []).map(i => i.nome).join(', '))}</div></td>
        <td class="p-3 text-slate-700 dark:text-violet-200">${escapeHtml(v.forma_pagamento)} (${v.parcelas || 1}x)</td>
        <td class="p-3 font-extrabold text-slate-900 dark:text-white">R$ ${formatarMoedaBR(v.total)}${podeCustos && v.lucro_estimado !== undefined ? `<div class="text-[10px] text-emerald-600">Lucro: R$ ${formatarMoedaBR(v.lucro_estimado)}</div>` : ''}</td>
        <td class="p-3 text-right space-x-1">
          ${podePdf ? `<button type="button" onclick="baixarReciboVendaPdf('${escapeHtml(v.id)}')" class="px-2.5 py-1 rounded-lg bg-violet-600 text-white text-[10px] font-bold">🧾 Recibo PDF</button>` : ''}
          ${(!cancelada && podeCancelar) ? `<button type="button" onclick="cancelarVendaLoja('${escapeHtml(v.id)}')" class="px-2 py-1 rounded-lg bg-rose-50 text-rose-600 text-[10px] font-bold">Cancelar</button>` : `<span class="text-[10px] font-bold text-rose-500">${cancelada ? 'Cancelada' : ''}</span>`}
        </td>
      </tr>
    `;
  }).join('');
}

function baixarReciboVendaPdf(vendaId) {
  abrirPdfAutenticado(`/loja/vendas/${encodeURIComponent(vendaId)}/recibo/pdf`, 'Erro ao gerar recibo de venda.');
}

async function cancelarVendaLoja(vendaId) {
  if (!window.confirm('Cancelar esta venda e devolver os itens ao estoque?')) return;
  const res = await apiFetch(`/loja/vendas/${encodeURIComponent(vendaId)}/cancelar`, {
    method: 'POST',
    body: JSON.stringify({ motivo: 'Cancelado pelo operador no PDV' }),
  });
  if (!res.ok) {
    mostrarToast(await extrairErroResposta(res, 'Erro ao cancelar venda.'), 'erro');
    return;
  }
  mostrarToast('Venda cancelada e itens estornados ao estoque!', 'info');
  await carregarPainelLoja();
}

// --- Orçamentos & Montagem de PCs ---
function aoSelecionarProdutoOrc() {
  const id = document.getElementById('orc_produto_select')?.value || '';
  const item = lojaCatalogoCache.find(i => i.id === id);
  if (!item) return;
  document.getElementById('orc_item_nome').value = item.nome || '';
  document.getElementById('orc_item_preco').value = Number(item.preco_venda || 0).toFixed(2);
}

function adicionarItemOrcamento() {
  const estId = document.getElementById('orc_produto_select')?.value || '';
  const nome = (document.getElementById('orc_item_nome')?.value || '').trim();
  const qtd = Math.max(1, Number.parseInt(document.getElementById('orc_item_qtd')?.value || '1', 10) || 1);
  const preco = Math.max(0, Number.parseFloat(document.getElementById('orc_item_preco')?.value || '0') || 0);
  if (!nome) {
    mostrarToast('Informe a descrição do item para o orçamento.', 'erro');
    return;
  }
  lojaItensOrcamento.push({ estoque_item_id: estId, nome, quantidade: qtd, preco_unitario: preco, garantia_meses: 6 });
  document.getElementById('orc_item_nome').value = '';
  document.getElementById('orc_item_preco').value = '';
  renderizarItensOrcamento();
}

function renderizarItensOrcamento() {
  const box = document.getElementById('orc_itens_lista');
  if (!box) return;
  if (lojaItensOrcamento.length === 0) {
    box.innerHTML = '<p class="text-slate-400">Adicione peças/serviços para montar a proposta.</p>';
    return;
  }
  box.innerHTML = lojaItensOrcamento.map((it, idx) => `
    <div class="p-2 rounded-lg bg-violet-50/70 dark:bg-[#1C1633] border border-violet-200/80 dark:border-violet-800/70 flex justify-between items-center text-xs">
      <span><b>${it.quantidade}x</b> ${escapeHtml(it.nome)} — R$ ${formatarMoedaBR(it.quantidade * it.preco_unitario)}</span>
      <button type="button" onclick="lojaItensOrcamento.splice(${idx},1); renderizarItensOrcamento();" class="text-rose-500 font-bold">✕</button>
    </div>
  `).join('');
}

async function criarOrcamentoLoja(event) {
  event.preventDefault();
  if (lojaItensOrcamento.length === 0) {
    mostrarToast('Adicione pelo menos 1 componente ou serviço ao orçamento.', 'erro');
    return;
  }
  const payload = {
    titulo: document.getElementById('orc_titulo')?.value.trim() || 'Montagem PC / Proposta TI',
    tipo: document.getElementById('orc_tipo')?.value || 'Montagem PC / Hardware',
    cliente_nome: document.getElementById('orc_cliente_nome')?.value.trim() || '',
    cliente_telefone: document.getElementById('orc_cliente_tel')?.value.trim() || '',
    validade_dias: Number.parseInt(document.getElementById('orc_validade')?.value || '7', 10) || 7,
    desconto: Number.parseFloat(document.getElementById('orc_desconto')?.value || '0') || 0,
    condicoes_pagamento: document.getElementById('orc_condicoes')?.value.trim() || '',
    itens: lojaItensOrcamento,
  };
  const res = await apiFetch('/loja/orcamentos', { method: 'POST', body: JSON.stringify(payload) });
  if (!res.ok) {
    mostrarToast(await extrairErroResposta(res, 'Erro ao salvar orçamento.'), 'erro');
    return;
  }
  lojaItensOrcamento = [];
  event.target.reset();
  renderizarItensOrcamento();
  mostrarToast('Orçamento emitido e salvo!', 'sucesso');
  await carregarPainelLoja();
}

async function carregarOrcamentosLoja() {
  const res = await apiFetch('/loja/orcamentos');
  if (!res.ok) return;
  lojaOrcamentosCache = await res.json();
  const box = document.getElementById('listaOrcamentosLoja');
  if (!box) return;
  if (!lojaOrcamentosCache.length) {
    box.innerHTML = '<p class="text-slate-400">Nenhum orçamento comercial emitido ainda.</p>';
    return;
  }
  const podeVender = temPermissao('operar_pdv_vendas');
  const podeOs = temPermissao('registrar_entrada') || temPermissao('gerenciar_orcamentos_loja');
  box.innerHTML = lojaOrcamentosCache.map(o => `
    <div class="p-3.5 rounded-xl bg-violet-50/70 dark:bg-[#1C1633] border border-violet-200/80 dark:border-violet-800/70 space-y-2 text-xs">
      <div class="flex flex-wrap justify-between items-start gap-2">
        <div>
          <span class="text-[10px] font-extrabold text-violet-600 dark:text-violet-400 uppercase">${escapeHtml(o.codigo)} • ${escapeHtml(o.tipo)} • Válido até ${formatarDataBR(o.valido_ate)}</span>
          <h4 class="font-extrabold text-slate-900 dark:text-white">${escapeHtml(o.titulo)} — ${escapeHtml(o.cliente_nome)}</h4>
          <p class="text-[11px] text-slate-600 dark:text-violet-200">Total: <b>R$ ${formatarMoedaBR(o.total)}</b> (${(o.itens || []).length} itens)</p>
        </div>
        <span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-white dark:bg-[#141024] border border-violet-200 dark:border-violet-700">${escapeHtml(o.status)}</span>
      </div>
      <div class="flex flex-wrap gap-1.5">
        <button type="button" onclick="abrirPdfAutenticado('/loja/orcamentos/${escapeHtml(o.id)}/pdf', 'Erro ao gerar PDF do orçamento.')" class="px-2.5 py-1 rounded-lg bg-violet-600 text-white text-[10px] font-bold">📄 Proposta PDF</button>
        ${podeVender && !o.venda_gerada_id ? `<button type="button" onclick="converterOrcamentoEmVenda('${escapeHtml(o.id)}')" class="px-2.5 py-1 rounded-lg bg-emerald-600 text-white text-[10px] font-bold">🛒 Virar Venda PDV</button>` : ''}
        ${podeOs && !o.os_gerada_id ? `<button type="button" onclick="converterOrcamentoEmOs('${escapeHtml(o.id)}')" class="px-2.5 py-1 rounded-lg bg-indigo-600 text-white text-[10px] font-bold">🖥️ Gerar O.S. Bancada</button>` : ''}
        <button type="button" onclick="excluirOrcamentoLoja('${escapeHtml(o.id)}')" class="px-2 py-1 rounded-lg bg-rose-50 text-rose-600 text-[10px] font-bold">✕</button>
      </div>
    </div>
  `).join('');
}

async function converterOrcamentoEmVenda(orcId) {
  const res = await apiFetch(`/loja/orcamentos/${encodeURIComponent(orcId)}/converter-venda`, {
    method: 'POST',
    body: JSON.stringify({ forma_pagamento: 'Pix' }),
  });
  if (!res.ok) {
    mostrarToast(await extrairErroResposta(res, 'Erro ao converter orçamento em venda.'), 'erro');
    return;
  }
  const data = await res.json();
  mostrarToast(`Venda ${data.venda?.codigo || ''} gerada a partir do orçamento!`, 'sucesso');
  await carregarPainelLoja();
}

async function converterOrcamentoEmOs(orcId) {
  const res = await apiFetch(`/loja/orcamentos/${encodeURIComponent(orcId)}/converter-os`, {
    method: 'POST',
    body: JSON.stringify({ localizacao: 'Bancada TI' }),
  });
  if (!res.ok) {
    mostrarToast(await extrairErroResposta(res, 'Erro ao gerar O.S.'), 'erro');
    return;
  }
  const data = await res.json();
  mostrarToast(`O.S. #${data.registro?.id || ''} aberta na Bancada TI!`, 'sucesso');
  await carregarPainelLoja();
}

async function excluirOrcamentoLoja(orcId) {
  const res = await apiFetch(`/loja/orcamentos/${encodeURIComponent(orcId)}`, { method: 'DELETE' });
  if (res.ok) await carregarOrcamentosLoja();
}

// --- Caixa PDV ---
async function carregarCaixaLoja() {
  const res = await apiFetch('/loja/caixa');
  if (!res.ok) return;
  const data = await res.json();
  lojaCaixaAtual = data.caixa_atual || null;
  const boxStatus = document.getElementById('caixaStatusBox');
  const formAbrir = document.getElementById('formAbrirCaixaLoja');
  const panelMov = document.getElementById('painelCaixaAbertoLoja');

  if (formAbrir) formAbrir.classList.toggle('hidden', Boolean(lojaCaixaAtual));
  if (panelMov) panelMov.classList.toggle('hidden', !lojaCaixaAtual);
  if (boxStatus) {
    boxStatus.innerHTML = lojaCaixaAtual
      ? `<div class="p-4 rounded-xl bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-800 space-y-1 text-xs">
           <div class="font-extrabold text-emerald-800 dark:text-emerald-300">🟢 Caixa Aberto — ${escapeHtml(lojaCaixaAtual.filial || 'Matriz')} (${escapeHtml(lojaCaixaAtual.operador || '')})</div>
           <div>Fundo Inicial: <b>R$ ${formatarMoedaBR(lojaCaixaAtual.valor_abertura)}</b> • Vendas: <b>${lojaCaixaAtual.qtd_vendas || 0}</b> (R$ ${formatarMoedaBR(lojaCaixaAtual.total_vendido)}) • Saldo em Gaveta: <b>R$ ${formatarMoedaBR(lojaCaixaAtual.saldo_esperado)}</b></div>
         </div>`
      : '<div class="p-4 rounded-xl bg-amber-50 dark:bg-amber-950/40 border border-amber-200 dark:border-amber-800 text-xs font-semibold text-amber-800 dark:text-amber-300">🔒 Nenhum turno de caixa aberto no momento.</div>';
  }

  const histBox = document.getElementById('listaTurnosCaixaLoja');
  const turnos = Array.isArray(data.turnos) ? data.turnos : [];
  if (histBox) {
    histBox.innerHTML = turnos.length === 0
      ? '<p class="text-slate-400">Nenhum histórico de caixa registrado.</p>'
      : turnos.map(t => `
        <div class="p-3 rounded-xl bg-violet-50/70 dark:bg-[#1C1633] border border-violet-200/80 dark:border-violet-800/70 flex flex-wrap justify-between gap-2 text-xs">
          <div>
            <div class="font-bold text-slate-900 dark:text-white">${escapeHtml(t.status)} • ${escapeHtml(t.filial || 'Matriz')} • Operador: ${escapeHtml(t.operador || '')}</div>
            <div class="text-[11px] text-slate-500 dark:text-violet-300/80">Vendido: R$ ${formatarMoedaBR(t.total_vendido)} (${t.qtd_vendas || 0} vendas) • Saldo Esperado: R$ ${formatarMoedaBR(t.saldo_esperado)}</div>
          </div>
          ${t.status === 'Fechado' ? `<span class="font-bold text-[11px] ${Number(t.diferenca) === 0 ? 'text-emerald-600' : 'text-amber-600'}">Conferido: R$ ${formatarMoedaBR(t.saldo_informado)} (Dif. R$ ${formatarMoedaBR(t.diferenca)})</span>` : ''}
        </div>
      `).join('');
  }
}

async function abrirCaixaLoja(event) {
  event.preventDefault();
  const payload = {
    valor_abertura: Number.parseFloat(document.getElementById('cx_abertura_val')?.value || '0') || 0,
    filial: document.getElementById('cx_abertura_filial')?.value.trim() || 'Matriz',
    observacao: document.getElementById('cx_abertura_obs')?.value.trim() || '',
  };
  const res = await apiFetch('/loja/caixa/abrir', { method: 'POST', body: JSON.stringify(payload) });
  if (!res.ok) {
    mostrarToast(await extrairErroResposta(res, 'Erro ao abrir caixa.'), 'erro');
    return;
  }
  mostrarToast('Caixa aberto!', 'sucesso');
  await carregarPainelLoja();
}

async function movimentarCaixaLoja(tipo) {
  const valor = Number.parseFloat(document.getElementById('cx_mov_val')?.value || '0') || 0;
  const motivo = document.getElementById('cx_mov_motivo')?.value.trim() || '';
  const res = await apiFetch('/loja/caixa/movimento', {
    method: 'POST',
    body: JSON.stringify({ tipo, valor, motivo }),
  });
  if (!res.ok) {
    mostrarToast(await extrairErroResposta(res, 'Erro ao movimentar caixa.'), 'erro');
    return;
  }
  document.getElementById('cx_mov_val').value = '';
  document.getElementById('cx_mov_motivo').value = '';
  mostrarToast(`${tipo === 'suprimento' ? 'Suprimento' : 'Sangria'} registrado!`, 'sucesso');
  await carregarPainelLoja();
}

async function fecharCaixaLoja(event) {
  event.preventDefault();
  const saldo = Number.parseFloat(document.getElementById('cx_fechar_val')?.value || '0') || 0;
  const obs = document.getElementById('cx_fechar_obs')?.value.trim() || '';
  const res = await apiFetch('/loja/caixa/fechar', {
    method: 'POST',
    body: JSON.stringify({ saldo_informado: saldo, observacao: obs }),
  });
  if (!res.ok) {
    mostrarToast(await extrairErroResposta(res, 'Erro ao fechar caixa.'), 'erro');
    return;
  }
  mostrarToast('Caixa fechado e conferido!', 'sucesso');
  await carregarPainelLoja();
}

// --- Garantias Antifraude & RMA ---
async function consultarGarantiaSerial() {
  const termo = (document.getElementById('garantia_busca_serial')?.value || '').trim();
  const box = document.getElementById('resultadoConsultaGarantia');
  if (!termo || !box) return;
  const res = await apiFetch(`/loja/garantia/consultar?termo=${encodeURIComponent(termo)}`);
  if (!res.ok) {
    box.innerHTML = '<p class="text-rose-500">Informe um serial, IMEI ou código de venda válido.</p>';
    return;
  }
  const lista = await res.json();
  if (!lista.length) {
    box.innerHTML = '<p class="text-amber-600 font-semibold">⚠️ Nenhum registro de venda encontrado para este Serial/IMEI ou código nesta loja.</p>';
    return;
  }
  box.innerHTML = lista.map(r => `
    <div class="p-3 rounded-xl border ${r.em_garantia ? 'bg-emerald-50 border-emerald-200 text-emerald-900' : 'bg-rose-50 border-rose-200 text-rose-900'} text-xs">
      <div class="font-extrabold">${r.em_garantia ? '✅ EM GARANTIA' : '❌ GARANTIA EXPIRADA'} — ${escapeHtml(r.produto)}</div>
      <div>Venda: <b>${escapeHtml(r.venda_codigo)}</b> (${formatarDataBR(r.data_venda)}) • Cliente: <b>${escapeHtml(r.cliente_nome)}</b> • Serial: <b>${escapeHtml(r.serial_imei)}</b></div>
      <div>Validade da Garantia: <b>${formatarDataBR(r.garantia_ate)}</b> (${r.garantia_meses} meses)</div>
    </div>
  `).join('');
}

async function criarRmaLoja(event) {
  event.preventDefault();
  const payload = {
    tipo: document.getElementById('rma_tipo')?.value || 'RMA Cliente',
    produto: document.getElementById('rma_produto')?.value.trim() || '',
    serial_imei: document.getElementById('rma_serial')?.value.trim() || '',
    venda_codigo: document.getElementById('rma_venda')?.value.trim() || '',
    cliente_fornecedor: document.getElementById('rma_cliente')?.value.trim() || '',
    defeito: document.getElementById('rma_defeito')?.value.trim() || '',
    protocolo_fabricante: document.getElementById('rma_protocolo')?.value.trim() || '',
  };
  const res = await apiFetch('/loja/rma', { method: 'POST', body: JSON.stringify(payload) });
  if (!res.ok) {
    mostrarToast(await extrairErroResposta(res, 'Erro ao abrir RMA.'), 'erro');
    return;
  }
  event.target.reset();
  mostrarToast('RMA / Garantia registrado!', 'sucesso');
  await carregarPainelLoja();
}

async function carregarRmasLoja() {
  const res = await apiFetch('/loja/rma');
  if (!res.ok) return;
  lojaRmasCache = await res.json();
  const box = document.getElementById('listaRmasLoja');
  if (!box) return;
  if (!lojaRmasCache.length) {
    box.innerHTML = '<p class="text-slate-400">Nenhum processo de RMA/garantia aberto.</p>';
    return;
  }
  const podeGerir = temPermissao('gerenciar_garantias_rma');
  box.innerHTML = lojaRmasCache.map(r => `
    <div class="p-3 rounded-xl bg-violet-50/70 dark:bg-[#1C1633] border border-violet-200/80 dark:border-violet-800/70 flex flex-wrap justify-between items-center gap-2 text-xs">
      <div>
        <div class="font-bold text-slate-900 dark:text-white">${escapeHtml(r.codigo)} • ${escapeHtml(r.produto)} <span class="text-[10px] text-violet-600">(${escapeHtml(r.tipo)})</span></div>
        <div class="text-[11px] text-slate-500 dark:text-violet-300/80">Serial: ${escapeHtml(r.serial_imei || '-')} • ${escapeHtml(r.cliente_fornecedor || '-')} • ${escapeHtml(r.defeito || '')}</div>
      </div>
      <div class="flex items-center gap-1.5">
        <select ${podeGerir ? '' : 'disabled'} onchange="atualizarStatusRmaLoja('${escapeHtml(r.id)}', this.value)" class="px-2 py-1 rounded-lg border border-violet-200 dark:border-violet-700 bg-white dark:bg-[#141024] text-[11px] font-semibold">
          ${['Aberto', 'Em Análise Bancada', 'Enviado ao Fornecedor', 'Crédito / Troca Aprovada', 'Concluído', 'Negado'].map(st => `<option value="${st}" ${r.status === st ? 'selected' : ''}>${st}</option>`).join('')}
        </select>
        ${podeGerir ? `<button type="button" onclick="excluirRmaLoja('${escapeHtml(r.id)}')" class="px-2 py-1 rounded-lg bg-rose-50 text-rose-600 text-[10px] font-bold">✕</button>` : ''}
      </div>
    </div>
  `).join('');
}

async function atualizarStatusRmaLoja(rmaId, status) {
  const res = await apiFetch(`/loja/rma/${encodeURIComponent(rmaId)}`, {
    method: 'PATCH',
    body: JSON.stringify({ status }),
  });
  if (res.ok) {
    mostrarToast('Status do RMA atualizado!', 'sucesso');
    await carregarResumoLoja();
  }
}

async function excluirRmaLoja(rmaId) {
  const res = await apiFetch(`/loja/rma/${encodeURIComponent(rmaId)}`, { method: 'DELETE' });
  if (res.ok) await carregarPainelLoja();
}

// --- Clientes PF/PJ (B2B), Fornecedores & Datalist de Origens ---
async function carregarClientesLoja() {
  const res = await apiFetch('/loja/clientes');
  if (!res.ok) return;
  const itens = await res.json();
  lojaClientesCache = Array.isArray(itens) ? itens : [];

  const selPdv = document.getElementById('pdv_cliente_select');
  if (selPdv) {
    selPdv.innerHTML = '<option value="">Consumidor Final (Avulso)</option>' +
      lojaClientesCache.filter(c => !String(c.tipo || '').toLowerCase().includes('fornecedor')).map(c =>
        `<option value="${escapeHtml(c.id)}">${escapeHtml(c.nome)} (${escapeHtml(c.tipo)})</option>`
      ).join('');
  }

  const dl = document.getElementById('datalistOrigens');
  if (dl) {
    const existentes = new Set(Array.from(dl.options).map(o => o.value));
    lojaClientesCache.forEach(c => {
      if (c.nome && !existentes.has(c.nome)) {
        const opt = document.createElement('option');
        opt.value = c.nome;
        dl.appendChild(opt);
      }
    });
  }

  const tbody = document.getElementById('tabelaClientesLoja');
  if (tbody) {
    tbody.innerHTML = lojaClientesCache.length === 0
      ? '<tr><td colspan="5" class="p-4 text-center text-slate-400">Nenhum cliente B2B/PF ou fornecedor cadastrado.</td></tr>'
      : lojaClientesCache.map(c => `
        <tr>
          <td class="p-3 font-bold text-slate-900 dark:text-white">${escapeHtml(c.nome)}<div class="text-[10px] font-normal text-slate-400">${escapeHtml(c.documento || '')}</div></td>
          <td class="p-3 text-slate-600 dark:text-violet-200">${escapeHtml(c.tipo)}</td>
          <td class="p-3 uppercase font-bold text-[10px] text-violet-600 dark:text-violet-300">${escapeHtml(c.tabela_preco || 'varejo')}</td>
          <td class="p-3 text-slate-600 dark:text-violet-200">${escapeHtml([c.telefone, c.email].filter(Boolean).join(' • ') || '-')}</td>
          <td class="p-3 text-right"><button type="button" onclick="excluirClienteLoja('${escapeHtml(c.id)}')" class="px-2 py-1 rounded-lg bg-rose-50 text-rose-600 text-[10px] font-bold">Excluir</button></td>
        </tr>
      `).join('');
  }
}

function aoSelecionarClientePdv() {
  const cliId = document.getElementById('pdv_cliente_select')?.value || '';
  const cli = lojaClientesCache.find(c => c.id === cliId);
  if (!cli) return;
  document.getElementById('pdv_cliente_nome').value = cli.nome || '';
  document.getElementById('pdv_cliente_doc').value = cli.documento || '';
  if (document.getElementById('pdv_tabela_preco')) {
    document.getElementById('pdv_tabela_preco').value = cli.tabela_preco || 'varejo';
  }
}

async function criarClienteLoja(event) {
  event.preventDefault();
  const payload = {
    nome: document.getElementById('lcli_nome')?.value.trim() || '',
    tipo: document.getElementById('lcli_tipo')?.value || 'Cliente PF',
    documento: document.getElementById('lcli_doc')?.value.trim() || '',
    tabela_preco: document.getElementById('lcli_tabela')?.value || 'varejo',
    telefone: document.getElementById('lcli_tel')?.value.trim() || '',
    email: document.getElementById('lcli_email')?.value.trim() || '',
    endereco: document.getElementById('lcli_end')?.value.trim() || '',
  };
  const res = await apiFetch('/loja/clientes', { method: 'POST', body: JSON.stringify(payload) });
  if (!res.ok) {
    mostrarToast(await extrairErroResposta(res, 'Erro ao cadastrar cliente/fornecedor.'), 'erro');
    return;
  }
  event.target.reset();
  mostrarToast('Cadastro salvo na Loja de TI!', 'sucesso');
  await carregarClientesLoja();
}

async function excluirClienteLoja(id) {
  const res = await apiFetch(`/loja/clientes/${encodeURIComponent(id)}`, { method: 'DELETE' });
  if (res.ok) await carregarClientesLoja();
}

function baixarRelatorioLojaPdf() {
  abrirPdfAutenticado('/loja/relatorio/pdf', 'Erro ao gerar relatório PDF da Loja de TI.');
}
