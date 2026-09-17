/**
 * Upload de vídeos longos/fatiados para o YouTube Studio via Playwright.
 * Usa perfil persistente para evitar limites de cota da API oficial do Google Cloud.
 */

const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

function parseArgs() {
  const args = process.argv.slice(2);
  const options = {
    mode: 'upload',
    video: null,
    title: '',
    description: '',
    playlist: '',
    profile: path.resolve(__dirname, '..', 'dados', 'youtube_perfil'),
    unlisted: true,
    headless: true,
  };

  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (arg === '--login') {
      options.mode = 'login';
      options.headless = false;
    } else if (arg === '--check') {
      options.mode = 'check';
    } else if (arg === '--upload') {
      options.mode = 'upload';
    } else if (arg === '--video' && i + 1 < args.length) {
      options.video = args[++i];
    } else if (arg === '--title' && i + 1 < args.length) {
      options.title = args[++i];
    } else if (arg === '--description' && i + 1 < args.length) {
      options.description = args[++i];
    } else if (arg === '--playlist' && i + 1 < args.length) {
      options.playlist = args[++i];
    } else if (arg === '--profile' && i + 1 < args.length) {
      options.profile = path.resolve(args[++i]);
    } else if (arg === '--headed') {
      options.headless = false;
    }
  }
  return options;
}

async function getBrowserContext(profileDir, headless = true) {
  if (!fs.existsSync(profileDir)) {
    fs.mkdirSync(profileDir, { recursive: true });
  }

  const channels = ['chrome', 'msedge', undefined];
  let lastError = null;

  for (const channel of channels) {
    try {
      const launchOpts = {
        headless: headless,
        args: [
          '--disable-blink-features=AutomationControlled',
          '--no-sandbox',
          '--disable-infobars',
        ],
        viewport: { width: 1280, height: 800 },
      };
      if (channel) launchOpts.channel = channel;

      const ctx = await chromium.launchPersistentContext(profileDir, launchOpts);
      return ctx;
    } catch (err) {
      lastError = err;
    }
  }

  throw new Error(`Nao foi possivel iniciar navegador: ${lastError ? lastError.message : 'desconhecido'}`);
}

