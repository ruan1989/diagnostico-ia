# Análise de Segurança — Company OS

> Avaliação honesta do estado atual, com falhas reais e um plano de correção
> priorizado. Nada aqui é "marketing de segurança" — é o que um pentester diria.

## ✅ O que já está implementado (pontos fortes)

| Controle | Onde |
|----------|------|
| Hash de senha (scrypt + salt) | `apps/api/src/platform/crypto.ts` |
| Criptografia de campo AES-256-GCM (confidencialidade + integridade) | idem |
| Sessão assinada (HMAC) com expiração 24h | `modules/auth/auth.service.ts` |
| RBAC (papéis: owner/admin/manager/member/viewer) | `core/tools/registry.ts` |
| Rate limiting (login/chat/API) anti brute force e flood | `platform/rate-limit.ts` |
| Cabeçalhos de segurança (CSP, HSTS, X-Frame-Options, nosniff) | `http/security.ts` |
| Isolamento multi-tenant (dados por `tenantId`) | `platform/repository.ts` |
| Webhook de pagamento com verificação HMAC (timing-safe) | `billing/billing.service.ts` |
| Idempotência de cobrança (sem duplicidade) | idem |
| Trilha de auditoria + detecção de rajada de login | `platform/audit.ts` |
| Zero armazenamento de dados de cartão (PCI-minded) | `billing/provider.ts` |
| Limite de corpo de request (256KB) | `index.ts` |

## ⚠️ Falhas e limitações reais (estado atual)

### Críticas para produção (corrigir antes de ter dados reais de clientes)
1. **Persistência in-memory / localStorage.** O backend guarda tudo em memória
   (perde no restart) e o site publicado guarda no `localStorage` do navegador.
   → No site estático, **os dados NÃO são privados nem sincronizados**: quem usa
   o mesmo dispositivo vê os dados. Ótimo para demo, inaceitável para dados reais.
2. **Autenticação de demonstração.** Há usuários fixos (`owner@demo.com` / `demo`)
   e o resolver cai num "tenant demo" quando não há token. Em produção: remover
   o fallback, exigir token sempre, e nunca embarcar credenciais.
3. **Segredos via variáveis de ambiente simples** (`JWT_SECRET`, `WEBHOOK_SECRET`).
   Precisam vir de um cofre (AWS Secrets Manager/Vault) com rotação.
4. **Sem verificação de e-mail / 2FA.** Signup aceita qualquer e-mail sem confirmar.

### Médias
5. **Token guardado no cliente.** Em produção use cookie `HttpOnly` + `SameSite`
   em vez de `localStorage` (mitiga XSS roubando o token).
6. **Rate limit em memória** não funciona entre instâncias — trocar por Redis.
7. **CORS liberado** (`origin: true`) — restringir aos domínios do produto.
8. **Sem validação de schema forte** nos corpos (hoje é _casting_). Adotar Zod.
9. **Pagamentos são mock.** A segurança real (3-D Secure, antifraude, tokenização)
   vem do provedor (Stripe/Adyen) quando integrado — hoje não há cobrança real.

### Baixas / higiene
10. Auditoria in-memory (não é WORM) — persistir append-only.
11. Sem CSP com nonce para scripts (a landing é estática, risco baixo).
12. Dependências: rodar `npm audit` no CI e Dependabot.

## 🎯 Plano de correção priorizado

**Fase 1 (antes de qualquer cliente real):**
- Postgres com Row-Level Security por `tenantId`
- Auth real: JWT + refresh em cookie HttpOnly, verificação de e-mail, 2FA (TOTP)
- Segredos em cofre + rotação; remover usuários/fallback de demo
- Validação com Zod em todas as rotas

**Fase 2:**
- Redis para rate limit e sessões
- WAF/Cloudflare na borda; CORS restrito; CSP com nonce
- Stripe/Adyen para pagamento (tokenização, 3-D Secure, antifraude)
- Criptografia de campos sensíveis no banco (PII) com KMS

**Fase 3:**
- SIEM/observabilidade de segurança, alertas de anomalia
- Pentest externo + bug bounty
- Conformidade LGPD/GDPR (DPA, direito ao esquecimento, logs de consentimento)
- Backup criptografado + DR testado

## Resumo executivo

O **desenho** de segurança está correto e as defesas certas já existem no código
(hash, criptografia, RBAC, rate limit, auditoria, webhook assinado). O que falta é
**infraestrutura de produção**: banco real com isolamento, auth robusta, segredos
em cofre e um provedor de pagamento real. Enquanto for demonstração client-side,
trate os dados como **públicos no dispositivo** — não coloque informação sensível
real até a Fase 1 estar concluída.
