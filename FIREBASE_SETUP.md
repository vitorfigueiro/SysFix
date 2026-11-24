# Guia de Configuração do Firebase no SysFix SaaS 2.0

O **SysFix 2.0** funciona em dois modos:
- **Modo Local (padrão sem chaves):** usa armazenamento local em JSON (`coletas.json` e `auth_db.json`) com login por e-mail/senha, ideal para testar imediatamente ou rodar offline.
- **Modo Firebase Cloud:** usa **Firebase Authentication** para login/cadastro/redefinição de senha e **Cloud Firestore** para guardar os dados de todas as empresas isolados em nuvem.

Siga os passos abaixo para ativar o Firebase:

---

## 1. Criar o Projeto no Firebase

1. Acesse o [Firebase Console](https://console.firebase.google.com/) e clique em **Adicionar projeto**.
2. Dê um nome ao projeto (ex.: `sysfix-saas`) e conclua a criação.

## 2. Ativar o Firebase Authentication (E-mail e Senha)

1. No menu lateral, clique em **Criação > Authentication**.
2. Clique em **Vamos começar**.
3. Na aba **Método de login**, ative **E-mail/senha** (primeira opção) e clique em **Salvar**.

## 3. Criar o Cloud Firestore e Aplicar as Regras

1. No menu lateral, clique em **Criação > Firestore Database**.
2. Clique em **Criar banco de dados**.
3. Escolha a região mais próxima (ex.: `southamerica-east1` — São Paulo) e inicie em **Modo de produção**.
4. Na aba **Regras**, cole o conteúdo do arquivo [`firestore.rules`](firestore.rules) e clique em **Publicar**:
   - Toda leitura e gravação é feita pelo backend FastAPI via Firebase Admin SDK, garantindo isolamento multi-empresa e verificação de papel (`admin` / `tecnico`).

## 4. Obter as Chaves do Web App (Frontend)

1. No Firebase Console, clique na engrenagem **Configurações do projeto** (ao lado de "Visão geral do projeto").
2. Em **Seus aplicativos**, clique no ícone **Web (`</>`)**.
3. Registre o app (ex.: `sysfix-web`) e copie o bloco `firebaseConfig`:
   - `apiKey` -> `FIREBASE_WEB_API_KEY`
   - `authDomain` -> `FIREBASE_WEB_AUTH_DOMAIN`
   - `projectId` -> `FIREBASE_WEB_PROJECT_ID`
   - `storageBucket` -> `FIREBASE_WEB_STORAGE_BUCKET`
   - `messagingSenderId` -> `FIREBASE_WEB_MESSAGING_SENDER_ID`
   - `appId` -> `FIREBASE_WEB_APP_ID`

## 5. Gerar a Chave da Conta de Serviço (Backend)

1. Ainda em **Configurações do projeto**, abra a aba **Contas de serviço**.
2. Clique em **Gerar nova chave privada** e baixe o arquivo `.json`.
3. Guarde esse arquivo **fora do Git** (o `.gitignore` já bloqueia `*firebase-adminsdk*.json` e `.env`).

## 6. Configurar o arquivo `.env`

1. Copie [`SysFix/.env.example`](.env.example) para `SysFix/.env`.
2. Preencha as variáveis:

```env
SYSFIX_BACKEND=firebase
FIREBASE_SERVICE_ACCOUNT_PATH=C:\caminho\seguro\sysfix-firebase-adminsdk.json

FIREBASE_WEB_API_KEY=...
FIREBASE_WEB_AUTH_DOMAIN=...
FIREBASE_WEB_PROJECT_ID=...
FIREBASE_WEB_STORAGE_BUCKET=...
FIREBASE_WEB_MESSAGING_SENDER_ID=...
FIREBASE_WEB_APP_ID=...
```

> **Deploy em nuvem (Render, Cloud Run, Railway):** em vez de `FIREBASE_SERVICE_ACCOUNT_PATH`, cole o JSON inteiro em uma linha na variável de ambiente `FIREBASE_SERVICE_ACCOUNT_JSON`.

## 7. Instalar Dependências e Iniciar

```powershell
cd SysFix
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

Acesse `http://localhost:8000` e crie a primeira empresa na aba **Criar Empresa**.

## 8. Migrar Dados Locais Existentes para o Firestore (Opcional)

Se você já possui um `coletas.json` ou o backup exportado do Neon, importe os registros para o Firestore:

```powershell
.\.venv\Scripts\python.exe migrar_para_firebase.py --arquivo "C:\caminho\coletas.json" --tenant-id default --empresa "Minha Empresa"
```
