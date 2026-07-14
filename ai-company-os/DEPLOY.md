# Deploy & Google — Company OS

Este guia coloca o site **no ar** e o prepara para o **Google**. A maior parte é
automática (workflow). Só 2 passos exigem **você**, porque dependem da sua conta
Google / das configurações do seu repositório — eu não acesso nenhuma das duas.

## O que já está automatizado (nesta PR)

- **Hospedagem**: GitHub Pages via `.github/workflows/deploy-pages.yml`.
  Publica `ai-company-os/landing/` e **auto-habilita o Pages** na 1ª execução.
- **URL pública**: `https://ruan1989.github.io/diagnostico-ia/`
- **SEO de produção**: canonical, `og:*`, Twitter card, `sitemap.xml`, `robots.txt`,
  `hreflang` PT/EN, favicon, PWA `manifest.webmanifest`, `404.html`.
- **IndexNow**: `scripts/submit-indexnow.mjs` avisa Bing/Yandex/Seznam a cada
  deploy (o Google não usa IndexNow — ver passo 2).

## Passo 1 — Habilitar o Pages (1 toggle, você)

O GitHub Actions já está ativo e o workflow **rodou automaticamente**. Porém o
auto-habilitar do Pages foi bloqueado pelo GitHub por segurança:

```
Create Pages site failed. Error: Resource not accessible by integration
```

Isso é esperado: o token do workflow **não tem permissão para criar o site Pages
na primeira vez** — só o dono do repositório pode ligar isso. É 1 toggle:

1. Repositório → **Settings → Pages → Build and deployment → Source** →
   selecione **"GitHub Actions"**.
2. (Se ainda falhar) **Settings → Actions → General → Workflow permissions** →
   marque **"Read and write permissions"** → Save.
3. Rode o deploy de novo: **Actions → "Deploy Company OS Landing" → Run workflow**
   (ou faça qualquer push que toque `ai-company-os/landing/**`).

Em ~1 min o site estará no ar em `https://ruan1989.github.io/diagnostico-ia/`.
A partir daí, todo push republica sozinho (o token só precisa *criar* o site uma
vez; depois só atualiza).

## Passo 2 — Conectar ao Google (2 cliques, você)

O Google exige provar que o site é seu — isso só pode ser feito logado na **sua**
conta Google. Não há como automatizar de fora.

1. Acesse [Google Search Console](https://search.google.com/search-console) →
   **Adicionar propriedade** → prefixo de URL:
   `https://ruan1989.github.io/diagnostico-ia/`
2. Método de verificação **"Tag HTML"** → copie o token e cole em
   `ai-company-os/landing/index.html`, substituindo
   `COLE-SEU-TOKEN-DO-SEARCH-CONSOLE-AQUI` na meta `google-site-verification`.
   Faça commit → o deploy publica → clique em **Verificar**.
3. Em **Sitemaps**, envie: `https://ruan1989.github.io/diagnostico-ia/sitemap.xml`.
4. (Opcional) **Inspeção de URL** → "Solicitar indexação" para acelerar.

Pronto: o Google passa a rastrear e indexar o site. A indexação leva de horas a
alguns dias (normal, controlado pelo Google).

## Passo 3 — Analytics (opcional)

Em `landing/index.html` há um bloco **Google Analytics 4** comentado. Troque
`G-XXXXXXXXXX` pelo seu Measurement ID e descomente.

## Passo 4 — Domínio próprio (opcional)

Para usar `companyos.ai` em vez do endereço `github.io`:

1. Compre o domínio (Registro.br/Namecheap/Cloudflare).
2. Settings → Pages → **Custom domain** → `companyos.ai`.
3. No DNS do domínio, aponte um `CNAME` para `ruan1989.github.io`.
4. Atualize as URLs absolutas em `index.html`, `sitemap.xml`, `robots.txt` e
   `manifest.webmanifest` para o novo domínio.

## Por que estes passos não são automáticos

- **Verificação no Google**: precisa de login na sua conta Google (segurança do
  Google — ninguém verifica propriedade por você).
- **Habilitar Actions/Pages**: é uma configuração do seu repositório.
- **Forçar indexação**: o Google descontinuou o "ping" de sitemap (2023); o único
  caminho oficial é o Search Console (passo 2).

Tudo o que **podia** ser automatizado, foi.
