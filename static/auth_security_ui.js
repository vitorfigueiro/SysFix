// ==============================================================================
// PAINEL INTERATIVO DE TI NO LOGIN & CENTRAL DE SEGURANÇA / 2FA TOTP
// ==============================================================================

const LOGIN_TECH_TOPICS = {
  infra: {
    titulo: 'Redes, Servidores & Alta Disponibilidade',
    desc: 'Segmentação por VLANs, redundância LACP/OSPF, monitoramento SNMP/Zabbix e backup imutável 3-2-1-1-0.',
    badges: ['VLAN 802.1Q', 'RAID & UPS', 'SNMP / ICMP'],
    svg: `
      <svg viewBox="0 0 280 128" class="w-full h-32" fill="none" xmlns="http://www.w3.org/2000/svg">
        <rect x="18" y="20" width="64" height="88" rx="10" fill="rgba(255,255,255,0.12)" stroke="rgba(255,255,255,0.35)" stroke-width="1.5"/>
        <rect x="28" y="32" width="44" height="12" rx="3" fill="rgba(167,139,250,0.4)"/>
        <circle cx="35" cy="38" r="2.2" fill="#34D399" class="tech-pulse"/>
        <rect x="28" y="50" width="44" height="12" rx="3" fill="rgba(167,139,250,0.4)"/>
        <circle cx="35" cy="56" r="2.2" fill="#34D399"/>
        <rect x="28" y="68" width="44" height="12" rx="3" fill="rgba(167,139,250,0.4)"/>
        <circle cx="35" cy="74" r="2.2" fill="#FBBF24" class="tech-pulse"/>
        <path d="M84 52 H134" stroke="#E9D5FF" stroke-width="2" class="tech-packet-dash"/>
        <path d="M84 74 H134" stroke="#A7F3D0" stroke-width="2" class="tech-packet-dash"/>
        <g class="tech-float">
          <rect x="134" y="34" width="60" height="58" rx="12" fill="rgba(255,255,255,0.16)" stroke="#FFFFFF" stroke-width="1.5"/>
          <path d="M148 56 H180 M148 66 H172" stroke="#FDE68A" stroke-width="2.2" stroke-linecap="round"/>
          <circle cx="164" cy="46" r="4" fill="#34D399"/>
        </g>
        <path d="M194 63 H226" stroke="#E9D5FF" stroke-width="2" class="tech-packet-dash"/>
        <circle cx="244" cy="63" r="18" fill="rgba(255,255,255,0.15)" stroke="#DDD6FE" stroke-width="1.5"/>
        <text x="244" y="67" text-anchor="middle" font-size="9" font-weight="800" fill="#FFFFFF">99.9%</text>
      </svg>
    `,
  },
  loja: {
    titulo: 'Loja de TI, PDV, Orçamentos & RMA',
    desc: 'Frente de caixa ágil, propostas de PCs sob medida, rastreamento antifraude por Serial/IMEI e baixa automática em estoque.',
    badges: ['PDV & Caixa', 'Curva ABC / B2B', 'Garantia & RMA'],
    svg: `
      <svg viewBox="0 0 280 128" class="w-full h-32" fill="none" xmlns="http://www.w3.org/2000/svg">
        <rect x="20" y="26" width="76" height="72" rx="10" fill="rgba(255,255,255,0.14)" stroke="#FFFFFF" stroke-width="1.5"/>
        <text x="58" y="54" text-anchor="middle" font-size="10" font-weight="800" fill="#FFFFFF">PDV LOJA</text>
        <text x="58" y="72" text-anchor="middle" font-size="8.5" font-weight="700" fill="#6EE7B7">NF-e / Pix</text>
        <path d="M98 62 H152" stroke="#FDE68A" stroke-width="2.5" class="tech-packet-dash"/>
        <g class="tech-float">
          <rect x="154" y="28" width="106" height="68" rx="12" fill="rgba(255,255,255,0.15)" stroke="#DDD6FE" stroke-width="1.5"/>
          <text x="207" y="54" text-anchor="middle" font-size="10" font-weight="800" fill="#FFFFFF">ESTOQUE &amp; RMA</text>
          <text x="207" y="72" text-anchor="middle" font-size="8.5" font-weight="700" fill="#A7F3D0">Serial Verificado</text>
        </g>
      </svg>
    `,
  },
  cyber: {
    titulo: 'Defesa Ativa, Zero Trust & 2FA',
    desc: 'Autenticação multifator TOTP, princípio do menor privilégio, cabeçalhos HSTS/CSP e conformidade CIS Controls v8.',
    badges: ['TOTP RFC 6238', 'CIS v8 / NIST', 'TLS 1.3'],
    svg: `
      <svg viewBox="0 0 280 128" class="w-full h-32" fill="none" xmlns="http://www.w3.org/2000/svg">
        <circle cx="140" cy="64" r="46" stroke="rgba(255,255,255,0.2)" stroke-width="1.5" stroke-dasharray="5 5"/>
        <circle cx="140" cy="64" r="34" fill="rgba(255,255,255,0.12)" stroke="#C4B5FD" stroke-width="1.5" class="tech-pulse"/>
        <g class="tech-float">
          <path d="M140 34 L164 44 V63 C164 78 153 89 140 94 C127 89 116 78 116 63 V44 L140 34Z" fill="rgba(124,58,237,0.7)" stroke="#FFFFFF" stroke-width="2"/>
          <path d="M131 63 L137 69 L150 56" stroke="#6EE7B7" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
        </g>
        <rect x="24" y="48" width="54" height="28" rx="8" fill="rgba(255,255,255,0.14)" stroke="rgba(255,255,255,0.3)"/>
        <text x="51" y="65" text-anchor="middle" font-size="9" font-weight="800" fill="#FDE68A">2FA TOTP</text>
        <rect x="202" y="48" width="54" height="28" rx="8" fill="rgba(255,255,255,0.14)" stroke="rgba(255,255,255,0.3)"/>
        <text x="229" y="65" text-anchor="middle" font-size="9" font-weight="800" fill="#6EE7B7">ZERO TRUST</text>
      </svg>
    `,
  },
  iot: {
    titulo: 'CFTV IP, IoT & Automação Segura',
    desc: 'Isolamento de câmeras e controladores em VLAN dedicada, desativação de UPnP/P2P inseguro e atualização de firmware.',
    badges: ['RTSP / ONVIF', 'VLAN CFTV', 'MQTT TLS'],
    svg: `
      <svg viewBox="0 0 280 128" class="w-full h-32" fill="none" xmlns="http://www.w3.org/2000/svg">
        <g class="tech-float">
          <rect x="30" y="36" width="76" height="44" rx="10" fill="rgba(255,255,255,0.15)" stroke="#FFFFFF" stroke-width="1.5"/>
          <circle cx="56" cy="58" r="10" fill="#1E1B4B" stroke="#C4B5FD" stroke-width="2"/>
          <circle cx="56" cy="58" r="4" fill="#6EE7B7" class="tech-pulse"/>
          <text x="86" y="61" text-anchor="middle" font-size="8" font-weight="800" fill="#FFFFFF">CAM</text>
        </g>
        <path d="M108 58 H164" stroke="#FDE68A" stroke-width="2.2" class="tech-packet-dash"/>
        <rect x="166" y="28" width="84" height="68" rx="12" fill="rgba(255,255,255,0.14)" stroke="#DDD6FE" stroke-width="1.5"/>
        <text x="208" y="52" text-anchor="middle" font-size="10" font-weight="800" fill="#FFFFFF">NVR / IoT</text>
        <rect x="180" y="62" width="56" height="18" rx="6" fill="rgba(16,185,129,0.28)"/>
        <text x="208" y="74" text-anchor="middle" font-size="8" font-weight="700" fill="#A7F3D0">VLAN ISOLADA</text>
      </svg>
    `,
  },
};

