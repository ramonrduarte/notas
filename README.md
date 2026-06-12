# SEFAZ Downloader — NF-e / CT-e

Sistema web para download automático de XMLs de NF-e e CT-e via Web Services oficiais da SEFAZ, usando certificado digital A1.

## Como funciona

- Conecta-se ao **NF-e DistDFe** e **CT-e DistDFe** (serviços nacionais da SEFAZ)
- Autentica com seu certificado A1 (`.pfx` / `.p12`) via mTLS
- Baixa todos os documentos novos desde a última sincronização (controle por NSU)
- Executa automaticamente todo dia no horário configurado
- Interface web para configuração, sincronização manual e download dos XMLs

---

## Instalação via Portainer (recomendado)

### Pré-requisitos

- Docker + Portainer instalados no servidor
- Porta `8000` aberta no firewall do servidor
- Conta no GitHub com acesso ao repositório

### Passo 1 — Abrir o Portainer

Acesse o Portainer no seu servidor (normalmente `http://ip-do-servidor:9000`).

### Passo 2 — Criar uma Stack a partir do GitHub

1. No menu lateral, clique em **Stacks**
2. Clique em **+ Add Stack**
3. Dê o nome: `sefaz-downloader`
4. Em **Build method**, selecione **Repository**

### Passo 3 — Configurar o repositório

Preencha os campos:

| Campo | Valor |
|---|---|
| Repository URL | `https://github.com/ramonrduarte/notas` |
| Repository reference | `refs/heads/main` |
| Compose path | `docker-compose.yml` |
| Authentication | Desativado (repositório público) |

### Passo 4 — Configurar variáveis de ambiente (opcional)

Se quiser personalizar a porta, clique em **Add an environment variable**:

| Nome | Valor padrão |
|---|---|
| `TZ` | `America/Sao_Paulo` |

### Passo 5 — Deploy

Clique em **Deploy the stack** e aguarde o Portainer baixar o repositório, fazer o build da imagem e iniciar o container.

> O build leva cerca de 1-2 minutos na primeira vez.

### Passo 6 — Verificar

Após o deploy, acesse:

```
http://ip-do-servidor:8000
```

---

## Configuração inicial (primeiro acesso)

Acesse a interface web e vá em **Configurações**:

1. **Certificado Digital**: faça upload do arquivo `.pfx` ou `.p12` e informe a senha
2. **CNPJ**: informe o CNPJ da empresa (somente números)
3. **Ambiente**: selecione **Produção**
4. **UF**: selecione o código do estado (RS = 43)
5. **Agendamento**: defina o horário do sync automático (ex: `07:00`)
6. Clique em **Salvar Configurações**

### Primeira sincronização

Na tela **Dashboard**, clique em **Sincronizar Tudo**.  
Na primeira execução, o sistema baixa todos os documentos dos últimos **90 dias** — pode levar alguns minutos dependendo do volume.

---

## Estrutura dos dados

Os XMLs ficam salvos no volume `./data/` do servidor:

```
data/
├── cert/              → certificado .pfx
├── xmls/
│   ├── nfe/
│   │   └── 2026/06/  → XMLs de NF-e organizados por ano/mês
│   └── cte/
│       └── 2026/06/  → XMLs de CT-e organizados por ano/mês
└── sefaz.db           → banco SQLite (NSU, histórico, metadados)
```

---

## Atualização do sistema

Para atualizar após uma nova versão no GitHub:

1. No Portainer, vá em **Stacks** → `sefaz-downloader`
2. Clique em **Pull and redeploy**
3. Confirme — o container é reiniciado com a nova versão (dados preservados)

---

## Segurança

> **Atenção**: A interface web não possui autenticação por padrão.  
> Se o servidor estiver exposto à internet, coloque o sistema atrás de um **reverse proxy com HTTPS e autenticação** (ex: Nginx Proxy Manager + senha básica, ou Traefik).

O certificado `.pfx` fica armazenado no volume do servidor e nunca é exposto via API.

---

## Teste local (sem servidor)

```bash
# Com Docker
docker compose up

# Sem Docker (Python 3.12+)
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Acesse: `http://localhost:8000`

---

## Serviços SEFAZ utilizados

| Serviço | URL |
|---|---|
| NF-e DistDFe (Produção) | `https://www.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx` |
| CT-e DistDFe (Produção) | `https://www.cte.fazenda.gov.br/CTeDistribuicaoDFe/CTeDistribuicaoDFe.asmx` |
