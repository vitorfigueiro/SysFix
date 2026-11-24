# Deploy do SysFix SaaS 2.0 no Render

Este projeto já inclui o arquivo [`render.yaml`](render.yaml) configurado para publicar o backend FastAPI + frontend + Firebase em minutos.

---

## Passo 1 — Enviar o código para o GitHub (`vitorfigueiro/SysFix`)

No PowerShell, dentro da pasta `SysFix`, confira que `.env` e `sysfix-saas-firebase-adminsdk.json` **não** aparecem no `git status` e faça o commit/push para a branch `main`:

```powershell
cd c:\Users\Vitor\Documents\sistema\SysFix
git status -s
git add .
git commit -m "SysFix SaaS 2.0: dashboard, login multi-empresa e Firebase"
git push origin main
```

---

## Passo 2 — Criar o serviço no Render via Blueprint

1. Acesse https://dashboard.render.com/ e faça login com sua conta GitHub.
2. Clique em **New + → Blueprint**.
3. Selecione o repositório **`vitorfigueiro/SysFix`** (branch `main`).
4. O Render lerá automaticamente o arquivo `render.yaml` (`sysfix-saas`).

---

## Passo 3 — Preencher as 2 variáveis secretas no Render

O Render vai pedir os valores das duas variáveis marcadas com `sync: false`:

1. **`FIREBASE_WEB_API_KEY`**: copie o valor de `FIREBASE_WEB_API_KEY` do seu arquivo `.env` local.
2. **`FIREBASE_SERVICE_ACCOUNT_JSON`**: execute o comando abaixo no PowerShell para copiar a chave inteira em 1 linha direto para a área de transferência (sem exibir a chave na tela) e cole no campo do Render:

```powershell
cd c:\Users\Vitor\Documents\sistema\SysFix
.\.venv\Scripts\python.exe -c "import json, pathlib; print(json.dumps(json.loads(pathlib.Path('sysfix-saas-firebase-adminsdk.json').read_text(encoding='utf-8')), separators=(',', ':')))" | Set-Clipboard
```

Clique em **Apply** / **Deploy** e aguarde o build concluir (**Live**).

---

## Passo 4 — Testar e Autorizar o Domínio no Firebase

1. Teste a saúde da API acessando `https://<sua-url>.onrender.com/api/health` (deve retornar `"status": "online"` e `"backend": "firebase"`).
2. Abra o **Firebase Console** → **Authentication** → **Configurações** → **Domínios autorizados** → **Adicionar domínio** e adicione `<sua-url>.onrender.com` (sem `https://`).
3. Acesse `https://<sua-url>.onrender.com`, crie a primeira empresa na aba **Criar Empresa** e comece a usar.

---

## Dica para Comercialização (Plano Free vs. Pago)

- No plano **Free** do Render, o servidor hiberna após 15 minutos sem acessos e leva cerca de **30 a 50 segundos** para acordar na primeira requisição.
- Quando começar a vender para clientes pagantes, troque o serviço no Render para o plano **Starter (US$ 7/mês)** para ficar **24/7 online sem hibernação** e conecte seu domínio próprio (ex.: `app.sysfix.com.br`) com HTTPS automático.