const LOGIN_IT_TIPS = [
  'Dica TI: Ative a verificação em duas etapas (2FA) no ícone 🔐 ao entrar para proteger sua conta mesmo se a senha vazar.',
  'Dica Redes: Nunca misture câmeras CFTV e dispositivos IoT na mesma VLAN dos servidores ou estações financeiras.',
  'Dica Loja TI: Sempre vincule o Número de Série / IMEI na venda para evitar fraudes na devolução e agilizar garantias RMA.',
  'Dica Hardware: Antes de trocar placa-mãe ou fonte em bancada, registre medidas de tensão e inspeção visual no checklist.',
  'Dica Backup: Mantenha ao menos 1 cópia offline ou imutável (regra 3-2-1-1-0) para resistir a ataques de ransomware.',
];

let loginTechTopicAtual = 'infra';
let loginTechTipIdx = 0;
let mfaSetupSecretAtual = '';
let mfaRecoveryCodesAtual = [];

function selecionarTemaLoginTI(temaKey) {
  const topic = LOGIN_TECH_TOPICS[temaKey] || LOGIN_TECH_TOPICS.infra;
  loginTechTopicAtual = temaKey;

  const stage = document.getElementById('loginTechStage');
  const title = document.getElementById('loginTechCardTitle');
  const desc = document.getElementById('loginTechCardDesc');
  const badges = document.getElementById('loginTechBadges');

  if (stage) stage.innerHTML = topic.svg;
  if (title) title.textContent = topic.titulo;
  if (desc) desc.textContent = topic.desc;
  if (badges) {
    badges.innerHTML = topic.badges
      .map(b => `<span class="px-2.5 py-1 rounded-lg bg-white/15 border border-white/20 text-[10px] font-bold text-white">${escapeHtml(b)}</span>`)
      .join('');
  }

  Object.keys(LOGIN_TECH_TOPICS).forEach(k => {
    const btn = document.getElementById(`btnLoginTech_${k}`);
    if (!btn) return;
    const active = k === temaKey;
    btn.className = active
      ? 'px-2.5 py-2 rounded-xl text-[11px] font-extrabold bg-white text-violet-800 shadow-md transition-all text-left flex items-center gap-1.5'
      : 'px-2.5 py-2 rounded-xl text-[11px] font-semibold bg-white/10 hover:bg-white/20 text-white/95 border border-white/15 transition-all text-left flex items-center gap-1.5';
  });
}

