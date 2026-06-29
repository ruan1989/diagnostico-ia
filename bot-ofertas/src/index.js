require('dotenv').config();
const cron = require('node-cron');
const { buscarOfertas }  = require('./mlOffers');
const { enviarOfertas }  = require('./telegram');

const MAX_OFERTAS = parseInt(process.env.MAX_OFERTAS_POR_CICLO || '5');

async function ciclo() {
  console.log(`\n[${new Date().toLocaleString('pt-BR')}] Iniciando ciclo...`);
  try {
    const ofertas = await buscarOfertas();
    console.log(`[ML] ${ofertas.length} ofertas encontradas.`);

    const top = ofertas.slice(0, MAX_OFERTAS);
    if (top.length === 0) {
      console.log('[Bot] Nenhuma oferta acima do mínimo. Aguardando próximo ciclo.');
      return;
    }

    await enviarOfertas(top);
  } catch (err) {
    console.error('[Bot] Erro no ciclo:', err.message);
  }
}

// Roda imediatamente ao iniciar
ciclo();

// Cron: a cada 45 minutos, das 7h às 23h (horário de Brasília)
cron.schedule('*/45 7-23 * * *', ciclo, {
  timezone: 'America/Sao_Paulo',
});

console.log('[Bot] Agendamento ativo — a cada 45 min, das 07h às 23h (Brasília).');
