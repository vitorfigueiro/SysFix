const API_BASE = '/api';
const TOKEN_STORAGE_KEY = 'sysfix_auth_token';
const THEME_STORAGE_KEY = 'sysfix_theme';

let clientConfig = { auth_mode: 'local', storage_backend: 'local', firebase_web: null };
let firebaseClient = null;
let firebaseAuth = null;
let firebaseSdk = null;

let sessaoAtual = null;
let permissionsCatalog = [];
let usuariosEmpresaCache = [];
let uidPermissaoEmEdicao = null;
let listaEquipamentosCache = [];
let registroSelecionadoId = null;
let filtroAtual = 'nao_finalizados';
let visaoAtual = 'dashboard';
let enviandoRequisicao = false;

function obterTemaAtual() {
  try {
    const salvo = localStorage.getItem(THEME_STORAGE_KEY);
    if (salvo === 'dark' || salvo === 'light') return salvo;
  } catch (_) {}
  if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
    return 'dark';
  }
  return 'light';
}

function aplicarTema(tema) {
  const isDark = tema === 'dark';
  document.documentElement.classList.toggle('dark', isDark);
  try {
    localStorage.setItem(THEME_STORAGE_KEY, tema);
  } catch (_) {}

  const metaTheme = document.querySelector('meta[name="theme-color"]');
  if (metaTheme) {
    metaTheme.setAttribute('content', isDark ? '#141024' : '#7C3AED');
  }

  const labelAuth = isDark ? '☀️ Modo Claro' : '🌙 Modo Escuro';
  const labelHeader = isDark ? '☀️ Claro' : '🌙 Escuro';
  const btnAuth = document.getElementById('btnTemaAuth');
  const btnHeader = document.getElementById('btnTemaHeader');
  if (btnAuth) btnAuth.textContent = labelAuth;
  if (btnHeader) btnHeader.textContent = labelHeader;
}

function alternarTema() {
  const proximo = document.documentElement.classList.contains('dark') ? 'light' : 'dark';
  aplicarTema(proximo);
}

function obterDataHojeISO() {
  const hoje = new Date();
  const ano = hoje.getFullYear();
  const mes = String(hoje.getMonth() + 1).padStart(2, '0');
  const dia = String(hoje.getDate()).padStart(2, '0');
  return `${ano}-${mes}-${dia}`;
}

function formatarDataBR(dataStr) {
  if (!dataStr) return '---';
  const texto = String(dataStr).trim();
  if (!texto) return '---';
  if (texto.includes('/')) return texto;
  const partes = texto.slice(0, 10).split('-');
  if (partes.length === 3) {
    return `${partes[2]}/${partes[1]}/${partes[0]}`;
  }
  return texto;
}

function formatarMoedaBR(valor) {
  const numero = Number.parseFloat(valor);
  const valorSeguro = Number.isFinite(numero) ? numero : 0;
  return valorSeguro.toLocaleString('pt-BR', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  });
}

function escapeHtml(text) {
  if (text === null || text === undefined) return '';
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function mostrarToast(mensagem, tipo = 'sucesso') {
  const container = document.getElementById('toastContainer');
  if (!container) return;

  const toast = document.createElement('div');
  const estilos = {
    sucesso: 'bg-[#1C1633] text-emerald-300 border-emerald-500/40',
    erro: 'bg-[#1C1633] text-rose-300 border-rose-500/40',
    info: 'bg-[#1C1633] text-violet-200 border-violet-500/40'
  };
  const icones = { sucesso: '✨', erro: '🚨', info: 'ℹ️' };

  toast.className = `pointer-events-auto flex items-center gap-3 px-4 py-3 rounded-xl shadow-2xl border text-xs font-semibold transform transition-all duration-300 translate-y-4 opacity-0 ${estilos[tipo] || estilos.info}`;

  const iconSpan = document.createElement('span');
  iconSpan.className = 'text-sm';
  iconSpan.textContent = icones[tipo] || 'ℹ️';

  const msgSpan = document.createElement('span');
  msgSpan.textContent = String(mensagem ?? '');

  toast.appendChild(iconSpan);
  toast.appendChild(msgSpan);
  container.appendChild(toast);

  requestAnimationFrame(() => {
    toast.classList.remove('translate-y-4', 'opacity-0');
  });

  setTimeout(() => {
    toast.classList.add('translate-y-4', 'opacity-0');
    setTimeout(() => toast.remove(), 300);
  }, 3500);
}

function formatarMensagemErro(err) {
  if (!err) return 'Erro desconhecido no servidor.';
  if (typeof err.detail === 'string') return err.detail;
  if (Array.isArray(err.detail)) {
    return err.detail.map(e => {
      const campo = e.loc ? e.loc.filter(item => item !== 'body').join('.') : 'Campo';
      return `${campo}: ${e.msg}`;
    }).join(' | ');
  }
  if (err.detail && typeof err.detail === 'object') {
    if (err.detail.mensagem) return err.detail.mensagem;
    return JSON.stringify(err.detail);
  }
  if (err.mensagem) return err.mensagem;
  return JSON.stringify(err);
}

async function extrairErroResposta(res, fallbackMsg) {
  try {
    const err = await res.json();
    return formatarMensagemErro(err) || fallbackMsg;
  } catch (_) {
    return fallbackMsg;
  }
}

async function obterTokenAtivo() {
  if (clientConfig.auth_mode === 'firebase' && firebaseAuth && firebaseAuth.currentUser) {
    return await firebaseAuth.currentUser.getIdToken();
  }
  return localStorage.getItem(TOKEN_STORAGE_KEY);
}

async function apiFetch(path, options = {}) {
  const token = await obterTokenAtivo();
  const headers = new Headers(options.headers || {});
  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  if (options.body && !(options.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }

  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers
  });

  if (res.status === 401 && sessaoAtual) {
    await sairSistema(false);
    mostrarToast('Sessão expirada. Faça login novamente.', 'info');
  }
  return res;
}

// ==============================================================================
// INICIALIZAÇÃO E AUTENTICAÇÃO (FIREBASE SDK OU MODO LOCAL)
// ==============================================================================

document.addEventListener('DOMContentLoaded', async () => {
  aplicarTema(obterTemaAtual());
  const hoje = new Date();
  const hojeISO = obterDataHojeISO();
  document.getElementById('ent_data').value = hojeISO;
  document.getElementById('sai_data').value = hojeISO;
  document.getElementById('rel_ano').value = hoje.getFullYear();
  document.getElementById('rel_mes').value = hoje.getMonth() + 1;
  document.getElementById('dash_ano').value = hoje.getFullYear();
  document.getElementById('dash_mes').value = hoje.getMonth() + 1;

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      fecharModalRelatorio();
      fecharModalExcluir();
      fecharModalPermissoes();
    }
  });

  if (window.location.protocol === 'file:') {
    mostrarToast('Abra o SysFix pelo servidor http://localhost:8000, não como arquivo local.', 'erro');
    return;
  }

  await inicializarSistema();
});

async function inicializarSistema() {
  try {
    const res = await fetch(`${API_BASE}/config`);
    if (res.ok) {
      clientConfig = await res.json();
    }
  } catch (e) {
    console.error('Erro ao ler configuração:', e);
  }

  const badgeModo = document.getElementById('badgeModoAuth');
  if (clientConfig.auth_mode === 'firebase' && clientConfig.firebase_web) {
    badgeModo.textContent = '🔥 Firebase Cloud';
    document.getElementById('btnEsqueciSenha').classList.remove('hidden');
    await inicializarFirebaseSdk();
  } else {
    badgeModo.textContent = '💻 Modo Local';
    const token = localStorage.getItem(TOKEN_STORAGE_KEY);
    if (token) {
      await carregarSessao();
    } else {
      exibirTelaAuth();
    }
  }
}

async function inicializarFirebaseSdk() {
  try {
    const [appMod, authMod] = await Promise.all([
      import('https://www.gstatic.com/firebasejs/10.14.1/firebase-app.js'),
      import('https://www.gstatic.com/firebasejs/10.14.1/firebase-auth.js')
    ]);
    firebaseSdk = { ...appMod, ...authMod };
    firebaseClient = appMod.initializeApp(clientConfig.firebase_web);
    firebaseAuth = authMod.getAuth(firebaseClient);

    authMod.onAuthStateChanged(firebaseAuth, async (user) => {
      if (user) {
        await carregarSessao();
      } else {
        exibirTelaAuth();
      }
    });
  } catch (e) {
    console.error('Erro ao carregar Firebase SDK:', e);
    mostrarToast('Falha ao carregar SDK do Firebase. Verifique a conexão com a internet.', 'erro');
    exibirTelaAuth();
  }
}

function exibirTelaAuth() {
  sessaoAtual = null;
  document.getElementById('appShell').classList.add('hidden');
  document.getElementById('telaAuth').classList.remove('hidden');
  document.getElementById('cardOnboarding').classList.add('hidden');
  document.getElementById('cardLoginRegistro').classList.remove('hidden');
}

function alternarAuthTab(tab) {
  const isLogin = tab === 'login';
  document.getElementById('formLogin').classList.toggle('hidden', !isLogin);
  document.getElementById('formRegister').classList.toggle('hidden', isLogin);

  const active = 'py-2 text-xs font-bold rounded-lg bg-violet-600 text-white shadow-sm transition-all';
  const inactive = 'py-2 text-xs font-bold rounded-lg text-violet-700 dark:text-violet-300 hover:text-violet-900 dark:hover:text-white transition-all';

  document.getElementById('btnAuthTabLogin').className = isLogin ? active : inactive;
  document.getElementById('btnAuthTabRegister').className = !isLogin ? active : inactive;
}

async function carregarSessao() {
  try {
    const res = await apiFetch('/auth/me');
    if (res.status === 409) {
      const data = await res.json();
      const detail = data.detail || {};
      if (detail.codigo === 'ONBOARDING_REQUIRED') {
        document.getElementById('telaAuth').classList.remove('hidden');
        document.getElementById('appShell').classList.add('hidden');
        document.getElementById('cardLoginRegistro').classList.add('hidden');
        document.getElementById('cardOnboarding').classList.remove('hidden');
        document.getElementById('onboardEmailInfo').textContent =
          `Conta autenticada: ${detail.email || ''}. Informe sua empresa para iniciar.`;
        if (detail.nome) {
          document.getElementById('onb_nome').value = detail.nome;
        }
        return;
      }
    }

    if (!res.ok) {
      if (clientConfig.auth_mode !== 'firebase') {
        localStorage.removeItem(TOKEN_STORAGE_KEY);
      }
      exibirTelaAuth();
      return;
    }

    sessaoAtual = await res.json();
    permissionsCatalog = Array.isArray(sessaoAtual.permissions_catalog)
      ? sessaoAtual.permissions_catalog
      : [];
    aplicarSessaoNaUI();
  } catch (e) {
    console.error('Erro ao validar sessão:', e);
    exibirTelaAuth();
  }
}