function proximaDicaLoginTI() {
  loginTechTipIdx = (loginTechTipIdx + 1) % LOGIN_IT_TIPS.length;
  const el = document.getElementById('loginTechTipText');
  if (el) el.textContent = LOGIN_IT_TIPS[loginTechTipIdx];
}

function inicializarPainelLoginTI() {
  if (!document.getElementById('loginTechStage')) return;
  selecionarTemaLoginTI('infra');
  const tipEl = document.getElementById('loginTechTipText');
  if (tipEl) tipEl.textContent = LOGIN_IT_TIPS[0];
}

document.addEventListener('DOMContentLoaded', inicializarPainelLoginTI);

// ==============================================================================
// UTILIDADES VISUAIS DE SENHA (MOSTRAR/OCULTAR & FORÇA DA SENHA)
// ==============================================================================

function toggleSenhaInput(inputId, btn) {
  const input = document.getElementById(inputId);
  if (!input) return;
  const mostrando = input.type === 'text';
  input.type = mostrando ? 'password' : 'text';
  if (btn) btn.textContent = mostrando ? '👁️' : '🙈';
}

function calcularNivelForcaSenha(s) {
  let pontos = 0;
  if (s.length >= 6) pontos++;
  if (s.length >= 10) pontos++;
  if (/[A-Z]/.test(s) && /[a-z]/.test(s)) pontos++;
  if (/\d/.test(s)) pontos++;
  if (/[^A-Za-z0-9]/.test(s)) pontos++;
  return pontos;
}

function atualizarForcaSenha(valor, barId = 'regSenhaForcaBar', txtId = 'regSenhaForcaTexto') {
  const bar = document.getElementById(barId);
  const txt = document.getElementById(txtId);
  if (!bar || !txt) return;

  const s = String(valor || '');
  if (!s) {
    bar.style.width = '0%';
    bar.className = 'h-full transition-all duration-300 bg-rose-500';
    txt.textContent = 'Mínimo 6 caracteres (use letras, números e símbolos)';
    return;
  }

  const pontos = calcularNivelForcaSenha(s);
  const pct = Math.min(100, Math.max(20, pontos * 20));
  bar.style.width = `${pct}%`;

  if (pontos <= 2) {
    bar.className = 'h-full transition-all duration-300 bg-rose-500';
    txt.textContent = 'Senha fraca — adicione números ou letras maiúsculas';
  } else if (pontos === 3 || pontos === 4) {
    bar.className = 'h-full transition-all duration-300 bg-amber-500';
    txt.textContent = 'Senha moderada — adicione símbolos para reforçar';
  } else {
    bar.className = 'h-full transition-all duration-300 bg-emerald-500';
    txt.textContent = 'Senha forte!';
  }
}

// ==============================================================================
// CENTRAL DE SEGURANÇA DO USUÁRIO (2FA TOTP, SESSÕES & AUDITORIA)
// ==============================================================================

async function abrirModalMinhaSeguranca() {
  const modal = document.getElementById('modalMinhaSeguranca');
  if (!modal) return;
  modal.classList.remove('hidden');
  document.getElementById('segRecoveryCodesBox')?.classList.add('hidden');
  document.getElementById('segQrBox')?.classList.add('hidden');
  await carregarStatusSegurancaModal();
}

function fecharModalMinhaSeguranca() {
  document.getElementById('modalMinhaSeguranca')?.classList.add('hidden');
}

