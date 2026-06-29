require('dotenv').config();
const http = require('http');
const { exec } = require('child_process');
const axios = require('axios');
const { salvarNovoToken } = require('./tokenManager');

const APP_ID   = process.env.ML_APP_ID;
const SECRET   = process.env.ML_SECRET;
const PORT     = 3000;
const REDIRECT = `http://localhost:${PORT}/callback`;

if (!APP_ID || !SECRET) {
  console.error('\nErro: defina ML_APP_ID e ML_SECRET no .env antes de rodar.\n');
  process.exit(1);
}

const AUTH_URL =
  `https://auth.mercadolivre.com.br/authorization?response_type=code` +
  `&client_id=${APP_ID}&redirect_uri=${encodeURIComponent(REDIRECT)}`;

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`);

  if (url.pathname !== '/callback') {
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    res.end('<h2>Aguardando autorização do Mercado Livre...</h2>');
    return;
  }

  const code  = url.searchParams.get('code');
  const error = url.searchParams.get('error');

  if (error) {
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    res.end(`<h2 style="color:red">Erro: ${error}</h2>`);
    console.error(`\nErro de autorização: ${error}\n`);
    server.close();
    return;
  }

  if (!code) {
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    res.end('<h2 style="color:orange">Nenhum code recebido.</h2>');
    return;
  }

  console.log(`\nCode recebido. Trocando pelo access token...\n`);

  try {
    const params = new URLSearchParams({
      grant_type:    'authorization_code',
      client_id:     APP_ID,
      client_secret: SECRET,
      code,
      redirect_uri:  REDIRECT,
    });

    const { data } = await axios.post(
      'https://api.mercadolibre.com/oauth/token',
      params.toString(),
      { headers: { 'content-type': 'application/x-www-form-urlencoded' } }
    );

    if (data.access_token) {
      salvarNovoToken(data);

      console.log('═══════════════════════════════════════');
      console.log('  ACCESS TOKEN OBTIDO COM SUCESSO!');
      console.log('═══════════════════════════════════════');
      console.log(`Salvo em .tokens.json — pode rodar: npm start\n`);

      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
      res.end(`
        <div style="font-family:monospace;padding:20px;background:#f0fff0;border:2px solid green;border-radius:8px;max-width:600px;margin:40px auto">
          <h2 style="color:green">Token obtido com sucesso!</h2>
          <p>Pode fechar esta janela e voltar ao terminal. Já pode rodar:</p>
          <pre style="background:#fff;padding:12px;border-radius:4px">npm start</pre>
        </div>
      `);
    } else {
      throw new Error(JSON.stringify(data));
    }
  } catch (err) {
    const detalhe = err.response?.data || err.message;
    console.error('Erro ao trocar token:', detalhe);
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    res.end(`<h2 style="color:red">Erro ao obter token</h2><pre>${JSON.stringify(detalhe, null, 2)}</pre>`);
  } finally {
    server.close();
  }
});

server.listen(PORT, () => {
  console.log('\n═══════════════════════════════════════');
  console.log('  Bot Ofertas ML — Autenticação');
  console.log('═══════════════════════════════════════');
  console.log(`\n1. Confirme que esta Redirect URI está cadastrada no seu app ML:`);
  console.log(`   ${REDIRECT}\n`);
  console.log(`2. Abrindo o navegador para autorização...\n`);

  const cmd = process.platform === 'darwin' ? 'open'
            : process.platform === 'win32'  ? 'start ""'
            : 'xdg-open';
  exec(`${cmd} "${AUTH_URL}"`, () => {});

  console.log(`Se não abrir automaticamente, acesse:\n${AUTH_URL}\n`);
  console.log('Aguardando autorização...\n');
});
