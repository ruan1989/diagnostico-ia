const axios = require('axios');
const { getToken } = require('./tokenManager');

const AFFILIATE_ID = process.env.ML_AFFILIATE_ID || 'matt_tool=47114387';
const MIN_DISCOUNT = parseInt(process.env.MIN_DISCOUNT || '15');
const MIN_SCORE    = parseFloat(process.env.MIN_SCORE   || '3.5');

const CATEGORIAS = [
  'MLB1000',  // Eletrônicos
  'MLB1276',  // Esporte e Lazer
  'MLB1430',  // Beleza e Cuidado Pessoal
  'MLB1182',  // Moda
  'MLB1574',  // Casa e Jardim
  'MLB3937',  // Bebês
];

// Converte thumbnail para imagem grande (troca -I.jpg por -O.jpg)
function getImageUrl(item) {
  if (item.pictures && item.pictures.length > 0) {
    return item.pictures[0].url;
  }
  const thumb = item.thumbnail || item.secure_thumbnail || '';
  return thumb
    .replace('-I.jpg', '-O.jpg')
    .replace('-I.webp', '-O.jpg')
    .replace('http://', 'https://');
}

// Busca detalhes completos do item (inclui pictures de alta resolução)
async function getItemDetails(itemId, token) {
  try {
    const { data } = await axios.get(
      `https://api.mercadolibre.com/items/${itemId}`,
      { headers: { Authorization: `Bearer ${token}` } }
    );
    return data;
  } catch {
    return null;
  }
}

function calcDesconto(original, atual) {
  if (!original || original <= atual) return 0;
  return Math.round(((original - atual) / original) * 100);
}

function gerarLinkAfiliado(permalink) {
  const url = new URL(permalink);
  url.searchParams.set('matt_tool', AFFILIATE_ID.replace('matt_tool=', ''));
  return url.toString();
}

async function buscarOfertas() {
  const token = await getToken();
  const ofertas = [];

  for (const categoria of CATEGORIAS) {
    try {
      const { data } = await axios.get('https://api.mercadolibre.com/sites/MLB/search', {
        headers: { Authorization: `Bearer ${token}` },
        params: {
          category: categoria,
          sort: 'price_asc',
          promotions: 'DEAL_OF_THE_DAY,LIGHTNING_DEAL',
          limit: 20,
        },
      });

      const items = data.results || [];

      for (const item of items) {
        const precoOriginal = item.original_price;
        const precoAtual    = item.price;
        const desconto      = calcDesconto(precoOriginal, precoAtual);
        const reputacao     = item.seller?.seller_reputation?.transactions?.ratings?.positive || 0;

        if (desconto < MIN_DISCOUNT) continue;
        if (reputacao < MIN_SCORE && reputacao > 0) continue;

        // Busca detalhes para imagem de alta resolução
        let imageUrl = getImageUrl(item);
        if (!item.pictures) {
          const detalhes = await getItemDetails(item.id, token);
          if (detalhes) {
            imageUrl = getImageUrl(detalhes);
          }
        }

        ofertas.push({
          id:            item.id,
          titulo:        item.title,
          precoOriginal: precoOriginal || precoAtual,
          precoAtual,
          desconto,
          imageUrl,
          link:          gerarLinkAfiliado(item.permalink),
          categoria,
          reputacao,
          score:         calcScore({ desconto, reputacao }),
        });
      }
    } catch (err) {
      console.error(`[ML] Erro categoria ${categoria}:`, err.message);
    }
  }

  // Remove duplicatas por ID, mantém maior score
  const seen = new Map();
  for (const o of ofertas) {
    if (!seen.has(o.id) || seen.get(o.id).score < o.score) {
      seen.set(o.id, o);
    }
  }

  return [...seen.values()].sort((a, b) => b.score - a.score);
}

function calcScore({ desconto, reputacao }) {
  const pDesc = Math.min(desconto / 80, 1) * 40;
  const pRep  = Math.min(reputacao / 5, 1) * 30;
  return Math.round(pDesc + pRep);
}

module.exports = { buscarOfertas };