async function carregarStatusSegurancaModal() {
  const res = await apiFetch('/auth/seguranca');
  if (!res.ok) {
    mostrarToast(await extrairErroResposta(res, 'Erro ao consultar configurações de segurança.'), 'erro');
    return;
  }
  const d = await res.json();

  const badge = document.getElementById('segMfaStatusBadge');
  const blocoAtivar = document.getElementById('segBlocoAtivarMfa');
  const blocoDesativar = document.getElementById('segBlocoDesativarMfa');
  const blocoSenhaLocal = document.getElementById('segBlocoAlterarSenhaLocal');
  const sessoesInfo = document.getElementById('segSessoesInfo');

  if (badge) {
    if (d.mfa_enabled) {
      badge.textContent = `🟢 2FA Ativo (${d.recovery_remaining ?? 0} códigos de backup)`;
      badge.className = 'px-2.5 py-1 rounded-full text-[11px] font-bold bg-emerald-100 dark:bg-emerald-950/60 text-emerald-700 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-800';
    } else {
      badge.textContent = d.exigir_mfa_equipe
        ? '🟠 Recomendado pela empresa (Desativado)'
        : '⚪ 2FA Desativado';
      badge.className = 'px-2.5 py-1 rounded-full text-[11px] font-bold bg-amber-100 dark:bg-amber-950/60 text-amber-800 dark:text-amber-300 border border-amber-200 dark:border-amber-800';
    }
  }

  blocoAtivar?.classList.toggle('hidden', Boolean(d.mfa_enabled));
  blocoDesativar?.classList.toggle('hidden', !d.mfa_enabled);
  blocoSenhaLocal?.classList.toggle('hidden', d.auth_mode === 'firebase');

  if (sessoesInfo) {
    sessoesInfo.textContent = `Versão ativa da sessão: v${d.session_version || 1}. Revogar encerra acessos abertos em outros navegadores.`;
  }

  const lista = document.getElementById('listaUltimosAcessos');
  const acessos = Array.isArray(d.ultimos_acessos) ? d.ultimos_acessos : [];
  if (lista) {
    lista.innerHTML = acessos.length === 0
      ? '<p class="text-slate-400">Nenhum evento de acesso registrado ainda.</p>'
      : acessos.map(a => {
          const isOk = a.status === 'Sucesso';
          return `
            <div class="p-2.5 rounded-xl bg-violet-50/70 dark:bg-[#1C1633] border border-violet-200/70 dark:border-violet-800/70 flex flex-wrap justify-between items-center gap-2 text-[11px]">
              <div>
                <span class="font-bold ${isOk ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400'}">${escapeHtml(a.status)}</span>
                <span class="text-slate-500 dark:text-violet-300/80">• ${escapeHtml(a.metodo || 'senha')} • IP ${escapeHtml(a.ip || '-')}</span>
                <div class="text-[10px] text-slate-400 truncate max-w-md">${escapeHtml(a.dispositivo || '')}</div>
              </div>
              <span class="text-[10px] font-semibold text-slate-500 dark:text-violet-300">${escapeHtml(formatarDataBR(a.data_hora))}</span>
            </div>
          `;
        }).join('');
  }
}

async function iniciarAtivacaoMfa() {
  const res = await apiFetch('/auth/mfa/setup', { method: 'POST' });
  if (!res.ok) {
    mostrarToast(await extrairErroResposta(res, 'Não foi possível gerar QR Code 2FA.'), 'erro');
    return;
  }
  const data = await res.json();
  mfaSetupSecretAtual = data.secret || '';
  const img = document.getElementById('mfaQrImg');
  const secTxt = document.getElementById('mfaSecretText');
  if (img) img.src = data.qr_svg_data_uri || '';
  if (secTxt) secTxt.textContent = mfaSetupSecretAtual;
  document.getElementById('segQrBox')?.classList.remove('hidden');
  document.getElementById('mfa_confirm_codigo')?.focus();
}