async function checkLogin(options) {
  let ctx = null;
  try {
    ctx = await getBrowserContext(options.profile, true);
    const page = await ctx.newPage();
    await page.goto('https://studio.youtube.com', { timeout: 30000, waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(4000);

    const url = page.url();
    const isLogged = !url.includes('accounts.google.com') && url.includes('studio.youtube.com');
    console.log(JSON.stringify({ autenticado: isLogged, url: url }));
  } catch (err) {
    console.log(JSON.stringify({ autenticado: false, erro: err.message }));
  } finally {
    if (ctx) await ctx.close();
  }
}

async function openLogin(options) {
  let ctx = null;
  try {
    ctx = await getBrowserContext(options.profile, false);
    const page = await ctx.newPage();
    await page.goto('https://studio.youtube.com', { timeout: 60000, waitUntil: 'domcontentloaded' });
    console.log(JSON.stringify({ status: 'aguardando_usuario', mensagem: 'Navegador aberto. Faca login e feche o navegador quando terminar.' }));

    // Mantem aberto ate o usuario fechar a janela
    await new Promise((resolve) => {
      ctx.on('close', resolve);
      page.on('close', async () => {
        const pages = ctx.pages();
        if (pages.length === 0) resolve();
      });
    });
    console.log(JSON.stringify({ status: 'concluido', mensagem: 'Sessao salva com sucesso.' }));
  } catch (err) {
    console.log(JSON.stringify({ status: 'erro', erro: err.message }));
  } finally {
    if (ctx) {
      try { await ctx.close(); } catch (_) {}
    }
  }
}

async function uploadVideo(options) {
  if (!options.video || !fs.existsSync(options.video)) {
    console.log(JSON.stringify({ sucesso: false, erro: `Arquivo nao encontrado: ${options.video}` }));
    return;
  }

  let ctx = null;
  try {
    ctx = await getBrowserContext(options.profile, options.headless);
    const page = await ctx.newPage();

    await page.goto('https://studio.youtube.com', { timeout: 45000, waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(4000);

    const currentUrl = page.url();
    if (currentUrl.includes('accounts.google.com')) {
      console.log(JSON.stringify({
        sucesso: false,
        precisa_login: true,
        erro: 'Sessao expirada ou nao autenticada no YouTube Studio. Use a opcao de login antes de enviar.',
      }));
      return;
    }

    // Clica no botao Criar
    const createBtn = page.locator('#create-icon, button[aria-label="Criar"], button[aria-label="Create"]').first();
    await createBtn.waitFor({ state: 'visible', timeout: 15000 });
    await createBtn.click();
    await page.waitForTimeout(1000);

    // Clica em "Enviar videos"
    const uploadItem = page.locator('#text-item-0, tp-yt-paper-item:has-text("Enviar"), tp-yt-paper-item:has-text("Upload")').first();
    await uploadItem.waitFor({ state: 'visible', timeout: 10000 });

    const [fileChooser] = await Promise.all([
      page.waitForEvent('filechooser', { timeout: 15000 }),
      uploadItem.click(),
    ]);

    // Seleciona o arquivo
    await fileChooser.setFiles(options.video);

    // Aguarda o dialogo de detalhes carregar
    await page.waitForSelector('#title-textarea, #textbox[aria-label*="título"], #textbox[aria-label*="Title"]', { timeout: 60000 });
    await page.waitForTimeout(2000);

    // Preenche titulo
    if (options.title) {
      const titleBox = page.locator('#title-textarea #textbox, #textbox[aria-label*="título"], #textbox[aria-label*="Title"]').first();
      await titleBox.fill(options.title.slice(0, 99));
    }

    // Preenche descricao
    if (options.description) {
      const descBox = page.locator('#description-textarea #textbox, #textbox[aria-label*="descrição"], #textbox[aria-label*="Description"]').first();
      await descBox.fill(options.description.slice(0, 4900));
    }

    // Marca "Nao e conteudo para criancas"
    try {
      const notForKids = page.locator('tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"], tp-yt-paper-radio-button[name="NOT_MFK"]').first();
      await notForKids.waitFor({ state: 'visible', timeout: 5000 });
      await notForKids.click();
    } catch (_) {}

    // Avanca os passos (Detalhes -> Elementos do video -> Verificacoes -> Visibilidade)
    for (let step = 0; step < 3; step++) {
      try {
        const nextBtn = page.locator('#next-button, button:has-text("Próximo"), button:has-text("Next")').first();
        if (await nextBtn.isVisible()) {
          await nextBtn.click();
          await page.waitForTimeout(1500);
        }
      } catch (_) {}
    }

    // Seleciona visibilidade "Nao listado" (Unlisted)
    try {
      const unlistedRadio = page.locator('tp-yt-paper-radio-button[name="UNLISTED"]').first();
      await unlistedRadio.waitFor({ state: 'visible', timeout: 10000 });
      await unlistedRadio.click();
    } catch (_) {}

    // Captura o link do video
    let videoUrl = '';
    let videoId = '';
    try {
      const linkElem = page.locator('a.ytcp-video-info, a[href*="youtu.be"]').first();
      if (await linkElem.isVisible({ timeout: 5000 })) {
        videoUrl = (await linkElem.getAttribute('href')) || '';
      }
      if (!videoUrl) {
        const spanLink = page.locator('span.ytcp-video-info').first();
        if (await spanLink.isVisible()) {
          videoUrl = (await spanLink.textContent()) || '';
        }
      }
      if (videoUrl) {
        const match = videoUrl.match(/youtu\.be\/([a-zA-Z0-9_-]+)/);
        if (match) videoId = match[1];
      }
    } catch (_) {}

    // Clica em Salvar / Publicar
    const saveBtn = page.locator('#done-button, button:has-text("Salvar"), button:has-text("Save"), button:has-text("Concluir")').first();
    await saveBtn.waitFor({ state: 'visible', timeout: 10000 });
    await saveBtn.click();

    await page.waitForTimeout(5000);

    console.log(JSON.stringify({
      sucesso: true,
      video_id: videoId || 'gerado',
      video_url: videoUrl || 'https://studio.youtube.com',
      titulo: options.title,
    }));
  } catch (err) {
    console.log(JSON.stringify({ sucesso: false, erro: err.message }));
  } finally {
    if (ctx) {
      try { await ctx.close(); } catch (_) {}
    }
  }
}

async function main() {
  const options = parseArgs();
  if (options.mode === 'check') {
    await checkLogin(options);
  } else if (options.mode === 'login') {
    await openLogin(options);
  } else {
    await uploadVideo(options);
  }
}

main().catch((err) => {
  console.log(JSON.stringify({ sucesso: false, erro: err.message }));
  process.exit(1);
});