function temPermissao(chave) {
  if (!sessaoAtual || !sessaoAtual.user) return false;
  if (sessaoAtual.user.role === 'admin') return true;
  const perms = sessaoAtual.user.permissoes || {};
  return Boolean(perms[chave]);
}

function aplicarPermissoesNaUI() {
  const podeDashboard = temPermissao('ver_dashboard');
  const podeCustos = temPermissao('ver_custos');
  const podeEntrar = temPermissao('registrar_entrada');
  const podeEditar = temPermissao('editar_entrada');
  const podeSaida = temPermissao('registrar_saida');
  const podeExcluir = temPermissao('excluir_registros');
  const podePdf = temPermissao('exportar_pdf');
  const podeEquipe = temPermissao('gerenciar_equipe');
  const podeBackup = temPermissao('importar_exportar_dados');

  document.getElementById('navBtnDashboard').classList.toggle('hidden', !podeDashboard);
  document.getElementById('navBtnEmpresa').classList.toggle('hidden', !podeEquipe && !podeBackup);
  document.getElementById('btnExportarPdfHeader').classList.toggle('hidden', !podePdf);

  document.getElementById('kpiCardCustoMes').classList.toggle('hidden', !podeCustos);
  document.getElementById('dashBlocoProporcaoCusto').classList.toggle('hidden', !podeCustos);

  document.getElementById('btnAdicionarItemEntrada').classList.toggle('hidden', !podeEntrar);
  document.getElementById('btnSalvarEntrada').classList.toggle('hidden', !podeEntrar);
  document.getElementById('btnAtualizarEntrada').classList.toggle('hidden', !podeEditar);
  document.getElementById('grupoCustoSaida').classList.toggle('hidden', !podeCustos);
  document.getElementById('fichaBlocoCusto').classList.toggle('hidden', !podeCustos);
  document.getElementById('btnExcluirItem').classList.toggle('hidden', !podeExcluir);

  const mostraAbaEntrada = podeEntrar || podeEditar;
  const mostraAbaSaida = podeSaida;
  document.getElementById('btnTabEntrada').classList.toggle('hidden', !mostraAbaEntrada);
  document.getElementById('btnTabSaida').classList.toggle('hidden', !mostraAbaSaida);
  document.getElementById('barraTabsFormulario').classList.toggle('hidden', !(mostraAbaEntrada && mostraAbaSaida));

  const temAlgumaAcaoPainel = mostraAbaEntrada || mostraAbaSaida || podeExcluir;
  document.getElementById('painelFormularios').classList.toggle('hidden', !temAlgumaAcaoPainel);

  if (!mostraAbaEntrada && mostraAbaSaida) {
    alternarTab('saida');
  } else if (mostraAbaEntrada) {
    alternarTab('entrada');
  }

  document.getElementById('blocoConfigEmpresa').classList.toggle('hidden', !podeEquipe);
  document.getElementById('blocoEquipeEmpresa').classList.toggle('hidden', !podeEquipe);
  document.getElementById('blocoImportExport').classList.toggle('hidden', !podeBackup);
}

function aplicarSessaoNaUI() {
  const user = sessaoAtual.user;
  const tenant = sessaoAtual.tenant;
  const isAdmin = user.role === 'admin';

  document.getElementById('telaAuth').classList.add('hidden');
  document.getElementById('appShell').classList.remove('hidden');

  document.getElementById('headerEmpresaNome').textContent = tenant.nome || 'SysFix';
  document.getElementById('headerPlanoBadge').textContent = (tenant.plano || 'pro').toUpperCase();
  const nomeExibicao = user.nome || user.email || 'US';
  document.getElementById('headerUsuarioNome').textContent = nomeExibicao;
  document.getElementById('headerUsuarioRole').textContent = isAdmin ? 'Administrador' : 'Técnico';
  const avatarEl = document.getElementById('headerUsuarioAvatar');
  if (avatarEl) {
    const iniciais = nomeExibicao
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map(p => p[0].toUpperCase())
      .join('') || 'US';
    avatarEl.textContent = iniciais;
  }

  popularOpcoesLocalizacaoEmpresa(tenant.localizacoes);
  aplicarPermissoesNaUI();
  renderizarCheckboxesPermissoesNovoMembro();
  alternarVisao(temPermissao('ver_dashboard') ? 'dashboard' : 'equipamentos');
  carregarLista('nao_finalizados');
}

function popularOpcoesLocalizacaoEmpresa(localizacoes) {
  const selectLoc = document.getElementById('ent_loc');
  const lista = Array.isArray(localizacoes) && localizacoes.length > 0
    ? localizacoes
    : ['Bancada TI', 'PlayLan', 'Central Informática', 'Aguardando Peça'];

  selectLoc.innerHTML = '';
  lista.forEach(loc => {
    const opt = document.createElement('option');
    opt.value = loc;
    opt.textContent = loc;
    selectLoc.appendChild(opt);
  });
}