async function confirmarAtivacaoMfa(event) {
  event.preventDefault();
  const codigo = document.getElementById('mfa_confirm_codigo')?.value.trim() || '';
  const res = await apiFetch('/auth/mfa/confirm', {
    method: 'POST',
    body: JSON.stringify({ codigo }),
  });
  if (!res.ok) {
    mostrarToast(await extrairErroResposta(res, 'Código inválido. Tente novamente.'), 'erro');
    return;
  }
  const data = await res.json();
  if (data.mfa_token && typeof MFA_TOKEN_STORAGE_KEY === 'string') {
    localStorage.setItem(MFA_TOKEN_STORAGE_KEY, data.mfa_token);
  }
  if (sessaoAtual && data.user) {
    sessaoAtual.user = data.user;
    if (typeof atualizarIndicadorSegurancaHeader === 'function') {
      atualizarIndicadorSegurancaHeader();
    }
  }
  mfaRecoveryCodesAtual = Array.isArray(data.recovery_codes) ? data.recovery_codes : [];
  const recBox = document.getElementById('segRecoveryCodesBox');
  const recList = document.getElementById('segRecoveryCodesList');
  if (recList) {
    recList.innerHTML = mfaRecoveryCodesAtual
      .map(c => `<code class="px-2 py-1 rounded bg-white dark:bg-[#141024] border border-amber-300 dark:border-amber-700 font-mono text-[11px] font-bold text-center">${escapeHtml(c)}</code>`)
      .join('');
  }
  recBox?.classList.remove('hidden');
  document.getElementById('segQrBox')?.classList.add('hidden');
  if (document.getElementById('mfa_confirm_codigo')) {
    document.getElementById('mfa_confirm_codigo').value = '';
  }
  mostrarToast('Autenticação em duas etapas (2FA) ativada! Salve seus códigos de backup.', 'sucesso');
  await carregarStatusSegurancaModal();
}

async function desativarMfaUsuario(event) {
  event.preventDefault();
  const codigo = document.getElementById('mfa_disable_codigo')?.value.trim() || '';
  const res = await apiFetch('/auth/mfa/disable', {
    method: 'POST',
    body: JSON.stringify({ codigo }),
  });
  if (!res.ok) {
    mostrarToast(await extrairErroResposta(res, 'Código inválido para desativar 2FA.'), 'erro');
    return;
  }
  const data = await res.json();
  if (typeof MFA_TOKEN_STORAGE_KEY === 'string') {
    localStorage.removeItem(MFA_TOKEN_STORAGE_KEY);
  }
  if (sessaoAtual && data.user) {
    sessaoAtual.user = data.user;
    if (typeof atualizarIndicadorSegurancaHeader === 'function') {
      atualizarIndicadorSegurancaHeader();
    }
  }
  if (document.getElementById('mfa_disable_codigo')) {
    document.getElementById('mfa_disable_codigo').value = '';
  }
  mostrarToast('Autenticação 2FA desativada.', 'info');
  await carregarStatusSegurancaModal();
}

function copiarChaveMfa() {
  if (!mfaSetupSecretAtual) return;
  navigator.clipboard?.writeText(mfaSetupSecretAtual);
  mostrarToast('Chave secreta copiada!', 'sucesso');
}

function copiarCodigosRecuperacao() {
  if (!mfaRecoveryCodesAtual.length) return;
  navigator.clipboard?.writeText(mfaRecoveryCodesAtual.join('\n'));
  mostrarToast('Códigos de recuperação copiados! Guarde em local seguro.', 'sucesso');
}

async function revogarOutrasSessoes() {
  const res = await apiFetch('/auth/sessoes/revogar', { method: 'POST' });
  if (!res.ok) {
    mostrarToast(await extrairErroResposta(res, 'Erro ao revogar sessões.'), 'erro');
    return;
  }
  const data = await res.json();
  if (data.token && typeof TOKEN_STORAGE_KEY === 'string') {
    localStorage.setItem(TOKEN_STORAGE_KEY, data.token);
  }
  if (data.mfa_token && typeof MFA_TOKEN_STORAGE_KEY === 'string') {
    localStorage.setItem(MFA_TOKEN_STORAGE_KEY, data.mfa_token);
  }
  mostrarToast('Todas as sessões antigas foram desconectadas com sucesso!', 'sucesso');
  await carregarStatusSegurancaModal();
}

async function enviarAlterarSenhaLocal(event) {
  event.preventDefault();
  const senhaAtual = document.getElementById('seg_senha_atual')?.value || '';
  const novaSenha = document.getElementById('seg_nova_senha')?.value || '';
  const confirma = document.getElementById('seg_confirma_senha')?.value || '';
  if (novaSenha !== confirma) {
    mostrarToast('A confirmação da nova senha não confere.', 'erro');
    return;
  }

  const res = await apiFetch('/auth/alterar-senha', {
    method: 'POST',
    body: JSON.stringify({ senha_atual: senhaAtual, nova_senha: novaSenha }),
  });
  if (!res.ok) {
    mostrarToast(await extrairErroResposta(res, 'Erro ao alterar senha.'), 'erro');
    return;
  }
  const data = await res.json();
  if (data.token && typeof TOKEN_STORAGE_KEY === 'string') {
    localStorage.setItem(TOKEN_STORAGE_KEY, data.token);
  }
  event.target.reset();
  mostrarToast('Senha alterada e sessões anteriores revogadas!', 'sucesso');
  await carregarStatusSegurancaModal();
}
