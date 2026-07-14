// Submete a URL do site aos motores que suportam IndexNow (Bing, Yandex,
// Seznam, Naver) — sem login. O Google NÃO usa IndexNow (ver DEPLOY.md para o
// caminho do Google via Search Console). Roda após o deploy do Pages.
const HOST = process.env.INDEXNOW_HOST || "ruan1989.github.io";
const KEY = process.env.INDEXNOW_KEY || "companyos-indexnow-key-2026";
const BASE = `https://${HOST}/diagnostico-ia`;

const body = {
  host: HOST,
  key: KEY,
  keyLocation: `${BASE}/${KEY}.txt`,
  urlList: [`${BASE}/`],
};

try {
  const res = await fetch("https://api.indexnow.org/indexnow", {
    method: "POST",
    headers: { "content-type": "application/json; charset=utf-8" },
    body: JSON.stringify(body),
  });
  console.log(`IndexNow: HTTP ${res.status} — ${res.status < 400 ? "aceito" : "verifique a chave/URL"}`);
} catch (err) {
  console.warn("IndexNow falhou (não bloqueante):", err?.message ?? err);
}