async function enviarLogin(event) {
  event.preventDefault();
  const email = document.getElementById('login_email').value.trim();
  const senha = document.getElementById('login_senha').value;
  const btn = document.getElementById('btnEntrar');
  btn.disabled = true;

  try {
    if (clientConfig.auth_mode === 'firebase' && firebaseSdk && firebaseAuth) {
      await firebaseSdk.signInWithEmailAndPassword(firebaseAuth, email, senha);
      mostrarToast('Login realizado com sucesso!', 'sucesso');
      return;
    }

    const res = await fetch(`${API_BASE}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, senha })
    });
    if (!res.ok) {
      mostrarToast(await extrairErroResposta(res, 'E-mail ou senha inválidos.'), 'erro');
      return;
    }
    const data = await res.json();
    localStorage.setItem(TOKEN_STORAGE_KEY, data.token);
    sessaoAtual = { user: data.user, tenant: data.tenant };
    mostrarToast(`Bem-vindo(a), ${data.user.nome}!`, 'sucesso');
    aplicarSessaoNaUI();
  } catch (e) {
    mostrarToast(e.message || 'Falha ao entrar no sistema.', 'erro');
  } finally {
    btn.disabled = false;
  }
}

async function enviarCadastroEmpresa(event) {
  event.preventDefault();
  const nomeEmpresa = document.getElementById('reg_empresa').value.trim();
  const nomeUsuario = document.getElementById('reg_nome').value.trim();
  const email = document.getElementById('reg_email').value.trim();
  const senha = document.getElementById('reg_senha').value;
  const btn = document.getElementById('btnCadastrarEmpresa');
  btn.disabled = true;

  try {
    if (clientConfig.auth_mode === 'firebase' && firebaseSdk && firebaseAuth) {
      const cred = await firebaseSdk.createUserWithEmailAndPassword(firebaseAuth, email, senha);
      await firebaseSdk.updateProfile(cred.user, { displayName: nomeUsuario });
      const idToken = await cred.user.getIdToken();
      const res = await fetch(`${API_BASE}/auth/onboarding`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${idToken}`
        },
        body: JSON.stringify({ nome_empresa: nomeEmpresa, nome_usuario: nomeUsuario })
      });
      if (!res.ok) {
        mostrarToast(await extrairErroResposta(res, 'Não foi possível criar a empresa.'), 'erro');
        return;
      }
      sessaoAtual = await res.json();
      mostrarToast('Empresa cadastrada no Firebase com sucesso!', 'sucesso');
      aplicarSessaoNaUI();
      return;
    }

    const res = await fetch(`${API_BASE}/auth/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        nome_empresa: nomeEmpresa,
        nome_usuario: nomeUsuario,
        email,
        senha
      })
    });
    if (!res.ok) {
      mostrarToast(await extrairErroResposta(res, 'Não foi possível criar a conta.'), 'erro');
      return;
    }
    const data = await res.json();
    localStorage.setItem(TOKEN_STORAGE_KEY, data.token);
    sessaoAtual = { user: data.user, tenant: data.tenant };
    mostrarToast('Empresa criada com sucesso!', 'sucesso');
    aplicarSessaoNaUI();
  } catch (e) {
    mostrarToast(e.message || 'Erro ao criar empresa.', 'erro');
  } finally {
    btn.disabled = false;
  }
}

async function concluirOnboardingFirebase(event) {
  event.preventDefault();
  const nomeEmpresa = document.getElementById('onb_empresa').value.trim();
  const nomeUsuario = document.getElementById('onb_nome').value.trim();
  const btn = document.getElementById('btnConcluirOnboarding');
  btn.disabled = true;

  try {
    const res = await apiFetch('/auth/onboarding', {
      method: 'POST',
      body: JSON.stringify({ nome_empresa: nomeEmpresa, nome_usuario: nomeUsuario })
    });
    if (!res.ok) {
      mostrarToast(await extrairErroResposta(res, 'Erro ao configurar empresa.'), 'erro');
      return;
    }
    sessaoAtual = await res.json();
    mostrarToast('Empresa configurada com sucesso!', 'sucesso');
    aplicarSessaoNaUI();
  } finally {
    btn.disabled = false;
  }
}

async function recuperarSenhaFirebase() {
  const email = document.getElementById('login_email').value.trim();
  if (!email) {
    mostrarToast('Digite seu e-mail no campo acima para receber o link de redefinição.', 'info');
    return;
  }
  try {
    await firebaseSdk.sendPasswordResetEmail(firebaseAuth, email);
    mostrarToast('E-mail de redefinição enviado!', 'sucesso');
  } catch (e) {
    mostrarToast(e.message || 'Não foi possível enviar o e-mail de redefinição.', 'erro');
  }
}

async function sairSistema(notificar = true) {
  localStorage.removeItem(TOKEN_STORAGE_KEY);
  if (clientConfig.auth_mode === 'firebase' && firebaseSdk && firebaseAuth) {
    try {
      await firebaseSdk.signOut(firebaseAuth);
    } catch (_) {}
  }
  limparFormularios();
  exibirTelaAuth();
  if (notificar) {
    mostrarToast('Você saiu do sistema.', 'info');
  }
}

// ==============================================================================
// NAVEGAÇÃO ENTRE VISÕES E DASHBOARD
// ==============================================================================

function alternarVisao(visao) {
  visaoAtual = visao;
  document.getElementById('viewDashboard').classList.toggle('hidden', visao !== 'dashboard');
  document.getElementById('viewEquipamentos').classList.toggle('hidden', visao !== 'equipamentos');
  document.getElementById('viewEmpresa').classList.toggle('hidden', visao !== 'empresa');

  const active = 'px-3 py-1.5 text-xs font-bold rounded-lg bg-violet-600 text-white shadow-sm transition-all';
  const inactive = 'px-3 py-1.5 text-xs font-semibold rounded-lg text-violet-800 dark:text-violet-200 hover:bg-violet-100 dark:hover:bg-violet-900/40 transition-all';

  document.getElementById('navBtnDashboard').className = visao === 'dashboard' ? active : inactive;
  document.getElementById('navBtnEquipamentos').className = visao === 'equipamentos' ? active : inactive;
  document.getElementById('navBtnEmpresa').className = visao === 'empresa' ? active : inactive;

  if (visao === 'dashboard') {
    carregarDashboard();
  } else if (visao === 'empresa') {
    carregarTelaEmpresa();
  }
}

async function carregarDashboard() {
  const mes = document.getElementById('dash_mes').value;
  const ano = document.getElementById('dash_ano').value;

  try {
    const res = await apiFetch(`/dashboard?mes=${encodeURIComponent(mes)}&ano=${encodeURIComponent(ano)}`);
    if (!res.ok) {
      mostrarToast(await extrairErroResposta(res, 'Erro ao carregar dashboard.'), 'erro');
      return;
    }
    const d = await res.json();
    renderizarDashboard(d);
  } catch (e) {
    console.error('Erro no dashboard:', e);
    mostrarToast('Falha ao carregar indicadores do dashboard.', 'erro');
  }
}

function renderizarDashboard(d) {
  const r = d.resumo || {};
  const podeCustos = !d.ocultar_custos && temPermissao('ver_custos');
  document.getElementById('kpi_pendentes').textContent = r.total_pendentes ?? 0;
  document.getElementById('kpi_atrasados').textContent = r.total_atrasados ?? 0;
  document.getElementById('kpi_sla_label').textContent = `Acima de ${r.sla_dias ?? 3} dias`;
  document.getElementById('kpi_entregues').textContent = r.total_entregues ?? 0;
  document.getElementById('kpi_total_geral').textContent = `de ${r.total_registros ?? 0} registros`;
  document.getElementById('kpi_taxa').textContent = `${r.taxa_resolucao_percent ?? 0}%`;
  document.getElementById('kpi_tempo_medio').textContent = `${r.tempo_medio_resolucao_dias ?? 0}d`;
  if (podeCustos) {
    document.getElementById('kpi_custo_mes').textContent = `R$ ${formatarMoedaBR(r.custo_total_mes)}`;
    document.getElementById('kpi_custo_geral').textContent = `Total: R$ ${formatarMoedaBR(r.custo_total_geral)}`;
    document.getElementById('dashProporcaoCusto').textContent =
      `${r.qtd_com_custo ?? 0} com custo / ${r.qtd_sem_custo ?? 0} sem custo`;
  }

  // Gráfico de Evolução 6 meses
  const evoContainer = document.getElementById('dashEvolucaoGrafico');
  const evolucao = d.evolucao_mensal || [];
  const maxValor = Math.max(
    1,
    ...evolucao.flatMap(item => [item.entradas || 0, item.entregas || 0])
  );
  evoContainer.innerHTML = evolucao.map(item => {
    const hEnt = Math.max(4, Math.round(((item.entradas || 0) / maxValor) * 140));
    const hSai = Math.max(4, Math.round(((item.entregas || 0) / maxValor) * 140));
    const custoLinha = podeCustos
      ? `<span class="text-[10px] text-slate-400 dark:text-violet-300/70">R$ ${formatarMoedaBR(item.custo)}</span>`
      : '';
    return `
      <div class="flex-1 flex flex-col items-center justify-end gap-1.5 h-full">
        <div class="flex items-end gap-1.5 h-40">
          <div class="flex flex-col items-center gap-1">
            <span class="text-[10px] font-bold text-slate-600 dark:text-violet-200">${item.entradas}</span>
            <div class="w-4 sm:w-5 rounded-t bg-gradient-to-t from-violet-700 to-violet-400 transition-all" style="height:${hEnt}px" title="Entradas: ${item.entradas}"></div>
          </div>
          <div class="flex flex-col items-center gap-1">
            <span class="text-[10px] font-bold text-slate-600 dark:text-violet-200">${item.entregas}</span>
            <div class="w-4 sm:w-5 rounded-t bg-gradient-to-t from-emerald-600 to-emerald-400 transition-all" style="height:${hSai}px" title="Entregas: ${item.entregas}"></div>
          </div>
        </div>
        <span class="text-[11px] font-bold text-slate-700 dark:text-violet-100">${escapeHtml(item.label)}</span>
        ${custoLinha}
      </div>
    `;
  }).join('');

  // Localizações
  const locContainer = document.getElementById('dashLocalizacoes');
  const locs = d.por_localizacao || [];
  if (locs.length === 0) {
    locContainer.innerHTML = '<p class="text-xs text-slate-400 dark:text-violet-300/60">Nenhum equipamento pendente.</p>';
  } else {
    const maxLoc = Math.max(1, ...locs.map(l => l.quantidade));
    locContainer.innerHTML = locs.map(l => {
      const pct = Math.round((l.quantidade / maxLoc) * 100);
      return `
        <div class="space-y-1">
          <div class="flex justify-between text-xs">
            <span class="font-semibold text-slate-700 dark:text-violet-100">${escapeHtml(l.localizacao)}</span>
            <span class="font-bold text-violet-700 dark:text-violet-300">${l.quantidade}</span>
          </div>
          <div class="w-full bg-violet-100 dark:bg-[#1C1633] rounded-full h-2 overflow-hidden">
            <div class="bg-gradient-to-r from-violet-600 to-fuchsia-500 h-2 rounded-full" style="width:${pct}%"></div>
          </div>
        </div>
      `;
    }).join('');
  }

  // Ranking Técnicos
  const tecContainer = document.getElementById('dashTecnicos');
  const tecs = d.por_tecnico || [];
  tecContainer.innerHTML = tecs.length === 0
    ? '<p class="text-slate-400 dark:text-violet-300/60">Sem registros ainda.</p>'
    : tecs.map(t => `
      <div class="flex items-center justify-between p-2.5 rounded-xl bg-violet-50/70 dark:bg-[#1C1633] border border-violet-100 dark:border-violet-800/60">
        <span class="font-semibold text-slate-800 dark:text-violet-100">${escapeHtml(t.tecnico)}</span>
        <span class="text-[11px] text-slate-500 dark:text-violet-300">
          <b class="text-violet-600 dark:text-violet-400">${t.coletas}</b> col. / <b class="text-emerald-600 dark:text-emerald-400">${t.entregas}</b> ent.
        </span>
      </div>
    `).join('');

  // Top Origens
  const origContainer = document.getElementById('dashOrigens');
  const origens = d.top_origens || [];
  origContainer.innerHTML = origens.length === 0
    ? '<p class="text-slate-400 dark:text-violet-300/60">Sem registros ainda.</p>'
    : origens.map(o => `
      <div class="flex items-center justify-between p-2.5 rounded-xl bg-violet-50/70 dark:bg-[#1C1633] border border-violet-100 dark:border-violet-800/60">
        <span class="font-semibold text-slate-800 dark:text-violet-100 truncate pr-2">${escapeHtml(o.origem)}</span>
        <span class="text-[11px] font-bold text-violet-700 dark:text-violet-300 shrink-0">${o.quantidade}x${podeCustos ? ` (R$ ${formatarMoedaBR(o.custo)})` : ''}</span>
      </div>
    `).join('');

  // Alertas de Atraso
  const alertaContainer = document.getElementById('dashAlertasAtraso');
  const alertas = d.alertas_atraso || [];
  alertaContainer.innerHTML = alertas.length === 0
    ? '<p class="text-emerald-600 dark:text-emerald-400 font-semibold">✅ Nenhum equipamento acima do SLA!</p>'
    : alertas.map(a => `
      <button onclick="abrirItemDoDashboard(${a.id})" class="w-full text-left flex items-center justify-between p-2.5 rounded-xl bg-rose-50 dark:bg-rose-950/40 hover:bg-rose-100 dark:hover:bg-rose-900/50 border border-rose-200 dark:border-rose-800/70 transition-all">
        <div class="truncate pr-2">
          <div class="font-bold text-rose-900 dark:text-rose-200 truncate">#${a.id} ${escapeHtml(a.equipamento)}</div>
          <div class="text-[10px] text-rose-700 dark:text-rose-300/80 truncate">${escapeHtml(a.origem)} • ${escapeHtml(a.localizacao)}</div>
        </div>
        <span class="px-2 py-0.5 rounded-full bg-rose-600 text-white text-[10px] font-bold shrink-0">${a.dias_pendente}d</span>
      </button>
    `).join('');
}

async function abrirItemDoDashboard(id) {
  alternarVisao('equipamentos');
  await carregarLista('nao_finalizados');
  await selecionarItem(id);
}

// ==============================================================================
// GESTÃO DE EQUIPAMENTOS (LISTA, BUSCA, ENTRADA EM LOTE, SAÍDA, EXCLUSÃO)
// ==============================================================================

function converterDataParaInput(dataStr) {
  if (!dataStr) return obterDataHojeISO();
  const texto = String(dataStr).trim();
  if (texto.includes('/')) {
    const partes = texto.split('/');
    if (partes.length === 3) return `${partes[2]}-${partes[1].padStart(2, '0')}-${partes[0].padStart(2, '0')}`;
  }
  return texto.slice(0, 10);
}

function calcularDiasPendente(dataStr) {
  if (!dataStr) return 0;
  let dataColeta;
  if (dataStr.includes('/')) {
    const partes = dataStr.split('/');
    if (partes.length === 3) {
      dataColeta = new Date(partes[2], partes[1] - 1, partes[0]);
    }
  } else if (dataStr.includes('-')) {
    const partes = dataStr.split('-');
    if (partes.length === 3) {
      dataColeta = new Date(partes[0], partes[1] - 1, partes[2]);
    }
  } else {
    dataColeta = new Date(dataStr);
  }
  if (!dataColeta || isNaN(dataColeta.getTime())) return 0;

  const hoje = new Date();
  hoje.setHours(0, 0, 0, 0);
  dataColeta.setHours(0, 0, 0, 0);
  const diffDays = Math.floor((hoje.getTime() - dataColeta.getTime()) / (1000 * 60 * 60 * 24));
  return diffDays > 0 ? diffDays : 0;
}

function adicionarItemEntrada() {
  const container = document.getElementById('listaItensEntrada');
  const cards = container.querySelectorAll('.item-entrada-card');
  const index = cards.length + 1;

  const cardHtml = `
    <div class="item-entrada-card bg-white dark:bg-[#1C1633] p-3 rounded-xl border border-violet-200/80 dark:border-violet-900/70 shadow-sm space-y-2 relative">
      <div class="flex justify-between items-center border-b border-violet-100 dark:border-violet-800/70 pb-1.5">
        <span class="text-[11px] font-bold text-violet-700 dark:text-violet-300 num-item-label">Item #${index}</span>
        <button onclick="removerItemEntrada(this)" class="btn-remover-item text-rose-500 hover:text-rose-700 text-xs font-bold">🗑️ Remover</button>
      </div>
      <div>
        <label class="block text-[11px] font-semibold text-slate-600 dark:text-violet-200 mb-1">Equipamento *</label>
        <input type="text" class="ent_equip w-full px-2.5 py-1.5 text-xs border border-violet-200 dark:border-violet-800 rounded-lg focus:ring-2 focus:ring-violet-400/40 focus:border-violet-500 outline-none" placeholder="Ex: Monitor Dell 24">
      </div>
      <div>
        <label class="block text-[11px] font-semibold text-slate-600 dark:text-violet-200 mb-1">Tombamento *</label>
        <input type="text" class="ent_tomb w-full px-2.5 py-1.5 text-xs border border-violet-200 dark:border-violet-800 rounded-lg focus:ring-2 focus:ring-violet-400/40 focus:border-violet-500 outline-none" placeholder="Ex: 654321">
      </div>
      <div>
        <label class="block text-[11px] font-semibold text-slate-600 dark:text-violet-200 mb-1">Problema Relatado</label>
        <textarea class="ent_prob w-full px-2.5 py-1.5 text-xs border border-violet-200 dark:border-violet-800 rounded-lg focus:ring-2 focus:ring-violet-400/40 focus:border-violet-500 outline-none resize-none" rows="2" placeholder="Descreva a falha..."></textarea>
      </div>
    </div>
  `;

  container.insertAdjacentHTML('beforeend', cardHtml);
  atualizarContadoresERemover();
}

function removerItemEntrada(btn) {
  const card = btn.closest('.item-entrada-card');
  if (card) {
    card.remove();
    atualizarContadoresERemover();
  }
}

function atualizarContadoresERemover(rotuloPersonalizado = null) {
  const container = document.getElementById('listaItensEntrada');
  const cards = container.querySelectorAll('.item-entrada-card');
  document.getElementById('countItens').innerText = cards.length;

  cards.forEach((card, i) => {
    const label = card.querySelector('.num-item-label');
    if (label) {
      label.innerText = rotuloPersonalizado && cards.length === 1
        ? rotuloPersonalizado
        : `Item #${i + 1}`;
    }
    const btnRemove = card.querySelector('.btn-remover-item');
    if (btnRemove) {
      btnRemove.classList.toggle('hidden', cards.length === 1);
    }
  });
}

