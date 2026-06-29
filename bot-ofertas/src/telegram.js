const axios = require('axios');

const TOKEN    = process.env.TELEGRAM_BOT_TOKEN;
const CHAT_ID  = process.env.TELEGRAM_CHAT_ID;
const BASE_URL = `https://api.telegram.org/bot${TOKEN}`;

function formatarMoeda(valor) {
  return valor.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
}

function formatarMensagem(oferta) {
  const economia = formatarMoeda(oferta.precoOriginal - oferta.precoAtual);
  return (
    `🔥 *${oferta.titulo}*\n\n` +
    `~~${formatarMoeda(oferta.precoOriginal)}~~ → *${formatarMoeda(oferta.precoAtual)}*\n` +
    `💥 *${oferta.desconto}% OFF* — economia de ${economia}\n\n` +
    `⚡ [Comprar agora](${oferta.link})`
  );
}

async function enviarFoto(oferta) {
  const texto = formatarMensagem(oferta);

  // Tenta sendPhoto com a URL da imagem
  try {
    const { data } = await axios.post(`${BASE_URL}/sendPhoto`, {
      chat_id:    CHAT_ID,
      photo:      oferta.imageUrl,
      caption:    texto,
      parse_mode: 'Markdown',
      reply_markup: {
        inline_keyboard: [[
          { text: '🛒 Ver oferta', url: oferta.link },
        ]],
      },
    });

    if (data.ok) {
      console.log(`[TG] Foto enviada: ${oferta.titulo.substring(0, 40)}`);
      return true;
    }
  } catch (err) {
    console.warn(`[TG] sendPhoto falhou (${err.response?.data?.description || err.message}). Tentando sendMessage...`);
  }

  // Fallback: envia só texto se a foto falhar
  try {
    await axios.post(`${BASE_URL}/sendMessage`, {
      chat_id:    CHAT_ID,
      text:       texto,
      parse_mode: 'Markdown',
      disable_web_page_preview: false, // mostra preview com imagem
      reply_markup: {
        inline_keyboard: [[
          { text: '🛒 Ver oferta', url: oferta.link },
        ]],
      },
    });
    console.log(`[TG] Mensagem (sem foto) enviada: ${oferta.titulo.substring(0, 40)}`);
    return true;
  } catch (err2) {
    console.error(`[TG] Erro ao enviar:`, err2.response?.data || err2.message);
    return false;
  }
}

async function enviarOfertas(ofertas) {
  let enviados = 0;
  for (const oferta of ofertas) {
    const ok = await enviarFoto(oferta);
    if (ok) enviados++;
    // Pausa 2s entre envios para não bater no rate limit do Telegram
    await new Promise(r => setTimeout(r, 2000));
  }
  console.log(`[TG] ${enviados}/${ofertas.length} ofertas enviadas.`);
  return enviados;
}

module.exports = { enviarOfertas };
