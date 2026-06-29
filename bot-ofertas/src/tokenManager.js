const axios = require('axios');
const fs    = require('fs');
const path  = require('path');

const TOKEN_FILE = path.join(__dirname, '..', '.tokens.json');

function lerTokens() {
  if (!fs.existsSync(TOKEN_FILE)) return {};
  try { return JSON.parse(fs.readFileSync(TOKEN_FILE, 'utf8')); }
  catch { return {}; }
}

function salvarTokens(tokens) {
  fs.writeFileSync(TOKEN_FILE, JSON.stringify(tokens, null, 2));
}

async function renovarToken(refreshToken) {
  const { data } = await axios.post(
    'https://api.mercadolibre.com/oauth/token',
    new URLSearchParams({
      grant_type:    'refresh_token',
      client_id:     process.env.ML_APP_ID,
      client_secret: process.env.ML_SECRET,
      refresh_token: refreshToken,
    }).toString(),
    { headers: { 'content-type': 'application/x-www-form-urlencoded' } }
  );
  return data;
}

async function getToken() {
  // Prioridade: env var > arquivo de tokens
  if (process.env.ML_ACCESS_TOKEN) {
    return process.env.ML_ACCESS_TOKEN;
  }

  const tokens = lerTokens();

  if (!tokens.access_token) {
    throw new Error('Token ML não encontrado. Rode: node src/auth.js');
  }

  // Se expirar em menos de 10 min, renova
  const agora = Date.now();
  if (tokens.expires_at && agora > tokens.expires_at - 600_000) {
    console.log('[Token] Renovando access token...');
    try {
      const novo = await renovarToken(tokens.refresh_token);
      const updated = {
        access_token:  novo.access_token,
        refresh_token: novo.refresh_token || tokens.refresh_token,
        expires_at:    agora + (novo.expires_in * 1000),
      };
      salvarTokens(updated);
      return updated.access_token;
    } catch (err) {
      console.error('[Token] Falha ao renovar:', err.message);
    }
  }

  return tokens.access_token;
}

function salvarNovoToken(data) {
  const tokens = {
    access_token:  data.access_token,
    refresh_token: data.refresh_token,
    expires_at:    Date.now() + (data.expires_in * 1000),
    user_id:       data.user_id,
  };
  salvarTokens(tokens);
  console.log('[Token] Salvo com sucesso.');
  return tokens;
}

module.exports = { getToken, salvarNovoToken };