function alternarTab(tab) {
  const isEntrada = tab === 'entrada';
  document.getElementById('formEntradaContainer').classList.toggle('hidden', !isEntrada);
  document.getElementById('formSaidaContainer').classList.toggle('hidden', isEntrada);

  const active = 'py-2 px-3 text-xs font-bold rounded-xl transition-all bg-violet-600 text-white shadow-sm';
  const inactive = 'py-2 px-3 text-xs font-bold rounded-xl transition-all text-violet-700 dark:text-violet-300 hover:bg-violet-100 dark:hover:bg-violet-900/40';

  document.getElementById('btnTabEntrada').className = isEntrada ? active : inactive;
  document.getElementById('btnTabSaida').className = !isEntrada ? active : inactive;
}

function alternarCampoCusto() {
  const isComCusto = document.getElementById('sai_status_custo').value === 'Com Custo';
  const campo = document.getElementById('sai_valor');
  campo.disabled = !isComCusto;
  if (!isComCusto) {
    campo.value = '0.00';
  }
  campo.className = isComCusto
    ? 'w-full px-3 py-2 text-xs border border-violet-200 dark:border-violet-800 rounded-xl focus:ring-2 focus:ring-violet-400/40 focus:border-violet-500 outline-none transition-all bg-white dark:bg-[#1A1430] text-slate-900 dark:text-white'
    : 'w-full px-3 py-2 text-xs border border-violet-200 dark:border-violet-800 rounded-xl bg-slate-50 dark:bg-[#120E21] text-slate-400 outline-none transition-all';
}

function atualizarBotoesFiltro(filtro) {
  const activeClass = 'px-2.5 py-1 text-[11px] font-bold bg-violet-600 text-white rounded-lg shadow-sm transition-all';
  const inactiveClass = 'px-2.5 py-1 text-[11px] font-semibold bg-white dark:bg-[#141024] border border-violet-200 dark:border-violet-800 rounded-lg text-violet-800 dark:text-violet-200 hover:bg-violet-100 dark:hover:bg-violet-900/40 transition-all';

  document.getElementById('btnFiltroPendentes').className = filtro === 'nao_finalizados' ? activeClass : inactiveClass;
  document.getElementById('btnFiltroEntregues').className = filtro === 'finalizados' ? activeClass : inactiveClass;
  document.getElementById('btnFiltroTodos').className = filtro === 'todos' ? activeClass : inactiveClass;
}

function limparOpcoesLegadasLocalizacao() {
  document.getElementById('ent_loc').querySelectorAll('option[data-legacy="true"]').forEach(opt => opt.remove());
}

function definirLocalizacaoSelect(localizacao) {
  const selectLoc = document.getElementById('ent_loc');
  limparOpcoesLegadasLocalizacao();
  const valor = localizacao || (selectLoc.options[0] ? selectLoc.options[0].value : 'Bancada TI');
  const existe = Array.from(selectLoc.options).some(opt => opt.value === valor);
  if (!existe) {
    const option = document.createElement('option');
    option.value = valor;
    option.textContent = `${valor} (legado)`;
    option.dataset.legacy = 'true';
    selectLoc.appendChild(option);
  }
  selectLoc.value = valor;
}

function limparFormularios() {
  registroSelecionadoId = null;
  const hojeISO = obterDataHojeISO();

  document.getElementById('ent_tec').value = '';
  document.getElementById('ent_data').value = hojeISO;
  document.getElementById('ent_origem').value = '';
  document.getElementById('ent_os').value = '';
  limparOpcoesLegadasLocalizacao();
  const selectLoc = document.getElementById('ent_loc');
  if (selectLoc.options.length > 0) {
    selectLoc.selectedIndex = 0;
  }

  document.getElementById('listaItensEntrada').innerHTML = `
    <div class="item-entrada-card bg-white dark:bg-[#1C1633] p-3 rounded-xl border border-violet-200/80 dark:border-violet-900/70 shadow-sm space-y-2 relative">
      <div class="flex justify-between items-center border-b border-violet-100 dark:border-violet-800/70 pb-1.5">
        <span class="text-[11px] font-bold text-violet-700 dark:text-violet-300 num-item-label">Item #1</span>
        <button onclick="removerItemEntrada(this)" class="btn-remover-item text-rose-500 hover:text-rose-700 text-xs font-bold hidden">🗑️ Remover</button>
      </div>
      <div>
        <label class="block text-[11px] font-semibold text-slate-600 dark:text-violet-200 mb-1">Equipamento *</label>
        <input type="text" class="ent_equip w-full px-2.5 py-1.5 text-xs border border-violet-200 dark:border-violet-800 rounded-lg focus:ring-2 focus:ring-violet-400/40 focus:border-violet-500 outline-none" placeholder="Ex: Notebook Dell Vostro">
      </div>
      <div>
        <label class="block text-[11px] font-semibold text-slate-600 dark:text-violet-200 mb-1">Tombamento *</label>
        <input type="text" class="ent_tomb w-full px-2.5 py-1.5 text-xs border border-violet-200 dark:border-violet-800 rounded-lg focus:ring-2 focus:ring-violet-400/40 focus:border-violet-500 outline-none" placeholder="Ex: 123456">
      </div>
      <div>
        <label class="block text-[11px] font-semibold text-slate-600 dark:text-violet-200 mb-1">Problema Relatado</label>
        <textarea class="ent_prob w-full px-2.5 py-1.5 text-xs border border-violet-200 dark:border-violet-800 rounded-lg focus:ring-2 focus:ring-violet-400/40 focus:border-violet-500 outline-none resize-none" rows="2" placeholder="Descreva a falha..."></textarea>
      </div>
    </div>
  `;
  atualizarContadoresERemover();

  document.getElementById('sai_tec').value = '';
  document.getElementById('sai_os').value = '';
  document.getElementById('sai_data').value = hojeISO;
  document.getElementById('sai_laudado').value = 'Não';
  document.getElementById('sai_status_custo').value = 'Sem Custo';
  document.getElementById('sai_valor').value = '0.00';
  document.getElementById('sai_resolucao').value = '';
  alternarCampoCusto();

  const badge = document.getElementById('badgeStatusSelecao');
  badge.className = 'p-3 bg-rose-50 dark:bg-rose-950/40 border border-rose-200 dark:border-rose-800 rounded-xl text-xs font-semibold text-rose-600 dark:text-rose-300 flex items-center gap-2';
  badge.innerText = '⚠️ Nenhum item selecionado na lista';

  document.getElementById('fichaConteudo').classList.add('hidden');
  document.getElementById('fichaVazia').classList.remove('hidden');
  destacarLinhaSelecionada();
}

function destacarLinhaSelecionada() {
  document.querySelectorAll('#tabelaCorpo tr[data-id]').forEach(tr => {
    const ehSelecionada = Number(tr.dataset.id) === registroSelecionadoId;
    tr.classList.toggle('ring-2', ehSelecionada);
    tr.classList.toggle('ring-inset', ehSelecionada);
    tr.classList.toggle('ring-violet-500', ehSelecionada);
  });
}

function atualizarDatalists(dados) {
  const tecnicos = new Set();
  const origens = new Set();
  dados.forEach(item => {
    if (item.tecnico_coleta) tecnicos.add(item.tecnico_coleta.trim());
    if (item.tecnico_entrega) tecnicos.add(item.tecnico_entrega.trim());
    if (item.origem) origens.add(item.origem.trim());
  });

  const dlTec = document.getElementById('datalistTecnicos');
  dlTec.innerHTML = '';
  Array.from(tecnicos).sort().forEach(t => {
    const opt = document.createElement('option');
    opt.value = t;
    dlTec.appendChild(opt);
  });

  const dlOrig = document.getElementById('datalistOrigens');
  dlOrig.innerHTML = '';
  Array.from(origens).sort().forEach(o => {
    const opt = document.createElement('option');
    opt.value = o;
    dlOrig.appendChild(opt);
  });
}

async function carregarLista(filtro = filtroAtual) {
  filtroAtual = filtro;
  atualizarBotoesFiltro(filtro);

  try {
    const res = await apiFetch(`/equipamentos?status=${encodeURIComponent(filtro)}`);
    if (!res.ok) {
      mostrarToast(await extrairErroResposta(res, 'Erro ao carregar equipamentos.'), 'erro');
      return;
    }
    const dados = await res.json();
    listaEquipamentosCache = Array.isArray(dados) ? dados : [];
    atualizarDatalists(listaEquipamentosCache);
    filtrarTabelaLocal();
  } catch (e) {
    console.error('Erro ao carregar lista:', e);
    mostrarToast('Não foi possível conectar ao servidor SysFix.', 'erro');
  }
}

function filtrarTabelaLocal() {
  const termo = (document.getElementById('inputBuscaLista').value || '').trim().toLowerCase();
  const slaDias = Number(sessaoAtual?.tenant?.sla_dias || 3);

  const filtrados = !termo
    ? listaEquipamentosCache
    : listaEquipamentosCache.filter(item =>
        [
          item.id,
          item.equipamento,
          item.tombamento,
          item.os_coleta,
          item.os_entrega,
          item.origem,
          item.localizacao,
          item.tecnico_coleta,
          item.tecnico_entrega
        ].some(val => String(val || '').toLowerCase().includes(termo))
      );

  document.getElementById('listaContagem').textContent = filtrados.length;
  const tbody = document.getElementById('tabelaCorpo');
  tbody.innerHTML = '';

  if (filtrados.length === 0) {
    tbody.innerHTML = '<tr><td colspan="4" class="p-4 text-center text-slate-400 dark:text-violet-300/60">Nenhum equipamento encontrado.</td></tr>';
    return;
  }

  filtrados.forEach(item => {
    const tr = document.createElement('tr');
    tr.dataset.id = item.id;
    const statusLower = (item.status || '').toLowerCase().trim();
    const isEntregue = statusLower === 'entregue' || statusLower === 'finalizado' || (item.data_entrega && item.data_entrega.trim() !== '');
    const diasPendente = !isEntregue ? calcularDiasPendente(item.data_coleta) : 0;
    const estaAtrasado = !isEntregue && diasPendente > slaDias;

    tr.className = estaAtrasado
      ? 'bg-rose-50/90 dark:bg-rose-950/35 hover:bg-rose-100/80 dark:hover:bg-rose-900/45 border-l-4 border-l-rose-500 cursor-pointer transition-colors border-b border-rose-100 dark:border-rose-900/50'
      : 'hover:bg-violet-50/70 dark:hover:bg-[#1C1633] cursor-pointer transition-colors border-b border-violet-100 dark:border-violet-900/40';

    tr.onclick = (e) => {
      e.stopPropagation();
      selecionarItem(item.id);
    };

    let badge = '';
    if (isEntregue) {
      badge = '<span class="px-2 py-0.5 text-[10px] font-bold rounded-full bg-emerald-50 dark:bg-emerald-950/60 text-emerald-600 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-700">Entregue</span>';
    } else if (estaAtrasado) {
      badge = `<span class="px-2 py-0.5 text-[10px] font-bold rounded-full bg-rose-100 dark:bg-rose-950/70 text-rose-700 dark:text-rose-300 border border-rose-300 dark:border-rose-700">🚨 ${diasPendente} dias</span>`;
    } else {
      badge = `<span class="px-2 py-0.5 text-[10px] font-bold rounded-full bg-violet-100 dark:bg-violet-950/70 text-violet-700 dark:text-violet-300 border border-violet-200 dark:border-violet-700">Pendente (${diasPendente}d)</span>`;
    }

    tr.innerHTML = `
      <td class="p-2.5 font-bold ${estaAtrasado ? 'text-rose-700 dark:text-rose-300' : 'text-violet-600 dark:text-violet-400'}">#${item.id}</td>
      <td class="p-2.5 font-semibold ${estaAtrasado ? 'text-rose-900 dark:text-rose-100' : 'text-slate-800 dark:text-white'}">${escapeHtml(item.equipamento)}</td>
      <td class="p-2.5 ${estaAtrasado ? 'text-rose-700 dark:text-rose-300' : 'text-slate-600 dark:text-violet-200'}">${escapeHtml(item.tombamento || '-')}</td>
      <td class="p-2.5">${badge}</td>
    `;
    tbody.appendChild(tr);
  });
  destacarLinhaSelecionada();
}

async function selecionarItem(id) {
  try {
    const res = await apiFetch(`/equipamentos/${id}`);
    if (!res.ok) {
      mostrarToast(await extrairErroResposta(res, 'Não foi possível carregar os detalhes do registro.'), 'erro');
      return;
    }
    const d = await res.json();
    registroSelecionadoId = d.id;
    destacarLinhaSelecionada();

    document.getElementById('ent_tec').value = d.tecnico_coleta || d.tecnico || '';
    document.getElementById('ent_data').value = converterDataParaInput(d.data_coleta);
    document.getElementById('ent_origem').value = d.origem || '';
    document.getElementById('ent_os').value = d.os_coleta || '';
    definirLocalizacaoSelect(d.localizacao);

    document.getElementById('listaItensEntrada').innerHTML = `
      <div class="item-entrada-card bg-white dark:bg-[#1C1633] p-3 rounded-xl border border-violet-200/80 dark:border-violet-900/70 shadow-sm space-y-2 relative">
        <div class="flex justify-between items-center border-b border-violet-100 dark:border-violet-800/70 pb-1.5">
          <span class="text-[11px] font-bold text-violet-700 dark:text-violet-300 num-item-label">Registro #${d.id}</span>
          <button onclick="removerItemEntrada(this)" class="btn-remover-item text-rose-500 hover:text-rose-700 text-xs font-bold hidden">🗑️ Remover</button>
        </div>
        <div>
          <label class="block text-[11px] font-semibold text-slate-600 dark:text-violet-200 mb-1">Equipamento *</label>
          <input type="text" class="ent_equip w-full px-2.5 py-1.5 text-xs border border-violet-200 dark:border-violet-800 rounded-lg focus:ring-2 focus:ring-violet-400/40 focus:border-violet-500 outline-none" value="${escapeHtml(d.equipamento || '')}">
        </div>
        <div>
          <label class="block text-[11px] font-semibold text-slate-600 dark:text-violet-200 mb-1">Tombamento *</label>
          <input type="text" class="ent_tomb w-full px-2.5 py-1.5 text-xs border border-violet-200 dark:border-violet-800 rounded-lg focus:ring-2 focus:ring-violet-400/40 focus:border-violet-500 outline-none" value="${escapeHtml(d.tombamento || '')}">
        </div>
        <div>
          <label class="block text-[11px] font-semibold text-slate-600 dark:text-violet-200 mb-1">Problema Relatado</label>
          <textarea class="ent_prob w-full px-2.5 py-1.5 text-xs border border-violet-200 dark:border-violet-800 rounded-lg focus:ring-2 focus:ring-violet-400/40 focus:border-violet-500 outline-none resize-none" rows="2">${escapeHtml(d.problema || '')}</textarea>
        </div>
      </div>
    `;
    atualizarContadoresERemover(`Registro #${d.id}`);

    document.getElementById('sai_tec').value = d.tecnico_entrega || '';
    document.getElementById('sai_os').value = d.os_entrega || '';
    document.getElementById('sai_data').value = converterDataParaInput(d.data_entrega);
    document.getElementById('sai_laudado').value = d.laudado || 'Não';
    document.getElementById('sai_status_custo').value = d.status_custo || 'Sem Custo';
    document.getElementById('sai_valor').value = d.valor_custo || d.valor || '0.00';
    document.getElementById('sai_resolucao').value = d.resolucao || d.servico_realizado || '';
    alternarCampoCusto();

    const statusLower = (d.status || '').toLowerCase().trim();
    const isEntregue = statusLower === 'entregue' || statusLower === 'finalizado' || (d.data_entrega && d.data_entrega.trim() !== '');
    const slaDias = Number(sessaoAtual?.tenant?.sla_dias || 3);

    const badge = document.getElementById('badgeStatusSelecao');
    if (isEntregue) {
      badge.className = 'p-3 bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-800 rounded-xl text-xs font-semibold text-emerald-700 dark:text-emerald-300 flex items-center gap-2';
      badge.innerText = `✅ Item #${d.id} selecionado (Entregue)`;
    } else {
      badge.className = 'p-3 bg-violet-50 dark:bg-violet-950/50 border border-violet-200 dark:border-violet-800 rounded-xl text-xs font-semibold text-violet-700 dark:text-violet-200 flex items-center gap-2';
      badge.innerText = `📦 Item #${d.id} selecionado para saída`;
    }

    document.getElementById('f_id').innerText = `REGISTRO #${d.id}`;
    document.getElementById('f_equipamento').innerText = d.equipamento || '---';
    document.getElementById('f_tombamento').innerText = `Tombamento: ${d.tombamento || '---'}`;

    const fBadge = document.getElementById('f_badge_status');
    if (isEntregue) {
      fBadge.className = 'px-3 py-1 text-xs font-bold rounded-full bg-emerald-50 dark:bg-emerald-950/60 text-emerald-600 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-700';
      fBadge.innerText = '✅ ENTREGUE';
    } else {
      const dias = calcularDiasPendente(d.data_coleta);
      if (dias > slaDias) {
        fBadge.className = 'px-3 py-1 text-xs font-bold rounded-full bg-rose-50 dark:bg-rose-950/60 text-rose-600 dark:text-rose-300 border border-rose-200 dark:border-rose-700';
        fBadge.innerText = `🚨 ATRASADO (${dias}d)`;
      } else {
        fBadge.className = 'px-3 py-1 text-xs font-bold rounded-full bg-violet-100 dark:bg-violet-950/70 text-violet-700 dark:text-violet-300 border border-violet-200 dark:border-violet-700';
        fBadge.innerText = `⏳ PENDENTE (${dias}d)`;
      }
    }

    document.getElementById('f_tec_coleta').innerText = d.tecnico_coleta || d.tecnico || '---';
    document.getElementById('f_data_coleta').innerText = formatarDataBR(d.data_coleta);
    document.getElementById('f_os_coleta').innerText = d.os_coleta || '---';
    document.getElementById('f_origem').innerText = d.origem || '---';
    document.getElementById('f_localizacao').innerText = d.localizacao || '---';
    document.getElementById('f_problema').innerText = d.problema || 'Nenhum detalhe relatado.';

    document.getElementById('f_tec_entrega').innerText = d.tecnico_entrega || '---';
    document.getElementById('f_data_entrega').innerText = formatarDataBR(d.data_entrega);
    document.getElementById('f_os_entrega').innerText = d.os_entrega || '---';
    document.getElementById('f_custo').innerText = d.status_custo === 'Com Custo'
      ? `Com Custo (R$ ${formatarMoedaBR(d.valor_custo)})`
      : (d.status_custo || '---');
    document.getElementById('f_laudado').innerText = d.laudado || '---';
    document.getElementById('f_resolucao').innerText = d.resolucao || d.servico_realizado || 'Pendente de atendimento.';

    document.getElementById('fichaVazia').classList.add('hidden');
    document.getElementById('fichaConteudo').classList.remove('hidden');
  } catch (e) {
    console.error('Erro ao carregar detalhes:', e);
    mostrarToast('Não foi possível carregar os detalhes do registro.', 'erro');
  }
}

async function salvarEntrada() {
  if (enviandoRequisicao) return;

  const tec = document.getElementById('ent_tec').value.trim();
  const dataColeta = document.getElementById('ent_data').value;
  const origem = document.getElementById('ent_origem').value.trim();
  const osColeta = document.getElementById('ent_os').value.trim();
  const localizacao = document.getElementById('ent_loc').value;

  const cards = document.querySelectorAll('.item-entrada-card');
  const itensParaSalvar = [];

  for (let i = 0; i < cards.length; i++) {
    const card = cards[i];
    const equip = card.querySelector('.ent_equip').value.trim();
    const tomb = card.querySelector('.ent_tomb').value.trim();
    const prob = card.querySelector('.ent_prob').value.trim();

    if (!equip && (tomb || prob)) {
      mostrarToast(`Item #${i + 1}: preencha o nome do equipamento.`, 'erro');
      return;
    }

    if (equip) {
      itensParaSalvar.push({
        equipamento: equip,
        tombamento: tomb,
        tecnico_coleta: tec,
        data_coleta: dataColeta,
        origem,
        os_coleta: osColeta,
        localizacao,
        problema: prob
      });
    }
  }

  if (itensParaSalvar.length === 0) {
    mostrarToast('Preencha o nome de pelo menos 1 equipamento para cadastrar.', 'erro');
    return;
  }

  const btn = document.getElementById('btnSalvarEntrada');
  enviandoRequisicao = true;
  btn.disabled = true;

  try {
    const res = await apiFetch('/equipamentos', {
      method: 'POST',
      body: JSON.stringify(itensParaSalvar)
    });

    if (!res.ok) {
      mostrarToast(await extrairErroResposta(res, 'Erro ao registrar entrada.'), 'erro');
      return;
    }

    const resultado = await res.json();
    mostrarToast(resultado.mensagem || 'Entrada registrada com sucesso!', 'sucesso');
    limparFormularios();
    carregarLista();
  } catch (e) {
    console.error('Erro ao salvar entrada:', e);
    mostrarToast('Falha na conexão ao tentar registrar entrada.', 'erro');
  } finally {
    enviandoRequisicao = false;
    btn.disabled = false;
  }
}

async function atualizarEntradaAdmin() {
  if (enviandoRequisicao) return;
  if (!registroSelecionadoId) {
    mostrarToast('Selecione um equipamento na lista para atualizar.', 'erro');
    return;
  }

  const card = document.querySelector('.item-entrada-card');
  const equip = card ? card.querySelector('.ent_equip').value.trim() : '';
  const tomb = card ? card.querySelector('.ent_tomb').value.trim() : '';
  const prob = card ? card.querySelector('.ent_prob').value.trim() : '';

  const payload = {
    equipamento: equip,
    tombamento: tomb,
    tecnico_coleta: document.getElementById('ent_tec').value.trim(),
    data_coleta: document.getElementById('ent_data').value,
    origem: document.getElementById('ent_origem').value.trim(),
    os_coleta: document.getElementById('ent_os').value.trim(),
    localizacao: document.getElementById('ent_loc').value,
    problema: prob
  };

  if (!payload.equipamento) {
    mostrarToast('Preencha o nome do equipamento para atualizar.', 'erro');
    return;
  }

  const btn = document.getElementById('btnAtualizarEntrada');
  enviandoRequisicao = true;
  btn.disabled = true;

  try {
    const res = await apiFetch(`/equipamentos/${registroSelecionadoId}`, {
      method: 'PUT',
      body: JSON.stringify(payload)
    });

    if (!res.ok) {
      mostrarToast(await extrairErroResposta(res, 'Não foi possível atualizar o registro.'), 'erro');
      return;
    }

    mostrarToast(`Dados do item #${registroSelecionadoId} atualizados!`, 'sucesso');
    await carregarLista();
    await selecionarItem(registroSelecionadoId);
  } catch (e) {
    console.error('Erro ao atualizar registro:', e);
    mostrarToast('Falha de conexão ao tentar atualizar o registro.', 'erro');
  } finally {
    enviandoRequisicao = false;
    btn.disabled = false;
  }
}

async function salvarSaida() {
  if (enviandoRequisicao) return;
  if (!registroSelecionadoId) {
    mostrarToast('Selecione um equipamento na lista para dar saída.', 'erro');
    return;
  }

  const statusCusto = document.getElementById('sai_status_custo').value;
  const valorNumerico = statusCusto === 'Com Custo'
    ? (Number.parseFloat(document.getElementById('sai_valor').value) || 0.0)
    : 0.0;

  const payload = {
    tecnico_entrega: document.getElementById('sai_tec').value.trim(),
    os_entrega: document.getElementById('sai_os').value.trim(),
    data_entrega: document.getElementById('sai_data').value,
    laudado: document.getElementById('sai_laudado').value,
    status_custo: statusCusto,
    valor_custo: valorNumerico,
    resolucao: document.getElementById('sai_resolucao').value.trim()
  };

  const btn = document.getElementById('btnSalvarSaida');
  enviandoRequisicao = true;
  btn.disabled = true;

  try {
    const res = await apiFetch(`/equipamentos/${registroSelecionadoId}/saida`, {
      method: 'PUT',
      body: JSON.stringify(payload)
    });

    if (!res.ok) {
      mostrarToast(await extrairErroResposta(res, 'Não foi possível registrar a saída.'), 'erro');
      return;
    }

    mostrarToast(`Saída finalizada! Item #${registroSelecionadoId} marcado como Entregue.`, 'sucesso');
    limparFormularios();
    carregarLista();
  } catch (e) {
    console.error('Erro ao registrar saída:', e);
    mostrarToast('Falha de conexão ao registrar a saída do item.', 'erro');
  } finally {
    enviandoRequisicao = false;
    btn.disabled = false;
  }
}

function excluirRegistroAdmin() {
  if (!registroSelecionadoId) {
    mostrarToast('Selecione um registro na lista para excluir.', 'erro');
    return;
  }
  document.getElementById('del_descricao').innerText =
    `Deseja excluir permanentemente o registro #${registroSelecionadoId}? Esta ação não pode ser desfeita.`;
  document.getElementById('modalExcluir').classList.remove('hidden');
}

function fecharModalExcluir() {
  document.getElementById('modalExcluir').classList.add('hidden');
}

async function confirmarExclusaoAdmin() {
  if (enviandoRequisicao || !registroSelecionadoId) return;

  const idAlvo = registroSelecionadoId;
  const btn = document.getElementById('btnConfirmarExclusao');
  enviandoRequisicao = true;
  btn.disabled = true;

  try {
    const res = await apiFetch(`/equipamentos/${idAlvo}`, {
      method: 'DELETE'
    });

    if (!res.ok) {
      mostrarToast(await extrairErroResposta(res, 'Não foi possível excluir o registro.'), 'erro');
      fecharModalExcluir();
      return;
    }

    fecharModalExcluir();
    mostrarToast(`Registro #${idAlvo} removido do sistema.`, 'info');
    limparFormularios();
    carregarLista();
  } catch (e) {
    console.error('Erro ao excluir registro:', e);
    mostrarToast('Erro ao tentar conectar com o servidor.', 'erro');
  } finally {
    enviandoRequisicao = false;
    btn.disabled = false;
  }
}

// ==============================================================================
// RELATÓRIOS PDF AUTENTICADOS
// ==============================================================================

function abrirModalRelatorio() {
  alternarCamposRelatorio();
  document.getElementById('modalRelatorio').classList.remove('hidden');
}

function fecharModalRelatorio() {
  document.getElementById('modalRelatorio').classList.add('hidden');
}

function alternarCamposRelatorio() {
  const tipo = document.getElementById('rel_tipo').value;
  document.getElementById('grupoPeriodoRelatorio').classList.toggle('hidden', tipo !== 'mensal');
}

async function baixarRelatorioPDF() {
  const tipo = document.getElementById('rel_tipo').value;
  const mes = document.getElementById('rel_mes').value;
  const ano = Number.parseInt(document.getElementById('rel_ano').value, 10);
  const anonimizar = document.getElementById('rel_anonimizar').checked ? 'true' : 'false';

  let url = `/relatorio/pdf?tipo=${encodeURIComponent(tipo)}&anonimizar=${anonimizar}`;
  if (tipo === 'mensal') {
    if (!Number.isInteger(ano) || ano < 2000 || ano > 2100) {
      mostrarToast('Informe um ano válido entre 2000 e 2100.', 'erro');
      return;
    }
    url += `&mes=${encodeURIComponent(mes)}&ano=${encodeURIComponent(ano)}`;
  }

  const btn = document.getElementById('btnBaixarPdf');
  btn.disabled = true;
  try {
    const res = await apiFetch(url);
    if (!res.ok) {
      mostrarToast(await extrairErroResposta(res, 'Erro ao gerar relatório PDF.'), 'erro');
      return;
    }
    const blob = await res.blob();
    const blobUrl = URL.createObjectURL(blob);
    window.open(blobUrl, '_blank');
    setTimeout(() => URL.revokeObjectURL(blobUrl), 60000);
    fecharModalRelatorio();
    mostrarToast('Relatório PDF gerado!', 'sucesso');
  } catch (e) {
    mostrarToast('Falha ao baixar o relatório PDF.', 'erro');
  } finally {
    btn.disabled = false;
  }
}

// ==============================================================================
// GESTÃO DE EQUIPE E CONFIGURAÇÕES DA EMPRESA (ADMIN)
// ==============================================================================

async function carregarTelaEmpresa() {
  if (!sessaoAtual) return;
  const tenant = sessaoAtual.tenant;
  if (temPermissao('gerenciar_equipe')) {
    document.getElementById('emp_nome').value = tenant.nome || '';
    document.getElementById('emp_sla').value = tenant.sla_dias || 3;
    document.getElementById('emp_locs').value = (tenant.localizacoes || []).join('\n');
    renderizarCheckboxesPermissoesNovoMembro();
    await carregarUsuariosEmpresa();
  }
}

function obterCatalogoPermissoes() {
  if (permissionsCatalog.length > 0) return permissionsCatalog;
  return [
    { key: 'ver_dashboard', label: 'Ver Dashboard', descricao: 'Indicadores e SLA', default_tecnico: true },
    { key: 'ver_custos', label: 'Ver e Informar Custos (R$)', descricao: 'Valores financeiros nas O.S.', default_tecnico: true },
    { key: 'registrar_entrada', label: 'Registrar Entradas', descricao: 'Cadastrar novos itens', default_tecnico: true },
    { key: 'editar_entrada', label: 'Editar Entradas', descricao: 'Alterar dados da coleta', default_tecnico: true },
    { key: 'registrar_saida', label: 'Dar Saída / Entregar', descricao: 'Finalizar O.S.', default_tecnico: true },
    { key: 'excluir_registros', label: 'Excluir Registros', descricao: 'Remover itens do banco', default_tecnico: false },
    { key: 'exportar_pdf', label: 'Exportar Relatórios PDF', descricao: 'Gerar PDF da empresa', default_tecnico: true },
    { key: 'gerenciar_equipe', label: 'Gerenciar Equipe e Empresa', descricao: 'Configurações e usuários', default_tecnico: false },
    { key: 'importar_exportar_dados', label: 'Importar / Exportar Backup', descricao: 'Planilhas e JSON', default_tecnico: false }
  ];
}

function renderizarCheckboxesPermissoesNovoMembro() {
  const container = document.getElementById('listaPermissoesNovoMembro');
  if (!container) return;
  const catalogo = obterCatalogoPermissoes();
  container.innerHTML = catalogo.map(item => `
    <label class="flex items-start gap-2 p-2 rounded-lg bg-white dark:bg-[#141024] border border-violet-200/80 dark:border-violet-800/70 cursor-pointer text-[11px]">
      <input type="checkbox" class="new-mem-perm mt-0.5 rounded border-violet-300 text-violet-600 focus:ring-violet-400" data-key="${escapeHtml(item.key)}" ${item.default_tecnico ? 'checked' : ''}>
      <span class="leading-tight">
        <span class="font-bold text-slate-800 dark:text-white block">${escapeHtml(item.label)}</span>
        <span class="text-[10px] text-slate-500 dark:text-violet-300/70">${escapeHtml(item.descricao || '')}</span>
      </span>
    </label>
  `).join('');
  alternarPermissoesNovoMembro();
}

function alternarPermissoesNovoMembro() {
  const role = document.getElementById('mem_role')?.value || 'tecnico';
  const box = document.getElementById('grupoPermissoesNovoMembro');
  if (box) {
    box.classList.toggle('hidden', role === 'admin');
  }
}

async function salvarConfiguracoesEmpresa(event) {
  event.preventDefault();
  const nome = document.getElementById('emp_nome').value.trim();
  const slaDias = Number.parseInt(document.getElementById('emp_sla').value, 10);
  const localizacoes = document.getElementById('emp_locs').value
    .split(/\r?\n|,/)
    .map(s => s.trim())
    .filter(Boolean);

  try {
    const res = await apiFetch('/empresa/configuracoes', {
      method: 'PUT',
      body: JSON.stringify({ nome, sla_dias: slaDias, localizacoes })
    });
    if (!res.ok) {
      mostrarToast(await extrairErroResposta(res, 'Erro ao salvar configurações.'), 'erro');
      return;
    }
    const data = await res.json();
    sessaoAtual.tenant = data.tenant;
    document.getElementById('headerEmpresaNome').textContent = data.tenant.nome;
    popularOpcoesLocalizacaoEmpresa(data.tenant.localizacoes);
    mostrarToast('Configurações da empresa atualizadas!', 'sucesso');
  } catch (e) {
    mostrarToast('Falha de conexão ao salvar configurações.', 'erro');
  }
}

async function carregarUsuariosEmpresa() {
  try {
    const res = await apiFetch('/empresa/usuarios');
    if (!res.ok) {
      mostrarToast(await extrairErroResposta(res, 'Erro ao listar equipe.'), 'erro');
      return;
    }
    const usuarios = await res.json();
    usuariosEmpresaCache = Array.isArray(usuarios) ? usuarios : [];
    const tbody = document.getElementById('tabelaUsuariosEmpresa');
    tbody.innerHTML = '';

    const totalCatalogo = obterCatalogoPermissoes().length;

    usuariosEmpresaCache.forEach(u => {
      const tr = document.createElement('tr');
      const ehProprio = u.uid === sessaoAtual.user.uid;
      const isTargetAdmin = u.role === 'admin';
      const podeAlterarRole = sessaoAtual.user.role === 'admin' && !ehProprio;

      const perms = u.permissoes || {};
      const qtdAtivas = isTargetAdmin
        ? totalCatalogo
        : obterCatalogoPermissoes().filter(p => Boolean(perms[p.key])).length;

      const permColHtml = isTargetAdmin
        ? '<span class="px-2 py-0.5 text-[10px] font-bold rounded-full bg-violet-100 dark:bg-violet-900/70 text-violet-700 dark:text-violet-200">Acesso Total</span>'
        : `<button type="button" onclick="abrirModalPermissoes('${escapeHtml(u.uid)}')" class="px-2.5 py-1 text-[11px] font-bold rounded-lg bg-violet-50 dark:bg-[#1C1633] text-violet-700 dark:text-violet-200 border border-violet-200 dark:border-violet-800 hover:bg-violet-100">🔐 ${qtdAtivas}/${totalCatalogo}</button>`;

      const statusBadge = u.ativo
        ? '<span class="px-2 py-0.5 text-[10px] font-bold rounded-full bg-emerald-50 dark:bg-emerald-950/60 text-emerald-700 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-700">Ativo</span>'
        : '<span class="px-2 py-0.5 text-[10px] font-bold rounded-full bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-300 border border-slate-200 dark:border-slate-700">Inativo</span>';

      tr.innerHTML = `
        <td class="p-3 font-semibold text-slate-800 dark:text-white">${escapeHtml(u.nome)} ${ehProprio ? '<span class="text-[10px] text-violet-600 dark:text-violet-400">(Você)</span>' : ''}</td>
        <td class="p-3 text-slate-600 dark:text-violet-200">${escapeHtml(u.email)}</td>
        <td class="p-3">
          <select ${podeAlterarRole ? '' : 'disabled'} onchange="alterarPapelUsuario('${escapeHtml(u.uid)}', this.value)" class="px-2 py-1 text-xs border border-violet-200 dark:border-violet-800 rounded-lg bg-white dark:bg-[#1A1430]">
            <option value="tecnico" ${u.role === 'tecnico' ? 'selected' : ''}>Técnico</option>
            <option value="admin" ${u.role === 'admin' ? 'selected' : ''}>Administrador</option>
          </select>
        </td>
        <td class="p-3">${permColHtml}</td>
        <td class="p-3">${statusBadge}</td>
        <td class="p-3 text-right">
          ${(ehProprio || (isTargetAdmin && sessaoAtual.user.role !== 'admin')) ? '' : `
            <button onclick="alternarStatusUsuario('${escapeHtml(u.uid)}', ${!u.ativo})" class="px-2.5 py-1 text-[11px] font-semibold rounded-lg border ${u.ativo ? 'bg-rose-50 dark:bg-rose-950/50 text-rose-600 dark:text-rose-300 border-rose-200 dark:border-rose-800 hover:bg-rose-100' : 'bg-emerald-50 dark:bg-emerald-950/50 text-emerald-700 dark:text-emerald-300 border-emerald-200 dark:border-emerald-800 hover:bg-emerald-100'}">
              ${u.ativo ? 'Desativar' : 'Reativar'}
            </button>
          `}
        </td>
      `;
      tbody.appendChild(tr);
    });
  } catch (e) {
    mostrarToast('Erro ao carregar lista de colaboradores.', 'erro');
  }
}

async function adicionarMembroEquipe(event) {
  event.preventDefault();
  const role = document.getElementById('mem_role').value;
  const permissoes = {};
  if (role !== 'admin') {
    document.querySelectorAll('.new-mem-perm').forEach(cb => {
      permissoes[cb.dataset.key] = cb.checked;
    });
  }

  const payload = {
    nome: document.getElementById('mem_nome').value.trim(),
    email: document.getElementById('mem_email').value.trim(),
    senha: document.getElementById('mem_senha').value,
    role,
    permissoes: role === 'admin' ? null : permissoes
  };

  try {
    const res = await apiFetch('/empresa/usuarios', {
      method: 'POST',
      body: JSON.stringify(payload)
    });
    if (!res.ok) {
      mostrarToast(await extrairErroResposta(res, 'Erro ao cadastrar colaborador.'), 'erro');
      return;
    }
    document.getElementById('mem_nome').value = '';
    document.getElementById('mem_email').value = '';
    document.getElementById('mem_senha').value = '';
    renderizarCheckboxesPermissoesNovoMembro();
    mostrarToast('Colaborador adicionado à equipe!', 'sucesso');
    await carregarUsuariosEmpresa();
  } catch (e) {
    mostrarToast('Falha ao adicionar colaborador.', 'erro');
  }
}

function abrirModalPermissoes(uid) {
  const usuario = usuariosEmpresaCache.find(u => u.uid === uid);
  if (!usuario) return;
  uidPermissaoEmEdicao = uid;

  document.getElementById('perm_usuario_info').textContent =
    `Editando acessos de ${usuario.nome} (${usuario.email}).`;

  const permsAtuais = usuario.permissoes || {};
  const container = document.getElementById('listaPermissoesModal');
  container.innerHTML = obterCatalogoPermissoes().map(item => `
    <label class="flex items-start gap-2.5 p-3 rounded-xl bg-violet-50/70 dark:bg-[#1C1633] border border-violet-200/80 dark:border-violet-800/70 cursor-pointer text-xs">
      <input type="checkbox" class="modal-mem-perm mt-0.5 rounded border-violet-300 text-violet-600 focus:ring-violet-400" data-key="${escapeHtml(item.key)}" ${permsAtuais[item.key] ? 'checked' : ''}>
      <span class="leading-snug">
        <span class="font-bold text-slate-900 dark:text-white block">${escapeHtml(item.label)}</span>
        <span class="text-[11px] text-slate-500 dark:text-violet-300/80">${escapeHtml(item.descricao || '')}</span>
      </span>
    </label>
  `).join('');

  document.getElementById('modalPermissoes').classList.remove('hidden');
}

function fecharModalPermissoes() {
  uidPermissaoEmEdicao = null;
  document.getElementById('modalPermissoes').classList.add('hidden');
}

function aplicarPresetPermissoes(preset) {
  const semCustosSet = new Set(['registrar_entrada', 'editar_entrada', 'registrar_saida', 'exportar_pdf']);
  const consultaSet = new Set(['ver_dashboard', 'exportar_pdf']);
  const catalogo = obterCatalogoPermissoes();

  document.querySelectorAll('.modal-mem-perm').forEach(cb => {
    const key = cb.dataset.key;
    if (preset === 'padrao') {
      const item = catalogo.find(c => c.key === key);
      cb.checked = Boolean(item?.default_tecnico);
    } else if (preset === 'sem_custos') {
      cb.checked = semCustosSet.has(key);
    } else if (preset === 'consulta') {
      cb.checked = consultaSet.has(key);
    }
  });
}

async function salvarPermissoesUsuario() {
  if (!uidPermissaoEmEdicao) return;
  const permissoes = {};
  document.querySelectorAll('.modal-mem-perm').forEach(cb => {
    permissoes[cb.dataset.key] = cb.checked;
  });

  const btn = document.getElementById('btnSalvarPermissoes');
  btn.disabled = true;
  try {
    const res = await apiFetch(`/empresa/usuarios/${encodeURIComponent(uidPermissaoEmEdicao)}`, {
      method: 'PATCH',
      body: JSON.stringify({ permissoes })
    });
    if (!res.ok) {
      mostrarToast(await extrairErroResposta(res, 'Não foi possível salvar permissões.'), 'erro');
      return;
    }
    fecharModalPermissoes();
    mostrarToast('Permissões atualizadas com sucesso!', 'sucesso');
    await carregarUsuariosEmpresa();
  } catch (e) {
    mostrarToast('Falha ao salvar permissões.', 'erro');
  } finally {
    btn.disabled = false;
  }
}

async function alterarPapelUsuario(uid, role) {
  const res = await apiFetch(`/empresa/usuarios/${encodeURIComponent(uid)}`, {
    method: 'PATCH',
    body: JSON.stringify({ role })
  });
  if (!res.ok) {
    mostrarToast(await extrairErroResposta(res, 'Não foi possível alterar o papel.'), 'erro');
    await carregarUsuariosEmpresa();
    return;
  }
  mostrarToast('Papel atualizado!', 'sucesso');
}

async function alternarStatusUsuario(uid, ativo) {
  const res = await apiFetch(`/empresa/usuarios/${encodeURIComponent(uid)}`, {
    method: 'PATCH',
    body: JSON.stringify({ ativo })
  });
  if (!res.ok) {
    mostrarToast(await extrairErroResposta(res, 'Não foi possível alterar o status.'), 'erro');
    return;
  }
  mostrarToast(ativo ? 'Usuário reativado!' : 'Usuário desativado!', 'info');
  await carregarUsuariosEmpresa();
}

// ==============================================================================
// IMPORTAÇÃO (JSON, CSV, XLSX) E BACKUP DE DADOS
// ==============================================================================

function lerArquivoBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const resultado = String(reader.result || '');
      const idx = resultado.indexOf(',');
      resolve(idx >= 0 ? resultado.slice(idx + 1) : resultado);
    };
    reader.onerror = () => reject(new Error('Não foi possível ler o arquivo selecionado.'));
    reader.readAsDataURL(file);
  });
}

function renderizarResultadoImportacao(data) {
  const box = document.getElementById('imp_resultado');
  box.classList.remove('hidden');

  const erros = Array.isArray(data.erros) ? data.erros : [];
  const amostra = Array.isArray(data.amostra) ? data.amostra : [];
  const titulo = data.simulado
    ? `Pré-visualização (${escapeHtml((data.formato || '').toUpperCase())})`
    : `Importação concluída (${escapeHtml((data.formato || '').toUpperCase())})`;

  const resumo = data.simulado
    ? `${data.total_validos ?? 0} registro(s) válido(s) de ${data.total_lidos ?? 0} linha(s) lida(s).`
    : `${data.importados ?? 0} registro(s) importado(s).`;

  const errosHtml = erros.length > 0
    ? `<div class="p-2.5 rounded-xl bg-amber-50 dark:bg-amber-950/40 border border-amber-200 dark:border-amber-800 text-amber-800 dark:text-amber-200 space-y-1">
         <div class="font-bold">Avisos / linhas ignoradas (${erros.length}):</div>
         <ul class="list-disc pl-4 space-y-0.5">${erros.map(e => `<li>${escapeHtml(e)}</li>`).join('')}</ul>
       </div>`
    : '';

  const amostraHtml = amostra.length > 0
    ? `<div class="overflow-x-auto bg-white dark:bg-[#141024] rounded-xl border border-violet-200 dark:border-violet-800">
         <table class="w-full text-left text-[11px] border-collapse">
           <thead class="bg-violet-50 dark:bg-[#1C1633] text-violet-900 dark:text-violet-200 font-bold">
             <tr>
               <th class="p-2">ID Orig.</th>
               <th class="p-2">Equipamento</th>
               <th class="p-2">Tombamento</th>
               <th class="p-2">Origem</th>
               <th class="p-2">Data</th>
               <th class="p-2">Status</th>
             </tr>
           </thead>
           <tbody class="divide-y divide-violet-100 dark:divide-violet-900/50">
             ${amostra.map(item => `
               <tr>
                 <td class="p-2 text-violet-600 dark:text-violet-400 font-semibold">${escapeHtml(item.id ?? '-')}</td>
                 <td class="p-2 font-semibold text-slate-800 dark:text-white">${escapeHtml(item.equipamento)}</td>
                 <td class="p-2 text-slate-600 dark:text-violet-200">${escapeHtml(item.tombamento)}</td>
                 <td class="p-2 text-slate-600 dark:text-violet-200">${escapeHtml(item.origem)}</td>
                 <td class="p-2 text-slate-600 dark:text-violet-200">${escapeHtml(formatarDataBR(item.data_coleta))}</td>
                 <td class="p-2 font-semibold text-slate-800 dark:text-white">${escapeHtml(item.status)}</td>
               </tr>
             `).join('')}
           </tbody>
         </table>
       </div>`
    : '';

  box.innerHTML = `
    <div class="flex flex-wrap items-center justify-between gap-2">
      <span class="font-bold text-slate-900 dark:text-white">${titulo}</span>
      <span class="font-semibold text-violet-700 dark:text-violet-300">${escapeHtml(resumo)}</span>
    </div>
    ${errosHtml}
    ${amostraHtml}
  `;
}

async function executarImportacao(simular) {
  const input = document.getElementById('imp_arquivo');
  const arquivo = input.files && input.files[0];
  if (!arquivo) {
    mostrarToast('Selecione um arquivo .json, .csv ou .xlsx primeiro.', 'erro');
    return;
  }
  if (arquivo.size > 5 * 1024 * 1024) {
    mostrarToast('O arquivo ultrapassa o limite máximo de 5 MB.', 'erro');
    return;
  }

  const modo = document.getElementById('imp_modo').value;
  const btnPrev = document.getElementById('btnPreviewImport');
  const btnExec = document.getElementById('btnExecutarImport');
  btnPrev.disabled = true;
  btnExec.disabled = true;

  try {
    const conteudoBase64 = await lerArquivoBase64(arquivo);
    const res = await apiFetch('/empresa/importar', {
      method: 'POST',
      body: JSON.stringify({
        filename: arquivo.name,
        conteudo_base64: conteudoBase64,
        modo,
        simular
      })
    });

    if (!res.ok) {
      mostrarToast(await extrairErroResposta(res, 'Falha ao processar arquivo para importação.'), 'erro');
      return;
    }

    const data = await res.json();
    renderizarResultadoImportacao(data);

    if (simular) {
      mostrarToast(`Pré-visualização pronta: ${data.total_validos} registro(s) válido(s).`, 'info');
    } else {
      mostrarToast(data.mensagem || 'Dados importados com sucesso!', 'sucesso');
      input.value = '';
      await carregarLista();
      await carregarDashboard();
    }
  } catch (e) {
    mostrarToast(e.message || 'Erro ao enviar arquivo de importação.', 'erro');
  } finally {
    btnPrev.disabled = false;
    btnExec.disabled = false;
  }
}

async function baixarArquivoAutenticado(path, nomePadrao) {
  try {
    const res = await apiFetch(path);
    if (!res.ok) {
      mostrarToast(await extrairErroResposta(res, 'Erro ao baixar arquivo.'), 'erro');
      return;
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = nomePadrao;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  } catch (e) {
    mostrarToast('Falha ao baixar o arquivo.', 'erro');
  }
}

function baixarModeloImportacao(formato) {
  const nome = formato === 'json' ? 'modelo_importacao_sysfix.json' : 'modelo_importacao_sysfix.csv';
  baixarArquivoAutenticado(`/empresa/modelo-importacao?formato=${encodeURIComponent(formato)}`, nome);
}

function exportarDadosEmpresa(formato) {
  const nome = formato === 'csv' ? 'backup_sysfix.csv' : 'backup_sysfix.json';
  baixarArquivoAutenticado(`/empresa/exportar?formato=${encodeURIComponent(formato)}`, nome);
}
